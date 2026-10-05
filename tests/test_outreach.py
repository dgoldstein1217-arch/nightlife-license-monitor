"""The daily outreach plan and its openers. Synthetic data only: every venue
and person is invented (Zebra Fake, Quokka Fake, Jane Tester)."""

from __future__ import annotations

import itertools

import pytest

from licmon import outreach
from licmon.history import ADDING_PERMIT, NEW_OWNER, NEW_VENUE, UNKNOWN
from licmon.leadsheet import NEW_FILING, STAGE_ADVANCED, DETAILS_CHANGED


def plan_row(i=1, **kw):
    """A sheet row after the contact lookup, reachable by Instagram DM."""
    base = {
        "venue_key": f"TX|78701|{i} FAKE ST", "business_name": "Zebra Fake Lounge",
        "company": "Zebra Fake Holdings LLC", "city": "Austin", "state": "TX",
        "priority": "A", "hot": "Hot", "lead_score": 80, "stage": "Approved",
        "whats_new": NEW_FILING, "venue_history": NEW_VENUE, "adult": False,
        "review_status": "new", "contact_status": "reachable", "newly_reachable": "",
        "outreach_method": "Instagram DM", "outreach_second": "Call",
        "contact_url": "https://www.instagram.com/zebrafakelounge/",
        "contact_url_text": "@zebrafakelounge", "confidence": "Verified 90",
        "contact_person": None, "license_keys": [], "platforms": [],
        "current_platform": "None found on their site", "opening_soon": False,
        "opening_signal": None, "opening_text": "", "google_type": None,
        "on_speakeasy": False, "pipeline": "", "lead_ids": str(100 + i),
    }
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def no_proof(monkeypatch):
    for var in outreach.PROOF_ENV.values():
        monkeypatch.delenv(var, raising=False)


# --- {first}: only a natural person's first name ---

@pytest.mark.parametrize("person, first", [
    ("Jane Q Tester", "Jane"),
    ("JANE TESTER", "Jane"),
    ("Tester, Jane Q", "Jane"),
    ("J Tester", None),  # an initial is not a first name
    ("J. Quinn Tester", "Quinn"),
    ("Mary-Kate O'Tester", "Mary-Kate"),
    ("Zebra Fake LLC", None),
    ("Quokka Holdings", None),
    ("Okapi Group", None),
    ("Tester Family Trust", None),
    ("Fake Partners", None),
    ("Fake Enterprises", None),
    ("Tester & Co", None),
    ("Tester Company", None),
    ("Okapi LP", None),
    ("Okapi Ltd", None),
    ("Okapi Inc", None),
    ("Okapi Corp", None),
    ("Jane Tester 2", None),  # digits
    ("Jane", None),  # one word: not sure it is a person
    ("Zebra Lounge", None),  # a venue word
    (None, None),
    ("", None),
])
def test_first_name_only_for_natural_persons(person, first):
    assert outreach.first_name(person) == first


def test_first_name_never_repeats_the_venue_name():
    assert outreach.first_name("Zebra Tester", ("Zebra Fake Lounge",)) is None
    assert outreach.first_name("Jane Tester", ("Zebra Fake Lounge",)) == "Jane"


# --- openers: every channel x angle x timing x platform x name ---

PRODUCT = {
    "club_lounge": ("We run tables, bottle service and the door for clubs and lounges, "
                    "all in one place."),
    "ticketed": ("We run ticketing, guest lists and the door for venues like yours, "
                 "all in one place."),
    "bar": "We help bars sell tickets to events and text their regulars, all from one place.",
    "restaurant": "We run reservations, tables and POS in one place for spots like yours.",
}
TIMING = {
    "opening_soon": "Congrats on the upcoming opening of Zebra Fake Lounge.",
    "just_opened": "Congrats on opening Zebra Fake Lounge.",
    "new_owner": "Congrats on taking over Zebra Fake Lounge.",
}
PLATFORM = "Saw you're on SevenRooms. Happy to show you a side by side."


def expected(channel, angle, timing, platform, first):
    soon = timing == "opening_soon"
    lines = []
    if channel == "dm":
        lines += [f"Hey {first}," if first else "Hey hey,", TIMING[timing],
                  "I'm Dylan, I run growth at Speakeasy. " + PRODUCT[angle]]
        lines += [PLATFORM] if platform else []
        lines += ["Would love to grab 15 minutes before you open. What does your week "
                  "look like?" if soon else
                  "Would love to grab 15 minutes this week or next. What works for you?"]
    elif channel == "email":
        lines += ["Subject: Zebra Fake Lounge + Speakeasy",
                  f"Hey {first}," if first else "Hey Zebra Fake Lounge team,",
                  TIMING[timing], "I'm Dylan, Head of Growth at Speakeasy.", PRODUCT[angle]]
        lines += [PLATFORM] if platform else []
        lines += ["Would love to grab 15 minutes before you open." if soon else
                  "Would love to grab 15 minutes this week or next.",
                  "Happy to work around your schedule.",
                  "What does your week look like?" if soon else "What works for you?",
                  "Best,\nDylan"]
    else:
        lines += ["Hi, this is Dylan with Speakeasy. Is the owner or GM around?",
                  TIMING[timing], PRODUCT[angle]]
        lines += [PLATFORM] if platform else []
        lines += ["Could I get 15 minutes on your calendar before you open?" if soon else
                  "Could I get 15 minutes on your calendar this week?"]
    return "\n\n".join(lines)


@pytest.mark.parametrize("channel, angle, timing, platform, first", list(itertools.product(
    ("dm", "email", "call"), ("club_lounge", "ticketed", "bar", "restaurant"),
    ("opening_soon", "just_opened", "new_owner"), (None, "SevenRooms"), (None, "Jane"))))
def test_opener_assembly(channel, angle, timing, platform, first):
    text = outreach.compose_opener(channel, venue="Zebra Fake Lounge", angle=angle,
                                   timing=timing, platform=platform, first=first)
    assert text == expected(channel, angle, timing, platform, first)
    assert "—" not in text and "–" not in text and ";" not in text


def test_opener_proof_line_from_settings_only(monkeypatch):
    monkeypatch.setenv("OUTREACH_PROOF_CLUB_LOUNGE", "  Zebra Fake Proof sold out 3 nights.  ")
    dm = outreach.compose_opener("dm", venue="Zebra Fake Lounge", angle="club_lounge",
                                 timing="just_opened", platform="DICE", first=None)
    assert dm.split("\n\n") == [
        "Hey hey,", "Congrats on opening Zebra Fake Lounge.",
        "I'm Dylan, I run growth at Speakeasy. " + PRODUCT["club_lounge"],
        "Zebra Fake Proof sold out 3 nights.",
        "Saw you're on DICE. Happy to show you a side by side.",
        "Would love to grab 15 minutes this week or next. What works for you?"]
    email = outreach.compose_opener("email", venue="Zebra Fake Lounge", angle="club_lounge",
                                    timing="just_opened", platform=None, first=None)
    parts = email.split("\n\n")
    assert parts[parts.index(PRODUCT["club_lounge"]) + 1] == "Zebra Fake Proof sold out 3 nights."
    # Another angle's proof is never borrowed.
    bar = outreach.compose_opener("call", venue="Zebra Fake Lounge", angle="bar",
                                  timing="just_opened", platform=None, first=None)
    assert "Proof" not in bar


def test_channel_from_method():
    assert outreach.channel_for("Instagram DM") == "dm"
    assert outreach.channel_for("Instagram DM (found by search)") == "dm"
    assert outreach.channel_for("Instagram DM (no recent posts seen)") == "dm"
    assert outreach.channel_for("Facebook message") == "dm"
    assert outreach.channel_for("Email") == "email"
    assert outreach.channel_for("Website contact form") == "email"
    assert outreach.channel_for("Call") == "call"
    assert outreach.channel_for("Call (Google does not say it is open)") == "call"


# --- timing and angle from the data ---

def test_timing_from_stage_history_and_signals():
    assert outreach.timing(plan_row(stage="Received")) == "opening_soon"
    assert outreach.timing(plan_row(stage="In review")) == "opening_soon"
    assert outreach.timing(plan_row(stage="Approved")) == "opening_soon"
    assert outreach.timing(plan_row(stage="Licensed")) == "just_opened"
    assert outreach.timing(plan_row(stage="Licensed", venue_history=UNKNOWN)) == "just_opened"
    assert outreach.timing(plan_row(stage="Licensed", opening_soon=True,
                                    opening_signal="coming soon")) == "opening_soon"
    assert outreach.timing(plan_row(stage="Approved", venue_history=NEW_OWNER)) == "new_owner"
    assert outreach.timing(plan_row(stage="Licensed", venue_history=NEW_OWNER,
                                    opening_soon=True)) == "new_owner"
    # The site says now open: it already opened, whatever the filing says.
    assert outreach.timing(plan_row(stage="Received", opening_signal="now open")) == \
        "just_opened"


@pytest.mark.parametrize("kw, angle", [
    ({"business_name": "Zebra Fake Lounge"}, "club_lounge"),
    ({"business_name": "Zebra Fake Nightclub"}, "club_lounge"),
    ({"business_name": "Zebra Fake Rooftop"}, "club_lounge"),
    ({"business_name": "Zebra Fake Comedy Club"}, "ticketed"),
    ({"business_name": "Zebra Fake Theater"}, "ticketed"),
    ({"business_name": "Zebra Fake Events", "license_keys": ["music_venue"]}, "ticketed"),
    ({"business_name": "Zebra Fake", "license_keys": ["nightclub_cabaret"]}, "club_lounge"),
    ({"business_name": "Zebra Fake Comedy Club", "google_type": "night_club"}, "club_lounge"),
    ({"business_name": "Zebra Fake Lounge", "google_type": "live_music_venue"}, "ticketed"),
    ({"priority": "B", "business_name": "Zebra Fake Taproom"}, "bar"),
    ({"priority": "B", "business_name": "Zebra Fake Tavern", "google_type": "restaurant"},
     "restaurant"),
    ({"priority": "C", "business_name": "Zebra Fake Bistro"}, "restaurant"),
    ({"priority": "C", "business_name": "Zebra Fake Bistro", "google_type": "wine_bar"}, "bar"),
])
def test_angle_from_tier_name_license_and_google(kw, angle):
    assert outreach.angle(plan_row(company=None, **kw)) == angle


# --- why reach out: from the data, never invented ---

def test_why_bullets_from_data():
    why = outreach.why(plan_row(stage="Approved", license_keys=["late_hours"],
                                platforms=[], opening_soon=True,
                                opening_signal="coming soon"))
    assert why == ["License approved, not open yet: pitch before launch",
                   "Lounge: tables, bottle service and the door",
                   "No ticketing or reservations found on their site: greenfield",
                   "Their site or Instagram says coming soon"]
    why = outreach.why(plan_row(stage="Licensed", platforms=["SevenRooms", "Toast"],
                                license_keys=["late_hours", "ppa"], venue_history=NEW_VENUE))
    assert why == ["Just licensed: opening now",
                   "Lounge: tables, bottle service and the door",
                   "Uses SevenRooms and Toast: switch pitch",
                   "Late hours and public place of amusement licenses"]
    why = outreach.why(plan_row(stage="In review", venue_history=NEW_OWNER, platforms=None,
                                priority="B", business_name="Quokka Fake Tavern",
                                google_type="pub"))
    assert why == ["New owner taking over", "Pub: event tickets and texting regulars"]
    why = outreach.why(plan_row(stage="Received", platforms=None, venue_history=NEW_VENUE,
                                google_type="night_club", business_name="Okapi Fake Hall"))
    assert why == ["License just filed, not open yet: pitch before launch",
                   "Nightclub: tables, bottle service and the door",
                   "New venue: no license at this address before"]
    no_reviews = outreach.why(plan_row(stage="Licensed", platforms=None, opening_soon=True,
                                       opening_signal="no Google reviews yet",
                                       venue_history=UNKNOWN))
    assert no_reviews == ["Licensed, not open yet: pitch before launch",
                          "Lounge: tables, bottle service and the door",
                          "No Google reviews yet"]
    for bullets in (why, no_reviews):
        assert 2 <= len(bullets) <= 4
        assert all(";" not in b and "—" not in b for b in bullets)


# --- the plan: who is in it, who is not, in what order ---

def test_plan_inclusion_exclusion_and_order():
    rows = [
        plan_row(1, business_name="Zebra Fake Lounge", lead_score=95, stage="Licensed"),
        plan_row(2, business_name="Quokka Fake Lounge", hot="", lead_score=60,
                 stage="Licensed", newly_reachable="Newly reachable", whats_new=""),
        plan_row(3, business_name="Okapi Fake Lounge", hot="", lead_score=70,
                 stage="Approved"),
        plan_row(4, business_name="Narwhal Fake Lounge", hot="Hot", lead_score=76,
                 stage="Licensed", whats_new=STAGE_ADVANCED),
        plan_row(5, business_name="Ibex Fake Lounge", lead_score=50, stage="Licensed",
                 hot=""),
        # Left out, each for one reason:
        plan_row(6, business_name="Out Waiting", contact_status="waiting"),
        plan_row(7, business_name="Out Unchecked", contact_status=None),
        plan_row(8, business_name="Out Adult", adult=True),
        plan_row(9, business_name="Out Permit", venue_history=ADDING_PERMIT),
        plan_row(10, business_name="Out Speakeasy", on_speakeasy=True,
                 pipeline="Already on Speakeasy"),
        plan_row(11, business_name="Out Attio", pipeline="In Attio: Contacted"),
        plan_row(12, business_name="Out Details", whats_new=DETAILS_CHANGED),
        *[plan_row(20 + n, business_name=f"Out Reviewed {status}", review_status=status)
          for n, status in enumerate(("contacted", "replied", "won", "rejected", "snoozed"))],
    ]
    plan = outreach.build_plan(rows)
    assert [p["name"] for p in plan] == [
        "Quokka Fake Lounge",  # newly reachable first
        "Okapi Fake Lounge",  # then opening soon (not licensed yet)
        "Zebra Fake Lounge", "Narwhal Fake Lounge",  # then Hot, by score
        "Ibex Fake Lounge"]
    assert [p["order"] for p in plan] == [1, 2, 3, 4, 5]
    reasons = {r["business_name"]: outreach.left_out(r) for r in rows}
    assert reasons["Out Adult"] == "adult venue"
    assert reasons["Out Permit"] == "adding a permit"
    assert reasons["Out Speakeasy"] == "Already on Speakeasy"
    assert reasons["Out Attio"] == "In Attio: Contacted"
    assert reasons["Out Reviewed won"] == "reviewed: won"
    # approved and wrong_contact leads stay in (a new channel was found)
    assert outreach.build_plan([plan_row(review_status="approved")])
    assert outreach.build_plan([plan_row(review_status="wrong_contact",
                                         newly_reachable="Newly reachable")])
    # one entry per venue
    assert len(outreach.build_plan([plan_row(1), plan_row(1)])) == 1


def test_plan_entry_has_how_and_opener():
    [entry] = outreach.build_plan([plan_row(
        contact_person="Jane Q Tester", platforms=["SevenRooms"],
        current_platform="SevenRooms", stage="Approved")])
    assert entry["header"] == "Zebra Fake Lounge, Austin | Approved | Hot, tier A"
    assert entry["how"] == "Instagram DM: @zebrafakelounge (Verified 90)"
    assert entry["second"] == "Call"
    assert entry["channel"] == "dm"
    assert entry["opener"].startswith("Hey Jane,\n\nCongrats on the upcoming opening of "
                                      "Zebra Fake Lounge.")
    assert "Saw you're on SevenRooms." in entry["opener"]
    assert entry["contact_person"] == "Jane Q Tester"
    assert entry["lead_ids"] == "101"
    email = outreach.build_plan([plan_row(outreach_method="Email",
                                          contact_url="mailto:hi@zebrafake.test",
                                          contact_url_text="hi@zebrafake.test",
                                          newly_reachable="Newly reachable")])[0]
    assert email["opener"].startswith("Subject: Zebra Fake Lounge + Speakeasy\n\nHey Zebra "
                                      "Fake Lounge team,")
    assert email["header"].endswith("| Newly reachable")


def test_empty_plan():
    assert outreach.build_plan([]) == []
    assert outreach.build_plan([plan_row(contact_status="waiting")]) == []
    assert outreach.EMPTY_PLAN == "No new reachable venues today."
