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
        "company": "Zebra Fake Holdings LLC", "address": f"{i} Fake St", "city": "Austin",
        "state": "TX", "market": "Austin",
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
    "club_lounge": "We handle tables, bottle service and the door for clubs",
    "ticketed": "We do ticketing and the door for venues like yours",
    "bar": "We help bars run ticketed events and text their regulars",
    "restaurant": "We handle reservations, tables and POS for restaurants",
}
VENUE = "Zebra Fake Lounge"
WHERE = "on N Fake St"
PLATFORM = "Saw you guys use SevenRooms. Happy to show you how we compare if you're curious."


DM_FIRST = {
    "opening_soon": ("Ran across your page and love what you guys are building",
                     f"Congrats on {VENUE}! Love what you guys are putting together"),
    "just_opened": (f"Ran across your page and love what you guys have going at {VENUE}",
                    f"Really respect what you guys have built with {VENUE}"),
    "new_owner": (f"Congrats on taking over {VENUE}! Excited to see what you guys do with it",),
}
DM_SECOND = {
    "opening_soon": "Saw you guys are opening soon",
    "just_opened": "Saw you guys just opened",
    "new_owner": "Saw you guys just took over",
}
DM_THIRD = {
    "club_lounge": "I think our table and bottle service booking could be a great touch "
                   "for your nights",
    "ticketed": "I think our fully white labeled ticketing pages could be a great touch "
                "for your events",
    "bar": "I think ticketed nights plus texting your regulars could be a great touch "
           "for you guys",
    "restaurant": "I think our reservations and table booking could be a great touch "
                  "for you guys",
}
CURIOUS = " and was curious if you've ever seen our platform @spkeasy"


def expected_dm(angle, timing, platform, first, variant=0, where=WHERE):
    hi = f"Hey {first}!" if first else "Hey team!"
    top = DM_FIRST[timing][variant]
    if timing == "opening_soon" and where:
        top += " " + where
    second = (f"Saw you guys use {platform}" if platform else DM_SECOND[timing]) + CURIOUS
    return (f"DM 1 of 3:\n{hi}\n\n{top}\n\nDM 2 of 3:\n{second}\n\n"
            f"DM 3 of 3:\n{proof_line(angle)}\n\n{DM_THIRD[angle]}")


def proof_line(angle, metro=None, key=VENUE):
    return ("We work with some of the top event organizers and venues in the US ("
            + outreach.name_drops(angle, metro, key) + ", & many more)")


FIRST_LINE = {
    "opening_soon": f"Congrats on {VENUE}! Love what you guys are building {WHERE}.",
    "just_opened": f"Congrats on opening {VENUE}! Really respect what you guys have built.",
    "new_owner": f"Congrats on taking over {VENUE}! Excited to see what you guys do with it.",
}


def expected_email(angle, timing, platform, first, local=False):
    when = "before you open" if timing == "opening_soon" else "this week or next"
    ask = ("Open to a quick coffee " if local else "Open to a quick call ") + when + "?"
    pitch = DM_THIRD[angle] + "."
    if platform:
        pitch += f" Happy to run alongside {platform}, no need to switch."
    lines = [f"Subject: Congrats on {VENUE}", f"Hey {first}," if first else "Hey there,",
             FIRST_LINE[timing],
             f"I'm Dylan with Speakeasy. We work with {outreach.name_drops(angle, None, VENUE)} "
             "and many more.", pitch, ask, "Best,\nDylan"]
    return "\n\n".join(lines)


CALL_TIMING = {
    "opening_soon": "Congrats on the new spot! When are you guys opening?",
    "just_opened": "Congrats on opening! How have the first few weeks been?",
    "new_owner": "Congrats on taking over! Planning any changes to the place?",
}


def expected_call(angle, timing, platform, local=False):
    lines = ["Hey, this is Dylan from Speakeasy. Is the owner or GM around?",
             CALL_TIMING[timing], PRODUCT[angle] + "."]
    lines += ["I know you're on SevenRooms right now. Happy to show you how we compare."] \
        if platform else []
    lines += ["Could I swing by this week and show you?" if local
              else "Could I set up a call this week to show you?"]
    return "\n\n".join(lines)


def compose(channel, angle="club_lounge", timing="opening_soon", platform=None, first=None,
            **kw):
    return outreach.compose_opener(channel, venue=VENUE, angle=angle, timing=timing,
                                   platform=platform, first=first, **kw)


def assert_clean(text):
    assert "—" not in text and "–" not in text and ";" not in text


@pytest.mark.parametrize("angle, timing, platform, first, variant", list(itertools.product(
    ("club_lounge", "ticketed", "bar", "restaurant"),
    ("opening_soon", "just_opened", "new_owner"), (None, "SevenRooms"), (None, "Jane"),
    (0, 1))))
def test_dm_opener(angle, timing, platform, first, variant):
    text = compose("dm", angle, timing, platform, first, where=WHERE, variant=variant)
    # new_owner has one wording, the others two.
    want_variant = 0 if timing == "new_owner" else variant
    assert text == expected_dm(angle, timing, platform, first, want_variant)
    assert_clean(text)


@pytest.mark.parametrize("angle, timing, platform, first, local", list(itertools.product(
    ("club_lounge", "ticketed", "bar", "restaurant"),
    ("opening_soon", "just_opened", "new_owner"), (None, "SevenRooms"), (None, "Jane"),
    (False, True))))
def test_email_opener(angle, timing, platform, first, local):
    text = compose("email", angle, timing, platform, first, where=WHERE, local=local)
    assert text == expected_email(angle, timing, platform, first, local)
    assert_clean(text)


@pytest.mark.parametrize("angle, timing, platform, first, local", list(itertools.product(
    ("club_lounge", "ticketed", "bar", "restaurant"),
    ("opening_soon", "just_opened", "new_owner"), (None, "SevenRooms"), (None, "Jane"),
    (False, True))))
def test_call_opener(angle, timing, platform, first, local):
    text = compose("call", angle, timing, platform, first, where=WHERE, local=local)
    assert text == expected_call(angle, timing, platform, local)  # a call greets nobody
    assert_clean(text)


def test_dm_opening_soon_variants_exact():
    assert compose("dm", "bar", first="Jane", where="on N Fake St", variant=0) == (
        "DM 1 of 3:\nHey Jane!\n\n"
        "Ran across your page and love what you guys are building on N Fake St\n\n"
        "DM 2 of 3:\nSaw you guys are opening soon and was curious if you've ever seen our "
        "platform @spkeasy\n\n"
        "DM 3 of 3:\n" + proof_line("bar") + "\n\n"
        "I think ticketed nights plus texting your regulars could be a great "
        "touch for you guys")
    assert compose("dm", "bar", platform="Toast", where="on N Fake St", variant=1) == (
        "DM 1 of 3:\nHey team!\n\n"
        "Congrats on Zebra Fake Lounge! Love what you guys are putting together on N Fake St"
        "\n\nDM 2 of 3:\nSaw you guys use Toast and was curious if you've ever seen our "
        "platform @spkeasy\n\n"
        "DM 3 of 3:\n" + proof_line("bar") + "\n\n"
        "I think ticketed nights plus texting your regulars could be a great "
        "touch for you guys")


def test_name_drops_are_the_most_similar_clients():
    # Same kind and same city first, then same kind.
    assert outreach.name_drops("restaurant", "Chicago", "IL|1").split(", ")[:2] in (
        ["Tao Group", "Lettuce Entertain You"], ["Lettuce Entertain You", "Tao Group"])
    assert outreach.name_drops("club_lounge", "Chicago", "IL|1").startswith("Tao Group, ")
    miami = set(outreach.name_drops("club_lounge", "Miami-Fort Lauderdale-West Palm Beach",
                                    "FL|1").split(", "))
    assert miami == {"Club Space", "Groot Hospitality", "Sable"}
    # Never a client of another kind while three of the same kind exist.
    for angle in ("club_lounge", "ticketed", "bar", "restaurant"):
        for metro in (None, "Chicago", "Austin", "New York City"):
            names = outreach.name_drops(angle, metro, "X|1").split(", ")
            fits = {n for n, angles, _ in outreach.NAME_DROPS if angle in angles}
            assert len(names) == 3 and set(names) <= fits
    # Mixed per venue, stable for one venue.
    picks = {outreach.name_drops("club_lounge", "Austin", f"TX|{i}") for i in range(30)}
    assert len(picks) > 1
    assert outreach.name_drops("bar", "Austin", "TX|9") == \
        outreach.name_drops("bar", "Austin", "TX|9")
    row = plan_row(9, market="Chicago", venue_key="IL|60601|9 FAKE ST", priority="B",
                   google_type="restaurant")
    assert proof_line("restaurant", "Chicago", "IL|60601|9 FAKE ST") in outreach.opener(row)


def test_dm_is_three_messages_with_no_intro_or_meeting_ask():
    for timing in ("opening_soon", "just_opened", "new_owner"):
        text = compose("dm", "club_lounge", timing, platform="Toast")
        assert [line for line in text.splitlines() if line.startswith("DM ")] == [
            "DM 1 of 3:", "DM 2 of 3:", "DM 3 of 3:"]
        assert "I'm Dylan" not in text and "show you" not in text
        assert "@spkeasy" in text.split("DM 2 of 3:")[1].split("DM 3 of 3:")[0]
        assert "Toast" not in text.split("DM 2 of 3:")[0]  # no pitch in message 1


def test_without_where_the_opening_line_drops_the_place():
    assert compose("dm", where="", variant=0).split("\n\n")[1] == \
        "Ran across your page and love what you guys are building"
    assert compose("dm", where="", variant=1).split("\n\n")[1] == \
        "Congrats on Zebra Fake Lounge! Love what you guys are putting together"
    assert compose("email", where="").split("\n\n")[2] == \
        "Congrats on Zebra Fake Lounge! Love what you guys are building."
    assert compose("dm", where="in Austin", variant=1).split("\n\n")[1] == \
        "Congrats on Zebra Fake Lounge! Love what you guys are putting together in Austin"


def test_email_ask_local_vs_not():
    local = compose("email", "bar", "new_owner", local=True).split("\n\n")
    away = compose("email", "bar", "new_owner", local=False).split("\n\n")
    assert local[-2] == "Open to a quick coffee this week or next?"
    assert away[-2] == "Open to a quick call this week or next?"
    assert compose("call", local=True).endswith("Could I swing by this week and show you?")
    assert compose("call").endswith("Could I set up a call this week to show you?")


@pytest.mark.parametrize("address, city, where", [
    ("100 N Clark St Ste 2", "Chicago", "on N Clark St"),
    ("100 N CLARK ST STE 2", "CHICAGO", "on N Clark St"),
    ("100 N Clark St, Suite 200", "Chicago", "on N Clark St"),
    ("12 W Fake Ave Unit 3B", "Austin", "on W Fake Ave"),
    ("12 W Fake Ave #3", "Austin", "on W Fake Ave"),
    ("12 W Fake Ave Fl 2", "Austin", "on W Fake Ave"),
    ("12 W Fake Ave 2nd Floor", "Austin", "on W Fake Ave"),
    ("12 W FAKE AVE 2ND FL", "Austin", "on W Fake Ave"),
    ("12 W Fake Ave Floor 3", "Austin", "on W Fake Ave"),
    ("12 W Fake Ave Rm 4", "Austin", "on W Fake Ave"),
    ("100-102 Fake Blvd", "Austin", "on Fake Blvd"),
    ("100A Fake Blvd", "Austin", "on Fake Blvd"),
    ("1200 NE FAKE ST", "Seattle", "on NE Fake St"),
    ("200 W 42ND ST", "New York", "on W 42nd St"),
    ("Fake Plaza", "Austin", "on Fake Plaza"),
    ("PO BOX 12", "Austin", "in Austin"),
    ("123", "AUSTIN", "in Austin"),
    ("", "Austin", "in Austin"),
    (None, "San Antonio", "in San Antonio"),
    (None, None, ""),
    ("", "", ""),
])
def test_where_from_address(address, city, where):
    assert outreach.where_for(plan_row(address=address, city=city)) == where


def test_local_is_chicago_metro_only():
    assert outreach.is_local(plan_row(market="Chicago"))
    assert outreach.is_local(plan_row(market=None, metro="Chicago"))
    assert not outreach.is_local(plan_row(market="Austin"))
    assert not outreach.is_local(plan_row(market=None))


def test_variant_is_stable_per_venue_and_both_occur():
    assert outreach.variant_for("TX|78701|1 FAKE ST") == outreach.variant_for(
        "TX|78701|1 FAKE ST")
    picks = {outreach.variant_for(f"TX|78701|{i} FAKE ST") for i in range(40)}
    assert picks == {0, 1}
    # Built on crc32, not Python's per-process hash().
    import zlib
    assert outreach.variant_for("IL|60601|9 FAKE ST") == zlib.crc32(b"IL|60601|9 FAKE ST") % 2
    rows = [plan_row(i, stage="Approved") for i in range(40)]
    openers = {outreach.opener(r) for r in rows}
    assert any("Ran across your page and love what you guys are building" in o
               for o in openers)
    assert any("Love what you guys are putting together" in o for o in openers)
    assert outreach.opener(rows[3]) == outreach.opener(dict(rows[3]))


def test_opener_from_row_uses_where_local_and_platform():
    row = plan_row(7, outreach_method="Email", contact_person="Jane Q Tester",
                   address="7 N Fake St Ste 1", city="Chicago", market="Chicago",
                   stage="Licensed", platforms=["Toast"])
    assert outreach.opener(row) == (
        "Subject: Congrats on Zebra Fake Lounge\n\nHey Jane,\n\n"
        "Congrats on opening Zebra Fake Lounge! Really respect what you guys have built.\n\n"
        "I'm Dylan with Speakeasy. We work with "
        + outreach.name_drops("club_lounge", "Chicago", row["venue_key"]) + " and many more."
        "\n\nI think our table and bottle service booking could be a great touch for your "
        "nights. Happy to run alongside Toast, no need to switch.\n\n"
        "Open to a quick coffee this week or next?\n\nBest,\nDylan")
    call = outreach.opener(dict(row, outreach_method="Call", market="Austin",
                                venue_history=NEW_OWNER))
    assert call.endswith("Could I set up a call this week to show you?")
    assert "Congrats on taking over! Planning any changes to the place?" in call


def test_opener_proof_line_from_settings_only(monkeypatch):
    monkeypatch.setenv("OUTREACH_PROOF_CLUB_LOUNGE", "  Zebra Fake Proof sold out 3 nights.  ")
    proof = "Zebra Fake Proof sold out 3 nights."
    # DM: its own paragraph at the end of message 3.
    dm = compose("dm", "club_lounge", "opening_soon", platform="DICE", variant=0)
    assert dm.split("DM 3 of 3:\n")[1] == (proof_line("club_lounge") + "\n\n"
                                            + DM_THIRD["club_lounge"] + "\n\n" + proof)
    assert "DICE" in dm.split("DM 2 of 3:")[1]
    # Email and call: after the platform line, before the ask.
    email = compose("email", "club_lounge", "just_opened", platform="DICE").split("\n\n")
    assert email[4:7] == [
        DM_THIRD["club_lounge"] + ". Happy to run alongside DICE, no need to switch.", proof,
        "Open to a quick call this week or next?"]
    call = compose("call", "club_lounge", "just_opened").split("\n\n")
    assert call[2:4] == [PRODUCT["club_lounge"] + ".", proof]
    # Another angle's proof is never borrowed.
    assert "Proof" not in compose("call", "bar", "just_opened")


def test_no_dashes_or_semicolons_in_any_rendered_opener():
    combos = itertools.product(("dm", "email", "call"),
                               ("club_lounge", "ticketed", "bar", "restaurant"),
                               ("opening_soon", "just_opened", "new_owner"),
                               (None, "SevenRooms and Toast"), (None, "Jane"),
                               ("", "in Austin", WHERE), (False, True), (0, 1))
    for channel, angle, timing, platform, first, where, local, variant in combos:
        assert_clean(compose(channel, angle, timing, platform, first, where=where,
                             local=local, variant=variant))


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
        "Ibex Fake Lounge",
        "Out Details"]  # nothing new today, but nobody has reached out yet: it stays
    assert [p["order"] for p in plan] == [1, 2, 3, 4, 5, 6]
    assert [p["new_today"] for p in plan] == [True] * 5 + [False]
    assert plan[-1]["header"].endswith("Still to reach")
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
    assert entry["header"] == "Zebra Fake Lounge, Austin | Approved | Hot, tier A | New today"
    assert entry["how"] == "Instagram DM: @zebrafakelounge (Verified 90)"
    assert entry["second"] == "Call"
    assert entry["channel"] == "dm"
    first_line = ("Ran across your page and love what you guys are building on Fake St",
                  "Congrats on Zebra Fake Lounge! Love what you guys are putting together "
                  "on Fake St")
    assert entry["opener"].split("\n\n")[0] == "DM 1 of 3:\nHey Jane!"
    assert entry["opener"].split("\n\n")[1] == \
        first_line[outreach.variant_for("TX|78701|1 FAKE ST")]
    assert "Saw you guys use SevenRooms and was curious" in entry["opener"]
    assert entry["contact_person"] == "Jane Q Tester"
    assert entry["lead_ids"] == "101"
    email = outreach.build_plan([plan_row(outreach_method="Email",
                                          contact_url="mailto:hi@zebrafake.test",
                                          contact_url_text="hi@zebrafake.test",
                                          newly_reachable="Newly reachable")])[0]
    assert email["opener"].startswith("Subject: Congrats on Zebra Fake Lounge\n\nHey there,\n\n"
                                      "Congrats on Zebra Fake Lounge! Love what you guys are "
                                      "building on Fake St.")
    assert email["header"].endswith("| Newly reachable")


def test_empty_plan():
    assert outreach.build_plan([]) == []
    assert outreach.build_plan([plan_row(contact_status="waiting")]) == []
    assert outreach.EMPTY_PLAN == "No reachable venues to contact today."


def test_sole_proprietor_owner_is_greeted_by_first_name():
    # The legal name is the person: it is the company column too.
    [entry] = outreach.build_plan([plan_row(company="Jane Q Tester",
                                            contact_person="Jane Q Tester")])
    assert entry["opener"].startswith("DM 1 of 3:\nHey Jane!\n\n")


def test_fit_bullet_never_mixes_a_google_type_with_another_pitch():
    rooftop = plan_row(business_name="Ibex Fake Rooftop", company=None, google_type="bar",
                       stage="Licensed", platforms=None)
    assert outreach.angle(rooftop) == "club_lounge"
    assert outreach.why(rooftop)[1] == "Rooftop: tables, bottle service and the door"
    # Google's word is used when it fits the pitch.
    assert outreach.why(plan_row(google_type="night_club"))[1] == \
        "Nightclub: tables, bottle service and the door"
    bar = plan_row(priority="B", business_name="Quokka Fake Tavern", company=None,
                   google_type="wine_bar")
    assert outreach.why(bar)[1] == "Wine bar: event tickets and texting regulars"


def test_plan_carries_venues_nobody_has_reached_yet():
    old = plan_row(1, business_name="Okapi Fake Lounge", whats_new=None, newly_reachable=None)
    today = plan_row(2, business_name="Zebra Fake Lounge")
    plan = outreach.build_plan([today], [old, dict(today)])
    assert [(p["name"], p["new_today"]) for p in plan] == [
        ("Zebra Fake Lounge", True), ("Okapi Fake Lounge", False)]
    assert plan[1]["header"].endswith("Still to reach")
    # once marked, it drops off
    assert outreach.build_plan([], [dict(old, review_status="contacted")]) == []


def test_where_keeps_directions_in_capitals_on_mixed_case():
    assert outreach.where_for({"address": "100 Ne 68th St"}) == "on NE 68th St"
    assert outreach.where_for({"address": "5 Fake St"}) == "on Fake St"
