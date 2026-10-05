"""Contact lookup: matching, link extraction, Instagram checks, confidence,
outreach method, rechecks. No network: every lookup is a fake. All venues,
handles, phones and domains are invented (.test domains, 555-01xx phones)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from licmon import cli, contact
from licmon.contact import Channel, SiteLinks
from licmon.models import Snapshot

TODAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 16, tzinfo=timezone.utc)
SITE = "https://zebrafake.test/"


def venue(**kw):
    base = {"venue_key": "TX|78701|100 FAKE ST", "business_name": "Zebra Fake Lounge",
            "company": "Zebra Fake Holdings LLC", "address": "100 Fake St",
            "city": "Austin", "state": "TX", "zip": "78701", "priority": "A",
            "lead_score": 80, "hot": "Hot", "phone": None}
    base.update(kw)
    return base


def place(name="Zebra Fake Lounge", number="100", route="Fake Street", zip_code="78701",
          city="Austin", phone="(512) 555-0142", website=SITE, status="OPERATIONAL",
          sub=None):
    comps = [{"longText": number, "shortText": number, "types": ["street_number"]},
             {"longText": route, "shortText": route, "types": ["route"]},
             {"longText": city, "shortText": city, "types": ["locality", "political"]},
             {"longText": "Fakeville", "types": ["neighborhood", "political"]},
             {"longText": zip_code, "shortText": zip_code, "types": ["postal_code"]}]
    if sub:
        comps.append({"longText": sub, "shortText": sub, "types": ["subpremise"]})
    out = {"id": "fake-place-1", "displayName": {"text": name},
           "formattedAddress": f"{number} {route}, {city}, TX {zip_code}, USA",
           "addressComponents": comps, "googleMapsUri": "https://maps.example.test/p1",
           "businessStatus": status}
    if phone:
        out["nationalPhoneNumber"] = phone
    if website:
        out["websiteUri"] = website
    return out


class FakePlaces:
    def __init__(self, results=None, error=None):
        self.results = results if results is not None else []
        self.error = error
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.results(query) if callable(self.results) else self.results


class FakeWebsite:
    def __init__(self, links=None):
        self.links_found = links
        self.urls = []

    def links(self, url):
        self.urls.append(url)
        return self.links_found


class FakeInstagram:
    def __init__(self, profiles):
        self.profiles = profiles
        self.asked = []

    def discover(self, handle):
        self.asked.append(handle)
        found = self.profiles.get(handle)
        if isinstance(found, Exception):
            raise found
        return found


def site_links(**kw):
    base = {"instagram": ["zebrafakelounge"],
            "facebook": ["https://www.facebook.com/zebrafakelounge"],
            "emails": ["hello@zebrafake.test"], "phones": ["5125550142"]}
    base.update(kw)
    return SiteLinks(**base)


def profile(**kw):
    base = {"username": "zebrafakelounge", "name": "Zebra Fake Lounge",
            "biography": "Cocktails and DJs. 100 Fake St, Austin", "website": SITE,
            "media": {"data": [{"timestamp": "2026-09-20T22:00:00+0000"}]}}
    base.update(kw)
    return base


def by_kind(result):
    return {ch.kind: ch for ch in result.channels}


# --- address and name matching ---

def test_address_match_uses_venue_history_rules():
    row = venue()
    good = place()
    assert contact.match_place(row, [place(number="102"), good]) is good
    assert contact.match_place(row, [place(zip_code="78702")]) is None
    assert contact.match_place(row, [place(route="Fake Avenue")]) is None
    # A suite on one side only matches (Google often drops it); two
    # different suites never do. Floors are ignored.
    assert contact.match_place(venue(address="100 Fake St Ste 5"), [place(sub="5")])
    assert contact.match_place(venue(address="100 Fake St Ste 5"), [place()])
    assert contact.match_place(row, [place(sub="#7")])
    assert contact.match_place(venue(address="100 Fake St Ste 5"), [place(sub="12")]) is None
    assert contact.match_place(venue(address="100 Fake St Fl 2"), [place()])
    # No street number on the listing: nothing to match on.
    assert contact.match_place(row, [place(number="")]) is None


def test_names_match_ignores_generic_words_and_entities():
    assert contact.names_match("Zebra Fake Lounge", "The Zebra Fake")
    assert contact.names_match("ZEBRA FAKE HOLDINGS LLC", "Zebra Fake Bar")
    assert not contact.names_match("Zebra Fake Lounge", "Old Quokka Saloon")
    assert not contact.names_match("Zebra Lounge", "Quokka Lounge")  # only a generic word
    assert not contact.names_match(None, "Zebra")


def test_search_query_has_name_address_city_state():
    q = contact.search_query(venue())
    assert q == "Zebra Fake Lounge 100 Fake St, Austin, TX 78701"


# --- website links ---

JUNK_PAGE = """
<html><body>
<a href="https://www.instagram.com/zebrafakelounge/">IG</a>
<a href="https://instagram.com/ZebraFakeLounge?igsh=abc">IG again, other case</a>
<a href="https://www.instagram.com/p/AbC123/">a post</a>
<a href="https://www.instagram.com/reel/XyZ/">a reel</a>
<a href="https://www.instagram.com/explore/tags/fake/">explore</a>
<a href="https://www.instagram.com/instagram/">instagram itself</a>
<a href="https://www.instagram.com/accounts/login/">login</a>
<a href="https://instagram.com/_u/zebrafakekitchen">deep link</a>
<a href="https://www.facebook.com/zebrafakelounge/">FB page</a>
<a href="https://www.facebook.com/sharer/sharer.php?u=https://zebrafake.test">share</a>
<a href="https://www.facebook.com/sharer.php?u=x">share 2</a>
<a href="https://www.facebook.com/tr?id=123&ev=PageView">pixel</a>
<a href="https://www.facebook.com/plugins/page.php?href=x">plugin</a>
<a href="https://www.facebook.com/groups/fakegroup/">group</a>
<a href="https://www.facebook.com/events/12345/">event</a>
<a href="https://www.facebook.com/facebook">facebook itself</a>
<a href="https://www.facebook.com/profile.php?id=10001234">numeric page</a>
<a href="mailto:Hello@ZebraFake.test?subject=Booking">email</a>
<a href="mailto:someone@example.com">placeholder</a>
<a href="mailto:not-an-email">broken</a>
<a href="tel:+1-512-555-0142">call</a>
<a href="tel:911">short</a>
<a href="/about">relative</a>
</body></html>
"""


def test_extract_links_keeps_real_accounts_and_drops_junk():
    links = contact.extract_links(JUNK_PAGE)
    assert links.instagram == ["zebrafakelounge", "zebrafakekitchen"]
    assert links.facebook == ["https://www.facebook.com/zebrafakelounge",
                              "https://www.facebook.com/profile.php?id=10001234"]
    assert links.emails == ["hello@zebrafake.test"]
    assert links.phones == ["5125550142"]


def test_extract_links_survives_garbage():
    assert contact.extract_links("<a href='https://instagram.com/") == SiteLinks()
    assert contact.extract_links("") == SiteLinks()


def test_website_fetch_failures_are_no_data():
    class Boom:
        def get(self, url):
            raise RuntimeError("down")

    class Pdf:
        def get(self, url):
            return Snapshot(url=url, body=b"%PDF-1.4", content_type="application/pdf",
                            fetched_at=NOW)

    class Page:
        def get(self, url):
            return Snapshot(url=url, body=JUNK_PAGE.encode(),
                            content_type="text/html; charset=utf-8", fetched_at=NOW)

    assert contact.Website(Boom()).links(SITE) is None
    assert contact.Website(Pdf()).links(SITE) is None
    assert contact.Website(Page()).links(SITE).instagram[0] == "zebrafakelounge"


# --- one venue: confidence, Instagram checks, outreach ---

def test_everything_verified_with_recent_instagram_post():
    ig = FakeInstagram({"zebrafakelounge": profile()})
    result = contact.check_venue(venue(), FakePlaces([place()]), FakeWebsite(site_links()),
                                 ig, TODAY)
    ch = by_kind(result)
    assert (ch["phone"].score, ch["phone"].label) == (75, "Verified")  # 35+20+10+10
    assert (ch["website"].score, ch["website"].label) == (65, "Likely")
    assert (ch["email"].score, ch["email"].label) == (75, "Verified")
    assert (ch["facebook"].score, ch["facebook"].label) == (65, "Likely")
    assert (ch["instagram"].score, ch["instagram"].label) == (100, "Verified")
    assert set(ch["instagram"].signals) >= {"linked_from_website", "ig_links_back",
                                            "ig_bio_address", "ig_bio_city"}
    assert ch["instagram"].last_post == "2026-09-20"
    assert result.reachable
    assert (result.method, result.second) == ("Instagram DM", "Call")
    assert result.best.value == "@zebrafakelounge"
    assert (result.score, result.label) == (100, "Verified")
    assert result.reason.startswith("Google listing at the same address, same name; "
                                    "the website links this Instagram")
    assert ig.asked == ["zebrafakelounge"]


def test_instagram_website_match_alone_adds_points():
    ig = FakeInstagram({"zebrafakelounge": profile(biography="Cocktails and DJs")})
    result = contact.check_venue(venue(), FakePlaces([place()]),
                                 FakeWebsite(site_links()), ig, TODAY)
    insta = by_kind(result)["instagram"]
    assert "ig_links_back" in insta.signals and "ig_bio_address" not in insta.signals
    assert insta.score == 90


def test_instagram_personal_account_is_found_but_unverifiable():
    ig = FakeInstagram({"zebrafakelounge": None})  # the API cannot read it
    result = contact.check_venue(venue(), FakePlaces([place()]),
                                 FakeWebsite(site_links(phones=[], facebook=[], emails=[])),
                                 ig, TODAY)
    insta = by_kind(result)["instagram"]
    assert "ig_unreadable" in insta.signals
    assert (insta.score, insta.label) == (60, "Likely")
    assert insta.last_post is None
    # Phone 65 Likely and open: Call first; Instagram has no recent post seen.
    assert (result.method, result.second) == ("Call", "Instagram DM (no recent posts seen)")


def test_instagram_token_failure_is_reported_and_stops_instagram():
    bad = contact.ContactError("instagram", "HTTP 400 code 190", fatal=True)
    ig = FakeInstagram({"zebrafakelounge": bad, "zebrafakekitchen": profile()})
    links = site_links(instagram=["zebrafakelounge", "zebrafakekitchen"])
    result = contact.check_venue(venue(), FakePlaces([place()]), FakeWebsite(links), ig,
                                 TODAY)
    assert result.lookups_failed == ["instagram failed (HTTP 400 code 190)"]
    assert ig.asked == ["zebrafakelounge"]  # not asked again after a bad token
    handles = [c for c in result.channels if c.kind == "instagram"]
    assert all(c.label == "Verified" and "ig_unreadable" not in c.signals for c in handles)


def test_listing_under_another_name_stays_unverified_unless_bio_names_the_address():
    other = place(name="Old Quokka Saloon")
    result = contact.check_venue(venue(), FakePlaces([other]),
                                 FakeWebsite(site_links(emails=[], facebook=[])), None, TODAY)
    ch = by_kind(result)
    assert all(c.score <= 49 for c in result.channels)
    assert ch["phone"].label == "Unverified" and not result.reachable
    assert result.method == contact.WAIT_METHOD
    assert "under another name (may be the old business)" in result.reason

    ig = FakeInstagram({"zebrafakelounge": profile(website="https://linktree.test/zfl",
                                                   media={"data": []})})
    result = contact.check_venue(venue(), FakePlaces([other]),
                                 FakeWebsite(site_links(emails=[], facebook=[])), ig, TODAY)
    insta = by_kind(result)["instagram"]
    assert "ig_bio_address" in insta.signals and "ig_links_back" not in insta.signals
    assert (insta.score, insta.label) == (75, "Verified")  # 35 + 20 + 15 + 5
    assert result.method == "Instagram DM (no recent posts seen)"


def test_filing_phone_alone_never_makes_a_venue_reachable():
    result = contact.check_venue(venue(phone="(512) 555-0199"), FakePlaces([]), None, None,
                                 TODAY)
    [ch] = result.channels
    assert ch.kind == "filing_phone" and ch.value == "(512) 555-0199"
    assert (ch.score, ch.label) == (15, "Unverified")
    assert not result.reachable and result.method == contact.WAIT_METHOD
    assert result.reason == "Phone from the license filing (may be a lawyer)"
    assert contact.KIND_TEXT["filing_phone"] == "Filing phone (may be a lawyer)"


def test_nothing_found_is_none():
    result = contact.check_venue(venue(), FakePlaces([place(number="999")]), None, None,
                                 TODAY)
    assert result.channels == [] and (result.score, result.label) == (0, "None")
    assert result.reason == "Nothing found yet" and result.method == contact.WAIT_METHOD


def test_closed_for_good_scores_zero():
    result = contact.check_venue(venue(), FakePlaces([place(status="CLOSED_PERMANENTLY")]),
                                 FakeWebsite(site_links()), None, TODAY)
    assert all(c.score == 0 and c.label == "None" for c in result.channels)
    assert not result.reachable


def test_outreach_rules_in_order():
    def run(links, status="OPERATIONAL", phone="(512) 555-0142", ig=None):
        return contact.check_venue(venue(), FakePlaces([place(status=status, phone=phone)]),
                                   FakeWebsite(links), ig, TODAY)

    stale = FakeInstagram({"zebrafakelounge": profile(
        media={"data": [{"timestamp": "2026-06-01T00:00:00+0000"}]})})
    r = run(site_links(), ig=stale)  # stale Instagram: Call, then Facebook
    assert (r.method, r.second) == ("Call", "Facebook message")
    r = run(site_links(facebook=[]), phone=None)
    assert (r.method, r.second) == ("Email", "Instagram DM (no recent posts seen)")
    r = run(site_links(instagram=[], facebook=[], emails=[]), phone=None)
    assert (r.method, r.second) == ("Website contact form", None)
    r = run(site_links(instagram=[], facebook=[], emails=[], phones=[]),
            status="CLOSED_TEMPORARILY")
    assert (r.method, r.second) == ("Website contact form",
                                    "Call (Google does not say it is open)")
    r = run(None, status="CLOSED_TEMPORARILY")  # website did not load: 55 Likely
    assert r.method == "Website contact form"
    assert [m for m, *_ in contact.METHOD_RULES][:4] == [
        "Instagram DM", "Call", "Facebook message", "Email"]


def test_labels_and_caps():
    assert [contact.label_for(s) for s in (100, 75, 74, 50, 49, 1, 0)] == [
        "Verified", "Verified", "Likely", "Likely", "Unverified", "Unverified", "None"]
    fb = contact.score_channel(Channel("facebook", "x", signals=[
        "listing_address", "listing_name", "facebook_linked", "linked_from_website"]))
    assert fb.score == 74  # Facebook tops out at Likely
    assert not contact.reachable(contact.score_channel(
        Channel("filing_phone", "x", signals=["filing_phone"] * 9)))


# --- API clients (fake sessions; the key never appears in errors) ---

class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, json=None, params=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "params": params,
                           "headers": headers})
        return self.responses.pop(0)


def test_places_request_shape_and_error_hides_key():
    session = FakeSession(FakeResp(200, {"places": [place()]}))
    places = contact.Places(key="secret-places-key",
                            api=contact.Api(session, sleep=lambda s: None))
    assert places.search("Zebra Fake Lounge 100 Fake St")[0]["id"] == "fake-place-1"
    call = session.calls[0]
    assert call["method"] == "POST" and call["url"] == contact.PLACES_URL
    assert call["json"] == {"textQuery": "Zebra Fake Lounge 100 Fake St", "pageSize": 5,
                            "regionCode": "US"}
    assert call["headers"]["X-Goog-FieldMask"] == (
        "places.id,places.displayName,places.formattedAddress,places.addressComponents,"
        "places.nationalPhoneNumber,places.websiteUri,places.googleMapsUri,"
        "places.businessStatus")
    assert call["headers"]["X-Goog-Api-Key"] == "secret-places-key"

    places.api.session = FakeSession(FakeResp(403, {"error": {"message": "Zebra"}}))
    with pytest.raises(contact.ContactError) as err:
        places.search("q")
    assert str(err.value) == "places failed (HTTP 403)" and err.value.fatal
    places.api.session = FakeSession(FakeResp(503), FakeResp(503), FakeResp(200, {}))
    assert places.search("q") == []  # retried, then no results


def test_instagram_client_paths():
    ok = FakeSession(FakeResp(200, {"business_discovery": profile()}))
    ig = contact.Instagram(token="secret-token", account_id="17840000000000000",
                           api=contact.Api(ok, sleep=lambda s: None))
    assert ig.discover("zebrafakelounge")["username"] == "zebrafakelounge"
    call = ok.calls[0]
    assert call["url"] == "https://graph.facebook.com/v21.0/17840000000000000"
    assert call["params"]["fields"].startswith(
        "business_discovery.username(zebrafakelounge){username,name,biography,website,")
    assert call["params"]["fields"].endswith("media.limit(1){timestamp}}")

    ig.api.session = FakeSession(FakeResp(400, {"error": {"code": 110,
                                                          "error_subcode": 2207013}}))
    assert ig.discover("zebrafakeperson") is None  # personal account: unverifiable
    ig.api.session = FakeSession(FakeResp(400, {"error": {"code": 190, "message": "x"}}))
    with pytest.raises(contact.ContactError) as err:
        ig.discover("zebrafakelounge")
    assert str(err.value) == "instagram failed (HTTP 400 code 190)" and err.value.fatal
    assert "secret-token" not in str(err.value)


# --- rechecks ---

def test_next_state_first_check_reachable_today_is_not_newly_reachable():
    s = contact.next_state(None, True, NOW, from_today=True)
    assert (s.status, s.next_check_at, s.newly_reachable_on) == ("reachable", None, None)
    assert s.became_reachable_at == NOW


def test_next_state_waiting_reschedules_weekly_then_gives_up_at_120_days():
    s = contact.next_state(None, False, NOW, from_today=True)
    assert s.status == "waiting" and s.next_check_at == NOW + timedelta(days=7)
    prev = {"status": "waiting", "first_checked_at": NOW}
    later = NOW + timedelta(days=119)
    s = contact.next_state(prev, False, later, from_today=False)
    assert s.status == "waiting" and s.first_checked_at == NOW
    s = contact.next_state(prev, False, NOW + timedelta(days=120), from_today=False)
    assert (s.status, s.next_check_at) == ("gave_up", None)
    assert s.gave_up_at == NOW + timedelta(days=120)


def test_next_state_waiting_to_reachable_is_newly_reachable():
    prev = {"status": "waiting", "first_checked_at": NOW}
    later = NOW + timedelta(days=14)
    s = contact.next_state(prev, True, later, from_today=False)
    assert s.status == "reachable" and s.newly_reachable_on == later.date()
    # A first check after the queue day (over a past day's cap) counts too.
    s = contact.next_state(None, True, later, from_today=False)
    assert s.newly_reachable_on == later.date()


def test_plan_order_cap_and_attio_eligibility():
    today = [venue(venue_key="t1", lead_score=90),
             venue(venue_key="t2", business_name="Zebra Fake Two"),
             venue(venue_key="adult", adult=True),
             venue(venue_key="weak-b", priority="B", hot="", lead_score=40),
             venue(venue_key="permit", venue_history="Adding a permit")]
    due = [venue(venue_key="w1")]
    backlog = [venue(venue_key="b1"), venue(venue_key="t1")]
    work, over = contact.plan(today, due, backlog, {"t2": {"status": "reachable"},
                                                    "w1": {"status": "waiting"}}, cap=10)
    assert [(r["venue_key"], k) for r, k in work] == [("t1", "new"), ("w1", "recheck"),
                                                      ("b1", "backlog")]
    work, over = contact.plan(today, due, backlog, {}, cap=2)
    assert [r["venue_key"] for r, _ in work] == ["t1", "t2"] and over == 2


def test_settings(monkeypatch):
    for var in ("GOOGLE_PLACES_API_KEY", "IG_GRAPH_ACCESS_TOKEN", "IG_BUSINESS_ACCOUNT_ID",
                "ENRICH_DAILY_CAP"):
        monkeypatch.delenv(var, raising=False)
    assert not contact.configured() and not contact.ig_configured()
    assert contact.daily_cap() == 150
    monkeypatch.setenv("ENRICH_DAILY_CAP", "nope")
    assert contact.daily_cap() == 150
    monkeypatch.setenv("ENRICH_DAILY_CAP", "20")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", " k ")
    monkeypatch.setenv("IG_GRAPH_ACCESS_TOKEN", "t")
    assert contact.configured() and not contact.ig_configured() and contact.daily_cap() == 20


# --- CLI ---

def test_cli_enrich_skips_when_not_configured(monkeypatch, caplog):
    for var in ("GOOGLE_PLACES_API_KEY", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)  # must not touch a DB
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["enrich"]) == 0
    assert "enrich skipped (not configured): 0 venues checked" in caplog.text


def test_cli_enrich_logs_counts_only_and_stays_green(monkeypatch, caplog):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "secret-places-key")
    monkeypatch.delenv("IG_GRAPH_ACCESS_TOKEN", raising=False)

    class NullConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli.db, "connect", lambda: NullConn())
    monkeypatch.setattr(cli.db, "init_schema", lambda conn: None)
    seen = {}

    def fake_run(conn, **kw):
        seen.update(kw)
        contact.log.warning("enrich FAILED %s x%d", "places failed (HTTP 403)", 2)
        return {"checked": 3, "new": 2, "recheck": 1, "backlog": 0, "reachable": 1,
                "newly_reachable": 1, "waiting": 2, "gave_up": 0, "over_cap": 0,
                "failed": 2, "instagram": 0}

    monkeypatch.setattr(contact, "run", fake_run)
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["enrich", "--cap", "5"]) == 0
    assert seen["cap"] == 5 and seen["instagram"] is None
    assert ("enrich: checked 3 (new 2, rechecks 1, backlog 0); reachable 1, newly "
            "reachable 1, waiting 2, gave up 0; over daily cap 0; lookups failed 2; "
            "instagram off") in caplog.text
    assert "enrich FAILED places failed (HTTP 403) x2" in caplog.text
    assert "secret-places-key" not in caplog.text


# --- whole run against a disposable Postgres ---

def _seed(pg, now):
    from licmon import pipeline
    from licmon.models import Record
    from licmon.sources.base import Source

    class FakeContactSource(Source):
        name = "fake_contact_tx"
        title = "fake"
        state = "TX"
        min_records = 1

        def fetch(self, http):
            rows = [{"id": "c1", "name": "Zebra Fake Nightclub", "addr": "100 Fake St"},
                    {"id": "c2", "name": "Quokka Fake Lounge", "addr": "200 Fake St"},
                    {"id": "c3", "name": "Fake Corner Bistro", "addr": "300 Fake St"}]
            return [Snapshot(url="https://example.invalid/contact",
                             body=json.dumps(rows).encode(), content_type="application/json",
                             fetched_at=now)]

        def parse(self, snapshots):
            for row in json.loads(snapshots[0].body):
                yield Record(
                    source=self.name, source_record_id=row["id"],
                    source_url=f"https://example.invalid/contact?id={row['id']}",
                    legal_name=row["name"] + " LLC", dba=row["name"], license_type="MB",
                    license_description="Mixed Beverage Permit",
                    application_type="ORIGINAL", status="Received",
                    application_date=(now - timedelta(days=1)).date(),
                    address=row["addr"], city="Austin", state="TX", zip="78701",
                    county="Travis", category="on_premise", raw=row)

    pipeline.run([FakeContactSource()], conn=pg, http=object(), now=now, trigger="test")


def test_enrich_run_waiting_then_newly_reachable(pg):
    from licmon import attio, leadsheet

    day1 = datetime(2026, 10, 1, 16, tzinfo=timezone.utc)
    _seed(pg, day1)
    before = leadsheet.load_rows(pg, day1.date())
    assert len(before) == 3 and not any(r.get("contact_status") for r in before)

    def results(query):
        return [place(name="Zebra Fake Nightclub")] if "Zebra" in query else []

    places = FakePlaces(results)
    counts = contact.run(pg, places=places, website=FakeWebsite(site_links()),
                         now=day1, cap=10)
    # The bistro is tier C: not eligible, never looked up.
    assert len(places.queries) == 2 and not any("Bistro" in q for q in places.queries)
    assert {k: counts[k] for k in ("checked", "new", "reachable", "waiting")} == {
        "checked": 2, "new": 2, "reachable": 1, "waiting": 1}

    rows = {r["business_name"]: r for r in leadsheet.load_rows(pg, day1.date())}
    zebra, quokka = rows["Zebra Fake Nightclub"], rows["Quokka Fake Lounge"]
    assert zebra["contact_status"] == "reachable" and zebra["newly_reachable"] == ""
    # Instagram was not checked by API (no post date), so the open phone wins.
    assert zebra["outreach_method"] == "Call"
    assert zebra["outreach_second"] == "Facebook message"
    assert zebra["contact_url"] == "tel:+15125550142"
    assert zebra["verified_instagram_url"] == "https://www.instagram.com/zebrafakelounge/"
    assert zebra["verified_phone"] == "(512) 555-0142"
    assert quokka["contact_status"] == "waiting"
    assert quokka["next_check"] == day1 + timedelta(days=7)
    assert rows["Fake Corner Bistro"].get("contact_status") is None
    # Attio: only the reachable venue goes (the bistro is C anyway).
    assert [r["business_name"] for r in attio.candidates(list(rows.values()))] == [
        "Zebra Fake Nightclub"]

    # Same day again: nothing is due, nothing is looked up twice.
    places.queries.clear()
    contact.run(pg, places=places, website=None, now=day1 + timedelta(hours=1), cap=10)
    assert places.queries == []

    # A week later the Quokka listing appears: newly reachable on that day's
    # sheet although it was queued a week ago.
    day8 = day1 + timedelta(days=7, hours=1)
    places.results = lambda q: [place(name="Quokka Fake Lounge", number="200")]
    counts = contact.run(pg, places=places, website=FakeWebsite(None), now=day8, cap=10)
    assert counts["recheck"] == 1 and counts["newly_reachable"] == 1
    today_rows = leadsheet.load_rows(pg, day8.date())
    assert [(r["business_name"], r["newly_reachable"]) for r in today_rows] == [
        ("Quokka Fake Lounge", "Newly reachable")]
    assert today_rows[0]["queue_date"] == day1.date()
    assert [r["business_name"] for r in attio.candidates(today_rows)] == [
        "Quokka Fake Lounge"]
    with pg.cursor() as cur:
        cur.execute("SELECT status, attempts FROM contact_checks ORDER BY venue_key")
        assert cur.fetchall() == [("reachable", 1), ("reachable", 2)]


# --- Instagram found by web search (results only; instagram.com is never opened) ---

def hit(handle_or_url, title=None, desc=""):
    url = (handle_or_url if "/" in handle_or_url
           else f"https://www.instagram.com/{handle_or_url}/")
    return contact.SearchHit(url, title if title is not None else
                             f"Zebra Fake Lounge (@{handle_or_url}) • Instagram photos "
                             "and videos", desc)


class FakeSearch:
    def __init__(self, hits=None, error=None):
        self.hits = hits or []
        self.error = error
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.hits


def search_venue(**kw):
    return venue(**{"market": "Austin", **kw})


def test_search_profile_handle_keeps_profiles_only():
    keep = {"https://www.instagram.com/zebrafake.atx/": "zebrafake.atx",
            "https://instagram.com/ZebraFake_ATX?hl=en": "zebrafake_atx",
            "https://m.instagram.com/zebrafake/": "zebrafake"}
    for url, handle in keep.items():
        assert contact.search_profile_handle(url) == handle
    for url in ("https://www.instagram.com/p/AbC123/", "https://www.instagram.com/reel/XyZ/",
                "https://www.instagram.com/reels/XyZ/", "https://www.instagram.com/explore/",
                "https://www.instagram.com/stories/zebrafake/123/",
                "https://www.instagram.com/tv/AbC/", "https://www.instagram.com/accounts/login/",
                "https://www.instagram.com/zebrafake/p/AbC123/",
                "https://www.instagram.com/zebrafake/reel/XyZ/",
                "https://www.instagram.com/instagram/", "https://www.instagram.com/creators/",
                "https://www.instagram.com/", "https://www.facebook.com/zebrafake/",
                "https://zebrafake.test/instagram.com/zebrafake", "", None):
        assert contact.search_profile_handle(url) is None, url


def test_profile_name_from_result_title():
    assert contact.profile_name(
        "Zebra Fake Lounge (@zebrafake.atx) • Instagram photos and videos") == \
        "Zebra Fake Lounge"
    assert contact.profile_name("Zebra &amp; Fake (@zf) | Instagram") == "Zebra & Fake"
    assert contact.profile_name("@zebrafake • Instagram photos and videos") is None
    assert contact.profile_name("Zebra Fake on Instagram: \"Friday\"") is None


def test_instagram_query_quotes_the_name_without_entity_words():
    assert contact.instagram_query(search_venue()) == \
        'site:instagram.com "Zebra Fake Lounge" Austin'
    row = search_venue(business_name=None, company='Zebra "Fake" Holdings, L.L.C.')
    assert contact.instagram_query(row) == 'site:instagram.com "Zebra Fake Holdings" Austin'
    assert contact.instagram_query(search_venue(business_name="Fake Bar Inc.", city=None)) == \
        'site:instagram.com "Fake Bar"'
    assert contact.instagram_query(search_venue(business_name=None, company=None)) is None


def test_search_name_match_requires_every_distinctive_word():
    m = contact.search_name_match
    # Display name: all distinctive words, generic ones (Lounge) not needed.
    assert m("Zebra Fake Lounge", "The Zebra Fake", "zz")
    assert m("Zebra Fake Lounge LLC", "ZEBRA FAKE BAR & KITCHEN", "zz")
    assert not m("Zebra Fake Lounge", "Zebra Lounge", "zz")  # Fake missing
    # Handle: the distinctive words inside it, dots and underscores removed.
    assert m("Fake Lounge", None, "fakelounge.atx")
    assert m("Zebra Fake Lounge", None, "the_zebra.fake")
    assert not m("Zebra Fake Lounge", None, "zebralounge.atx")
    # Very short names count in the display name only.
    assert m("Bo Bar", "Bo", "bobar.atx") and not m("Bo Bar", None, "bobar.atx")
    # Only generic words: the whole name, exactly.
    assert m("The Lounge", "Lounge", "x") and m("Kitchen & Bar", None, "kitchenbar.atx")
    assert not m("The Lounge", "Zebra Lounge", "zebralounge")
    assert not m("Kitchen & Bar", "Fake Kitchen and Bar", "fakekitchenbar")
    assert not m(None, "Zebra", "zebra") and not m("LLC", "LLC", "llc")


def test_search_city_match_uses_city_and_metro_aliases():
    row = search_venue()
    c = contact.search_city_match
    assert c(row, "Zebra Fake (@zf) • Instagram", "Cocktails in Austin, TX", "zf")
    assert c(row, "Zebra Fake ATX (@zf) • Instagram", "", "zf")
    assert c(row, "", "", "zebrafake.atx") and c(row, "", "", "zebrafake_austin")
    assert c(row, "", "", "zebrafakeatx") and c(row, "", "", "atxzebrafake")
    assert not c(row, "Zebra Fake (@zf)", "Cocktails in Dallas", "zebrafake.dfw")
    assert not c(row, "", "", "zebrafake")
    chi = search_venue(city="Chicago", market="Chicago", state="IL")
    assert c(chi, "", "", "zebrafake.chi")
    assert not c(chi, "", "Tai chi and tea", "zebrafake")  # "chi" counts in handles only
    assert c(chi, "", "Best bar in Chi-town? Chicago", "zebrafake")
    la = search_venue(city="Los Angeles", market="Los Angeles / Orange County", state="CA")
    assert c(la, "", "", "zebrafake.la") and not c(la, "", "la mejor", "zebrafake")
    assert c(la, "", "Now open in DTLA", "zebrafake")


def test_search_address_match_in_snippet():
    row = search_venue()
    assert contact._names_address("Now open at 100 Fake St, Austin", row)
    assert contact._names_address("Austin TX 78701", row)
    assert not contact._names_address("100 Other St, Austin", row)
    assert not contact._names_address("1,234 followers", row)


def search_result(hits, row=None):
    return contact.instagram_from_search(row or search_venue(), hits)


def test_search_labels_name_address_city_and_name_only():
    [ch] = search_result([hit("zebrafakelounge", desc="Cocktails. 100 Fake St")])
    assert ch.kind == "instagram_search" and ch.value == "@zebrafakelounge"
    assert ch.url == "https://www.instagram.com/zebrafakelounge/"
    assert ch.signals == ["ig_search_name", "ig_search_address"]
    assert (ch.score, ch.label) == (55, "Likely")
    assert contact.reason(ch) == "Found by web search: name and address match"

    [ch] = search_result([hit("zebrafakelounge", desc="Austin's newest lounge")])
    assert (ch.score, ch.label) == (50, "Likely")
    assert contact.reason(ch) == "Found by web search: name and city match"

    [ch] = search_result([hit("zebrafake.atx", desc="100 Fake St, Austin TX 78701")])
    assert (ch.score, ch.label) == (70, "Likely")  # never Verified from search alone
    assert contact.reason(ch) == "Found by web search: name, address and city match"
    full = contact.score_channel(Channel("instagram_search", "@x", signals=[
        "ig_search_name", "ig_search_address", "ig_search_city"] * 2))
    assert full.score == contact.CAP_SEARCH == 74

    [ch] = search_result([hit("zebrafakelounge", desc="Cocktails and DJs")])
    assert (ch.score, ch.label) == (35, "Unverified") and not contact.reachable(ch)
    assert contact.reason(ch) == "Found by web search: name match only; check by hand"

    # A profile whose name does not match never counts, whatever else it says.
    assert search_result([hit("quokkafake.atx", title="Quokka Fake (@quokkafake.atx)",
                              desc="100 Fake St, Austin 78701")]) == []


def test_search_ambiguous_handles_are_unverified_and_both_shown():
    hits = [hit("zebrafakelounge", desc="Austin"), hit("zebrafake.atx", desc="")]
    chans = search_result(hits)
    assert [c.value for c in chans] == ["@zebrafakelounge", "@zebrafake.atx"]
    assert all("ig_search_ambiguous" in c.signals and c.label == "Unverified"
               for c in chans)
    assert contact.reason(chans[0]).startswith("Found by web search: more than one account")
    # A clearly better match wins; duplicates of one handle are one account.
    hits = [hit("zebrafakelounge", desc="100 Fake St, Austin"),
            hit("zebrafakelounge", desc=""),
            hit("https://www.instagram.com/zebrafakelounge/?hl=en"),
            hit("zebrafake.dfw", title="Zebra Fake (@zebrafake.dfw)", desc="Dallas")]
    [ch] = search_result(hits)
    assert ch.value == "@zebrafakelounge" and ch.label == "Likely"


def test_check_venue_searches_only_without_a_good_website_instagram():
    likely = [hit("zebrafakelounge", desc="Austin")]
    # The website links an Instagram (Verified): no search.
    search = FakeSearch(likely)
    result = contact.check_venue(search_venue(), FakePlaces([place()]),
                                 FakeWebsite(site_links()), None, TODAY, search=search)
    assert search.queries == [] and not result.searched
    # No Google listing at all: the search is the only way to find it.
    search = FakeSearch(likely)
    result = contact.check_venue(search_venue(), FakePlaces([]), None, None, TODAY,
                                 search=search)
    assert search.queries == ['site:instagram.com "Zebra Fake Lounge" Austin']
    assert result.searched and result.reachable
    assert (result.method, result.second) == ("Instagram DM (found by search)", None)
    assert result.best.url == "https://www.instagram.com/zebrafakelounge/"
    assert result.reason == "Found by web search: name and city match"
    # The website's Instagram is only Unverified (listing under another
    # name): search too, and the website-derived channels are unchanged.
    search = FakeSearch(likely)
    result = contact.check_venue(search_venue(), FakePlaces([place(name="Old Quokka Saloon")]),
                                 FakeWebsite(site_links(emails=[], facebook=[])), None, TODAY,
                                 search=search)
    assert len(search.queries) == 1
    ch = by_kind(result)
    assert ch["phone"].score <= 49 and ch["instagram"].score <= 49
    assert ch["instagram_search"].label == "Likely"
    assert result.method == "Instagram DM (found by search)"


def test_check_venue_without_search_is_unchanged():
    args = (search_venue(), FakePlaces([place()]), FakeWebsite(site_links()), None, TODAY)
    a, b = contact.check_venue(*args), contact.check_venue(*args, search=None)
    assert [(c.kind, c.score) for c in a.channels] == [(c.kind, c.score) for c in b.channels]
    assert not any(c.kind == "instagram_search" for c in a.channels) and not a.searched


def test_search_failure_is_noted_not_raised():
    bad = contact.ContactError("instagram search", "HTTP 401", fatal=True)
    result = contact.check_venue(search_venue(), FakePlaces([]), None, None, TODAY,
                                 search=FakeSearch(error=bad))
    assert result.lookups_failed == ["instagram search failed (HTTP 401)"]
    assert result.fatal_lookups == {"instagram search"} and not result.reachable


def test_method_order_puts_search_after_email():
    assert [m for m, *_ in contact.METHOD_RULES] == [
        "Instagram DM", "Call", "Facebook message", "Email",
        "Instagram DM (found by search)", "Instagram DM (no recent posts seen)",
        "Website contact form", "Call (Google does not say it is open)"]
    # A Likely search find with a Verified email: Email first, then the DM.
    result = contact.check_venue(
        search_venue(), FakePlaces([place(phone=None)]),
        FakeWebsite(site_links(instagram=[], facebook=[])), None, TODAY,
        search=FakeSearch([hit("zebrafakelounge", desc="Austin")]))
    assert (result.method, result.second) == ("Email", "Instagram DM (found by search)")


# --- the Brave client (fake session and clock; never the network) ---

def test_brave_request_shape_spacing_and_errors_hide_key_and_query():
    body = {"web": {"results": [
        {"url": "https://www.instagram.com/zebrafakelounge/",
         "title": "Zebra Fake Lounge (@zebrafakelounge)",
         "description": "<strong>Zebra</strong> Fake &amp; friends"},
        {"title": "no url"}, "junk"]}}
    session = FakeSession(FakeResp(200, body), FakeResp(200, {}))
    clock = {"t": 100.0}
    slept = []

    def sleep(s):
        slept.append(round(s, 2))
        clock["t"] += s

    brave = contact.BraveSearch(key="secret-brave-key",
                                api=contact.Api(session, sleep=sleep),
                                clock=lambda: clock["t"], sleep=sleep)
    [h] = brave.search('site:instagram.com "Zebra Fake Lounge" Austin')
    assert h.url.endswith("/zebrafakelounge/") and "Zebra" in h.description
    call = session.calls[0]
    assert call["method"] == "GET" and call["url"] == contact.SEARCH_URL
    assert call["params"] == {"q": 'site:instagram.com "Zebra Fake Lounge" Austin',
                              "count": 10, "country": "us"}
    assert call["headers"] == {"X-Subscription-Token": "secret-brave-key",
                               "Accept": "application/json"}
    assert slept == []
    clock["t"] += 0.3
    assert brave.search("q2") == []
    assert slept == [0.8]  # spaced 1.1 s apart

    for status, fatal in ((401, True), (403, True), (402, True), (422, False)):
        brave.api.session = FakeSession(FakeResp(status, {"error": "Zebra Fake"}))
        with pytest.raises(contact.ContactError) as err:
            brave.search('site:instagram.com "Zebra Fake Lounge" Austin')
        assert str(err.value) == f"instagram search failed (HTTP {status})"
        assert err.value.fatal is fatal
        assert "secret-brave-key" not in str(err.value) and "Zebra" not in str(err.value)
    # 429 is retried first, then fatal for the day.
    brave.api.session = FakeSession(FakeResp(429), FakeResp(429), FakeResp(429))
    with pytest.raises(contact.ContactError) as err:
        brave.search("q")
    assert err.value.fatal and len(brave.api.session.calls) == 3


def test_web_search_is_off_without_the_key(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    assert not contact.search_configured() and contact.web_search() is None
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "  ")
    assert contact.web_search() is None
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "k")
    assert isinstance(contact.web_search(), contact.BraveSearch)


# --- run(): a fatal search error stops searching, the run goes on, logs stay clean ---

class _Cur:
    description = [type("Col", (), {"name": n}) for n in ("venue_key", "status", "first_checked_at",
                                  "became_reachable_at", "newly_reachable_on", "gave_up_at",
                                  "next_check_at")]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *a, **kw):
        pass

    def fetchall(self):
        return []


class _Conn:
    def cursor(self):
        return _Cur()

    def commit(self):
        pass


def test_run_fatal_search_stops_searching_and_logs_counts_only(monkeypatch, caplog):
    from licmon import leadsheet

    rows = [search_venue(venue_key=f"k{i}", business_name=f"Zebra Fake {n}")
            for i, n in enumerate(("One", "Two", "Three"))]
    monkeypatch.setattr(leadsheet, "load_rows",
                        lambda conn, day, **kw: rows if day is not None else [])
    saved = []
    monkeypatch.setattr(contact, "save", lambda conn, key, result, state, now:
                        saved.append((key, result)))
    bad = contact.ContactError("instagram search", "HTTP 401", fatal=True)
    search = FakeSearch(error=bad)
    places = FakePlaces([])
    with caplog.at_level("DEBUG"):
        counts = contact.run(_Conn(), places=places, now=NOW, cap=10, search=search)
    assert len(search.queries) == 1  # stopped after the first 401
    assert len(places.queries) == 3 and len(saved) == 3  # the run went on
    assert (counts["checked"], counts["searches"], counts["instagram_search"]) == (3, 1, 1)
    assert "enrich FAILED instagram search failed (HTTP 401) x1" in caplog.text
    for secret in ("Zebra", "Fake", "site:instagram", "Austin", "zebrafake"):
        assert secret not in caplog.text


def test_run_counts_search_finds(monkeypatch, caplog):
    from licmon import leadsheet

    rows = [search_venue(venue_key="k1")]
    monkeypatch.setattr(leadsheet, "load_rows",
                        lambda conn, day, **kw: rows if day is not None else [])
    saved = []
    monkeypatch.setattr(contact, "save", lambda conn, key, result, state, now:
                        saved.append(state.status))
    search = FakeSearch([hit("zebrafakelounge", desc="Austin"),
                         hit("zebrafake.dfw", title="Zebra Fake Lounge (@zebrafake.dfw)",
                             desc="")])
    with caplog.at_level("DEBUG"):
        counts = contact.run(_Conn(), places=FakePlaces([]), now=NOW, cap=10, search=search)
    assert (counts["searches"], counts["search_found"], counts["search_likely"]) == (1, 1, 1)
    assert saved == ["reachable"]
    assert "zebrafake" not in caplog.text.lower()


def test_cli_enrich_logs_instagram_search_on_off(monkeypatch, caplog):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "secret-places-key")
    monkeypatch.delenv("IG_GRAPH_ACCESS_TOKEN", raising=False)

    class NullConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli.db, "connect", lambda: NullConn())
    monkeypatch.setattr(cli.db, "init_schema", lambda conn: None)
    seen = {}

    def fake_run(conn, **kw):
        seen.update(kw)
        on = kw["search"] is not None
        return {"checked": 2, "new": 2, "recheck": 0, "backlog": 0, "reachable": 1,
                "newly_reachable": 0, "waiting": 1, "gave_up": 0, "over_cap": 0,
                "failed": 0, "instagram": 0, "instagram_search": int(on),
                "searches": 2 if on else 0, "search_found": 1 if on else 0,
                "search_likely": 1 if on else 0}

    monkeypatch.setattr(contact, "run", fake_run)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["enrich"]) == 0
    assert seen["search"] is None
    assert "instagram off; instagram search off" in caplog.text
    caplog.clear()
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "secret-brave-key")
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["enrich"]) == 0
    assert isinstance(seen["search"], contact.BraveSearch)
    assert "instagram search on: searches 2, handles found 1, likely 1" in caplog.text
    assert "secret-brave-key" not in caplog.text
    import logging
    assert logging.getLogger("urllib3").level > logging.CRITICAL  # never logs the query URL
