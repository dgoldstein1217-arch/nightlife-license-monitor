"""The daily attack plan: which venues to reach, why, how, and a copy-ready
opener for each.

Built from one day's sheet rows (leadsheet.load_rows) once the contact
lookup (contact.py) has run. Pure: no database, no network, no logging. It
never contacts anyone: the owner reads the plan in his own email and on the
workbook's Today's plan tab, and sends every message himself.

Who is in the plan (build_plan): every open venue that is reachable (a
Verified or Likely way to reach it) and that nobody has reached out to yet.
A venue stays on the plan day after day until the owner marks it (licmon
review) or the team works it in Attio. Venues that are new today (a new
filing, a stage advance or newly reachable) are marked New today and come
first; the rest say Still to reach. Left out (left_out): adult venues,
venues only adding a permit, venues whose site links Speakeasy (already a
client), venues the team already worked in Attio (list Status Contacted,
Not a fit or Moved to Targets, or a Target with outreach under way), and
venues already reviewed as contacted, replied, won, rejected or snoozed.
Order: new today first, then not open yet, then Hot, then lead score.

Copy: the opener wording is the owner's own and lives in the constants
below. To change what an opener says, edit those strings and keep the
{venue}, {first} and {platform} slots. Nothing else needs to change.
"""

from __future__ import annotations

import os
import re

from . import contact
from . import qualify
from . import stage as stage_mod
from .history import ADDING_PERMIT, NEW_OWNER, NEW_VENUE

# ---------------------------------------------------------------------------
# Opener copy, in the owner's voice. Edit the words; keep the {slots}.
# ---------------------------------------------------------------------------

GREETING_DM = "Hey hey,"
GREETING_DM_NAMED = "Hey {first},"
GREETING_EMAIL = "Hey {venue} team,"
GREETING_EMAIL_NAMED = "Hey {first},"

#: Timing line, picked by timing().
TIMING_LINES = {
    "opening_soon": "Congrats on the upcoming opening of {venue}.",
    "just_opened": "Congrats on opening {venue}.",
    "new_owner": "Congrats on taking over {venue}.",
}

INTRO_DM = "I'm Dylan, I run growth at Speakeasy."  # DMs and calls
INTRO_EMAIL = "I'm Dylan, Head of Growth at Speakeasy."

#: Product line, picked by angle().
PRODUCT_LINES = {
    "club_lounge": ("We run tables, bottle service and the door for clubs and lounges, "
                    "all in one place."),
    "ticketed": ("We run ticketing, guest lists and the door for venues like yours, "
                 "all in one place."),
    "bar": "We help bars sell tickets to events and text their regulars, all from one place.",
    "restaurant": "We run reservations, tables and POS in one place for spots like yours.",
}

#: Only when the venue's site shows a competing platform (contact.PLATFORMS).
PLATFORM_LINE = "Saw you're on {platform}. Happy to show you a side by side."

#: The ask: (first sentence, question). DMs put both on one line; emails put
#: EMAIL_FLEX between them.
ASKS = {
    "opening_soon": ("Would love to grab 15 minutes before you open.",
                     "What does your week look like?"),
    "other": ("Would love to grab 15 minutes this week or next.", "What works for you?"),
}
EMAIL_FLEX = "Happy to work around your schedule."
EMAIL_SUBJECT = "{venue} + Speakeasy"
EMAIL_SIGNOFF = "Best,\nDylan"

CALL_OPEN = "Hi, this is Dylan with Speakeasy. Is the owner or GM around?"
CALL_ASKS = {
    "opening_soon": "Could I get 15 minutes on your calendar before you open?",
    "other": "Could I get 15 minutes on your calendar this week?",
}

#: Optional proof line per angle, one sentence, from a GitHub variable (not a
#: secret). Unset by default; when set it goes on its own line right after
#: the product line. Never written here: the owner supplies real proof.
PROOF_ENV = {
    "club_lounge": "OUTREACH_PROOF_CLUB_LOUNGE",
    "ticketed": "OUTREACH_PROOF_TICKETED",
    "bar": "OUTREACH_PROOF_BAR",
    "restaurant": "OUTREACH_PROOF_RESTAURANT",
}

EMPTY_PLAN = "No reachable venues to contact today."
PLAN_TITLE = "Today's plan"

DM, EMAIL, CALL = "dm", "email", "call"
CHANNEL_TEXT = {DM: "DM", EMAIL: "Email", CALL: "Call script"}

# ---------------------------------------------------------------------------
# Why reach out: plain bullets, always from the data
# ---------------------------------------------------------------------------

_WHY_SOON = {
    stage_mod.APPROVED: "License approved, not open yet: pitch before launch",
    stage_mod.IN_REVIEW: "License in review, not open yet: pitch before launch",
    stage_mod.RECEIVED: "License just filed, not open yet: pitch before launch",
    stage_mod.LICENSED: "Licensed, not open yet: pitch before launch",
}
_WHY_SOON_DEFAULT = "Not open yet: pitch before launch"
_WHY_JUST_LICENSED = "Just licensed: opening now"
_WHY_NOW_OPEN = "Says now open: reach them while they set up"
_WHY_NEW_OWNER = "New owner taking over"
_WHY_NEW_VENUE = "New venue: no license at this address before"
_WHY_GREENFIELD = "No ticketing or reservations found on their site: greenfield"
_WHY_SWITCH = "Uses {platforms}: switch pitch"
_WHY_WORDS = "Their site or Instagram says {signal}"
#: What each angle means for Speakeasy, after the kind of venue.
_WHY_FIT = {
    "club_lounge": "tables, bottle service and the door",
    "ticketed": "ticketing, guest lists and the door",
    "bar": "event tickets and texting regulars",
    "restaurant": "reservations, tables and POS",
}
_KIND = {"club_lounge": "Nightclub or lounge", "ticketed": "Ticketed venue", "bar": "Bar",
         "restaurant": "Restaurant"}
#: Nightlife license keys (qualify.NIGHTLIFE_LICENSE_POINTS) in plain words.
_LICENSE_WORDS = {
    "late_hours": "late hours", "ppa": "public place of amusement",
    "public_premises": "public premises bar", "music_venue": "music venue",
    "nightclub_cabaret": "cabaret or nightclub", "theater": "theater",
    "sports_venue": "sports venue", "event_venue": "event center",
    "full_liquor_bar": "full liquor bar",
}
MAX_WHY = 4

#: Ticketed words that read as a club or lounge rather than a ticketed venue.
_CLUBLIKE = re.compile(r"\b(ROOFTOP|SUPPER CLUB|DAY ?CLUB|BEACH CLUB|CABARET)\b")
_TICKETED_LICENSE_KEYS = {"music_venue", "theater", "sports_venue", "event_venue"}
_TICKETED_KIND = {"COMEDY": "Comedy venue", "IMPROV": "Comedy venue",
                  "CONCERT": "Concert venue", "CONCERTS": "Concert venue",
                  "LIVE MUSIC": "Live music venue", "MUSIC HALL": "Music hall",
                  "MUSIC VENUE": "Music venue", "JAZZ CLUB": "Jazz club"}

# ---------------------------------------------------------------------------
# {first}: a natural person's first name, or nothing
# ---------------------------------------------------------------------------

#: A name with any of these words is a company, not a person.
_ENTITY_WORDS = {"LLC", "INC", "CORP", "LP", "LTD", "CO", "COMPANY", "GROUP", "HOLDINGS",
                 "PARTNERS", "TRUST", "ENTERPRISES", "LLP", "PLLC", "PC", "CORPORATION",
                 "INCORPORATED", "LIMITED", "ASSOCIATES", "VENTURES", "HOSPITALITY",
                 "MANAGEMENT", "PROPERTIES", "INVESTMENTS", "CAPITAL", "THE", "DBA",
                 "AND", "FAMILY", "ESTATE"}
_TITLES = {"MR", "MRS", "MS", "MISS", "DR", "JR", "SR", "II", "III", "IV"}


def first_name(person: str | None, business_names=()) -> str | None:
    """The first name of a natural person named on the filing, title-cased,
    or None: for companies (LLC, Inc, Corp, LP, Ltd, Co, Company, Group,
    Holdings, Partners, Trust, Enterprises and the like), names with digits,
    a single word, venue words (Bar, Lounge ...), or a word shared with the
    venue's own name. "Tester, Jane Q" gives "Jane"; initials are skipped."""
    text = " ".join(str(person or "").split())
    if not text or re.search(r"\d", text):
        return None
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", text.upper())
    if len(words) < 2 or set(words) & _ENTITY_WORDS or "&" in text:
        return None
    upper = " ".join(words)
    if any(p.search(upper) for p in (qualify.POSITIVE_WORDS, qualify.TICKETED_WORDS,
                                     qualify.NIGHTCLUB_WORDS, qualify.RESTAURANT_WORDS,
                                     qualify.BAR_VENUE_WORDS)):
        return None
    venue_words = {w for name in business_names if name
                   for w in re.findall(r"[A-Z][A-Z'\-]+", str(name).upper()) if len(w) > 2}
    if set(words) & venue_words:
        return None
    if "," in text:  # "Tester, Jane Q": the surname comes first
        given = re.findall(r"[A-Za-z][A-Za-z'\-]*", text.split(",", 1)[1].upper())
        first = next((w for w in given if len(w) > 1 and w not in _TITLES), None)
        return first.title() if first else None
    for i, word in enumerate(words):
        if len(word) > 1 and word not in _TITLES:
            # A first name needs a surname after it ("J Tester" has none).
            return word.title() if i < len(words) - 1 else None
    return None


# ---------------------------------------------------------------------------
# Channel, timing, angle
# ---------------------------------------------------------------------------

def channel_for(method: str | None) -> str:
    """dm, email or call, from contact.METHOD_RULES' method names."""
    method = method or ""
    if method.startswith("Call"):
        return CALL
    if method.startswith(("Email", "Website contact form")):
        return EMAIL
    return DM


def timing(row: dict) -> str:
    """new_owner, opening_soon or just_opened.

    New owner (venue history) always says taking over. Otherwise "now open"
    on the venue's site or Instagram means it opened; an opening-soon signal
    (contact.py) or a stage before Licensed means not open yet; Licensed
    means just opened."""
    if row.get("venue_history") == NEW_OWNER:
        return "new_owner"
    if row.get("opening_signal") == contact.NOW_OPEN:
        return "just_opened"
    if row.get("opening_soon"):
        return "opening_soon"
    if row.get("stage") == stage_mod.LICENSED:
        return "just_opened"
    return "opening_soon"


def _names(row: dict) -> str:
    return " ".join(str(row.get(k) or "") for k in ("business_name", "company")).upper()


def _strong_ticketed(names: str):
    found = qualify.TICKETED_WORDS.search(_CLUBLIKE.sub(" ", names))
    return None if found is None or qualify.NOT_TICKETED.search(names) else found


def angle(row: dict) -> str:
    """club_lounge, ticketed, bar or restaurant: which product line fits.

    Tier A: Google's type when it is a nightclub or ticketed type, else the
    name (a comedy, theater or concert word is ticketed; a club, lounge or
    rooftop word is a club), else the license. Tier B: bar, or restaurant
    when Google lists a restaurant. Tier C: restaurant, or bar when Google
    lists a bar. The tier is the one shown (after contact.google_tier)."""
    tier = row.get("priority")
    gtype = row.get("google_type")
    keys = set(row.get("license_keys") or ())
    if tier == "A":
        if gtype in ("night_club", "dance_hall"):
            return "club_lounge"
        if gtype in contact.GOOGLE_A_TYPES:
            return "ticketed"
        names = _names(row)
        if _strong_ticketed(names) or qualify.CONCESSIONAIRES.search(names):
            return "ticketed"
        club = qualify.NIGHTCLUB_WORDS.search(names) and not qualify.NOT_NIGHTCLUB.search(names)
        if club or _CLUBLIKE.search(names) or "nightclub_cabaret" in keys:
            return "club_lounge"
        return "ticketed" if keys & _TICKETED_LICENSE_KEYS else "club_lounge"
    if tier == "B":
        return "restaurant" if contact.is_food_type(gtype) else "bar"
    return "bar" if gtype in contact.GOOGLE_BAR_TYPES else "restaurant"


def _google_fits(gtype: str | None, chosen: str) -> bool:
    """Does Google's type say the same kind of place as the pitch?"""
    if chosen == "club_lounge":
        return gtype in ("night_club", "dance_hall")
    if chosen == "ticketed":
        return gtype in contact.GOOGLE_A_TYPES
    if chosen == "bar":
        return gtype in contact.GOOGLE_BAR_TYPES
    return contact.is_food_type(gtype)


def _kind(row: dict, chosen: str) -> str:
    """The kind of venue in plain words, for the fit bullet: Google's word
    when it agrees with the pitch, else the venue's own name."""
    if _google_fits(row.get("google_type"), chosen):
        label = contact.google_label(row.get("google_type"))
        return label[:1].upper() + label[1:]
    names = _names(row)
    if chosen == "club_lounge":
        club = qualify.NIGHTCLUB_WORDS.search(names)
        if club and "LOUNGE" in club.group(0):
            return "Lounge"
        if club:
            return "Nightclub"
        word = _CLUBLIKE.search(names)
        return " ".join(word.group(0).split()).title() if word else _KIND[chosen]
    if chosen == "ticketed":
        word = _strong_ticketed(names)
        if word:
            text = " ".join(word.group(0).split())
            return _TICKETED_KIND.get(text, text.title())
    return _KIND[chosen]


# ---------------------------------------------------------------------------
# Why, how, opener
# ---------------------------------------------------------------------------

def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def competitors(row: dict) -> list[str]:
    """Platforms on the venue's site other than Speakeasy."""
    return [p for p in row.get("platforms") or [] if p != contact.SPEAKEASY]


def why(row: dict) -> list[str]:
    """2 to 4 short reasons, in this order: timing, venue fit, the platform
    on their site, an opening-soon signal, nightlife licenses, venue
    history. Each one comes from a field on the row; nothing is guessed."""
    when = timing(row)
    stage = row.get("stage")
    if when == "new_owner":
        bullets = [_WHY_NEW_OWNER]
    elif when == "opening_soon":
        bullets = [_WHY_SOON.get(stage, _WHY_SOON_DEFAULT)]
    else:
        bullets = [_WHY_JUST_LICENSED if row.get("opening_signal") != contact.NOW_OPEN
                   and stage == stage_mod.LICENSED else _WHY_NOW_OPEN]
    chosen = angle(row)
    bullets.append(f"{_kind(row, chosen)}: {_WHY_FIT[chosen]}")
    platforms = row.get("platforms")
    if platforms is not None:
        others = competitors(row)
        bullets.append(_WHY_SWITCH.format(platforms=_join(others)) if others
                       else _WHY_GREENFIELD)
    signal = row.get("opening_signal")
    if row.get("opening_soon") and signal:
        bullets.append(signal[:1].upper() + signal[1:]
                       if signal in (contact.NO_REVIEWS, contact.NOT_OPEN_ON_GOOGLE)
                       else _WHY_WORDS.format(signal=signal))
    licenses = [_LICENSE_WORDS[k] for k in row.get("license_keys") or ()
                if k in _LICENSE_WORDS][:2]
    if licenses:
        text = _join(licenses)
        bullets.append(text[:1].upper() + text[1:] + (" licenses" if len(licenses) > 1
                                                       else " license"))
    if row.get("venue_history") == NEW_VENUE:
        bullets.append(_WHY_NEW_VENUE)
    return bullets[:MAX_WHY]


def proof(chosen: str) -> str | None:
    """The owner's proof sentence for this angle, if he set one."""
    value = " ".join(os.environ.get(PROOF_ENV[chosen], "").split())
    return value or None


def compose_opener(channel: str, *, venue: str, angle: str, timing: str,
                   platform: str | None = None, first: str | None = None) -> str:
    """The opener text for one channel (dm, email or call). Lines are
    separated by a blank line. An email starts with "Subject: ..."."""
    soon = timing == "opening_soon"
    ask_first, ask_question = ASKS["opening_soon" if soon else "other"]
    product = PRODUCT_LINES[angle]
    extra = [p for p in (proof(angle),) if p]
    if platform:
        extra.append(PLATFORM_LINE.format(platform=platform))
    when = TIMING_LINES[timing].format(venue=venue)
    if channel == EMAIL:
        greeting = (GREETING_EMAIL_NAMED.format(first=first) if first
                    else GREETING_EMAIL.format(venue=venue))
        lines = ["Subject: " + EMAIL_SUBJECT.format(venue=venue), greeting, when,
                 INTRO_EMAIL, product, *extra, ask_first, EMAIL_FLEX, ask_question,
                 EMAIL_SIGNOFF]
    elif channel == CALL:
        lines = [CALL_OPEN, when, product, *extra,
                 CALL_ASKS["opening_soon" if soon else "other"]]
    else:
        greeting = GREETING_DM_NAMED.format(first=first) if first else GREETING_DM
        lines = [greeting, when, f"{INTRO_DM} {product}", *extra,
                 f"{ask_first} {ask_question}"]
    return "\n\n".join(lines)


def opener(row: dict) -> str:
    """The opener for this venue's best way to reach it."""
    others = competitors(row)
    return compose_opener(
        channel_for(row.get("outreach_method")), venue=row.get("business_name") or "",
        angle=angle(row), timing=timing(row),
        platform=_join(others[:2]) if others else None,
        # Only the trade name: a sole proprietor's company name is the person.
        first=first_name(row.get("contact_person"), (row.get("business_name"),)))


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

#: Review statuses that mean the owner has already handled the venue.
REVIEWED = ("contacted", "replied", "won", "rejected", "snoozed")
_NEWS = ("New filing", "Stage advanced")  # leadsheet.NEW_FILING, STAGE_ADVANCED


def left_out(row: dict) -> str | None:
    """Why a sheet row is not in today's plan, or None when it is in."""
    if row.get("contact_status") != contact.REACHABLE:
        return "not reachable"
    if row.get("adult"):
        return "adult venue"
    if row.get("venue_history") == ADDING_PERMIT:
        return "adding a permit"
    if row.get("pipeline"):
        return row["pipeline"]  # Already on Speakeasy, In Attio: ...
    if row.get("on_speakeasy"):
        return "Already on Speakeasy"
    if row.get("review_status") in REVIEWED:
        return f"reviewed: {row['review_status']}"
    return None


def tier_label(row: dict) -> str:
    tier = row.get("priority") or "C"
    return f"Hot, tier {tier}" if row.get("hot") else f"Tier {tier}"


def is_new_today(row: dict) -> bool:
    """A row from the day's own sheet that is a new filing, a stage advance
    or newly reachable."""
    return row.get("whats_new") in _NEWS or bool(row.get("newly_reachable"))


def plan_key(row: dict, new_today: bool = False):
    """New today first, then newly reachable, then not open yet, then Hot,
    then lead score."""
    return (not new_today, not row.get("newly_reachable"), timing(row) != "opening_soon",
            not row.get("hot"), -(row.get("lead_score") or 0), row.get("business_name") or "")


def entry(row: dict, order: int, new_today: bool = False) -> dict:
    """One plan entry: everything the email block and the plan tab show."""
    method = row.get("outreach_method") or ""
    contact_text = row.get("contact_url_text") or row.get("contact_url") or ""
    how = method + (f": {contact_text}" if contact_text else "")
    if row.get("confidence"):
        how += f" ({row['confidence']})"
    header = " | ".join(p for p in (
        ", ".join(p for p in (row.get("business_name"), row.get("city")) if p),
        row.get("stage"), tier_label(row),
        row.get("newly_reachable") or ("New today" if new_today else "Still to reach")) if p)
    return {
        "order": order, "name": row.get("business_name"), "city": row.get("city"),
        "stage": row.get("stage"), "tier": tier_label(row), "header": header,
        "why": why(row), "method": method, "contact": contact_text,
        "contact_url": row.get("contact_url"), "confidence": row.get("confidence"),
        "how": how, "second": row.get("outreach_second") or "",
        "channel": channel_for(method), "opener": opener(row),
        "contact_person": row.get("contact_person") or "",
        "current_platform": row.get("current_platform") or "",
        "opening_text": row.get("opening_text") or "",
        "newly_reachable": bool(row.get("newly_reachable")), "new_today": new_today,
        "lead_ids": row.get("lead_ids") or "", "venue_key": row.get("venue_key"),
    }


def build_plan(rows: list[dict], carry: list[dict] = ()) -> list[dict]:
    """Every venue for today's plan (no cap), best first, once each. `rows`
    is the day's sheet; `carry` is every open lead (leadsheet.load_rows with
    open_only), so venues from past days stay on the plan until someone
    reaches out to them."""
    today = [(row, is_new_today(row)) for row in rows]
    keys = {row.get("venue_key") for row in rows}
    older = [(row, False) for row in carry if row.get("venue_key") not in keys]
    picked: dict[str, tuple[dict, bool]] = {}
    for row, new in sorted(today + older, key=lambda rn: plan_key(*rn)):
        if left_out(row) is None:
            picked.setdefault(row.get("venue_key") or row.get("business_name"), (row, new))
    return [entry(row, n, new) for n, (row, new) in enumerate(picked.values(), 1)]
