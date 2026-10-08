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
below. To change what an opener says, edit those strings and keep their
{slots} (listed above the constants). Nothing else needs to change.
"""

from __future__ import annotations

import os
import re
import zlib

from . import contact
from . import qualify
from . import stage as stage_mod
from .history import ADDING_PERMIT, NEW_OWNER, NEW_VENUE

# ---------------------------------------------------------------------------
# Opener copy, in the owner's voice.
#
# How to edit: change the words inside the quotes and keep every {slot}.
#   {hi}        the DM greeting: DM_HI, or DM_HI_NAMED when a first name is known
#   {first}     the contact person's first name
#   {venue}     the venue's name
#   {where}     "on N Clark St" from the address, or "in Chicago" without one
#   {product}   the PRODUCT_LINES sentence for the venue, without its period
#   {platform}  the competing platform on their site, like "SevenRooms"
#   {platform_para} / {platform_inline}  PLATFORM_LINE when their site shows
#               a competing platform (as its own paragraph, or inline), else
#               nothing
# "\n\n" is a blank line. No em dashes or semicolons in any of it.
# ---------------------------------------------------------------------------

DM_HI = "Hey hey!"
DM_HI_NAMED = "Hey {first}!"

#: DM (Instagram or Facebook) by timing(). opening_soon has two variants:
#: each venue always gets the same one (variant_for), picked from its key.
DM_OPENERS = {
    "opening_soon": (
        ("{hi} Saw {venue} is opening {where}. Congrats!\n\n"
         "I'm Dylan with Speakeasy. {product}.\n\n"
         "{platform_para}When are you guys opening? Would love to show you what we do "
         "before then."),
        ("{hi} Congrats on {venue}. Saw you're opening {where}.\n\n"
         "I'm Dylan, I work at Speakeasy. {product}.\n\n"
         "{platform_para}When's opening night? Would love to help you guys launch."),
    ),
    "just_opened": (
        ("{hi} Congrats on opening {venue}.\n\n"
         "How have the first few weeks been?\n\n"
         "I'm Dylan with Speakeasy. {product}. {platform_inline}Would love to show you if "
         "you're open to it."),
    ),
    "new_owner": (
        ("{hi} Congrats on taking over {venue}.\n\n"
         "Planning any changes to the place?\n\n"
         "I'm Dylan with Speakeasy. {product}. {platform_inline}Would love to show you what "
         "we do."),
    ),
}
#: The first DM line for opening_soon when there is no {where}, per variant.
DM_OPENING_SOON_NO_WHERE = ("{hi} Saw {venue} is opening soon. Congrats!",
                            "{hi} Congrats on {venue}.")

#: Product sentence, picked by angle(). No final period: the openers add it.
PRODUCT_LINES = {
    "club_lounge": "We handle tables, bottle service and the door for clubs",
    "ticketed": "We do ticketing and the door for venues like yours",
    "bar": "We help bars run ticketed events and text their regulars",
    "restaurant": "We handle reservations, tables and POS for restaurants",
}

#: Only when the venue's site shows a competing platform (contact.PLATFORMS).
PLATFORM_LINE = "Saw you guys use {platform}. Happy to show you how we compare if you're curious."

#: Email (and the website's contact form), one paragraph per line.
EMAIL_SUBJECT = "Congrats on {venue}"
GREETING_EMAIL = "Hey there,"
GREETING_EMAIL_NAMED = "Hey {first},"
EMAIL_FIRST_LINES = {
    "opening_soon": "Congrats on {venue}! Saw you're opening {where}.",
    "just_opened": "Congrats on opening {venue}!",
    "new_owner": "Congrats on taking over {venue}!",
}
EMAIL_FIRST_LINE_NO_WHERE = "Congrats on {venue}!"
INTRO_EMAIL = "I'm Dylan, Head of Growth at Speakeasy. {product}."
#: The ask, by (local, timing): local means the venue is in LOCAL_METROS.
EMAIL_ASKS = {
    (True, "opening_soon"): ("Would love to grab a coffee or stop by before you open. "
                             "Happy to work around your schedule."),
    (True, "other"): ("Would love to grab a coffee or stop by sometime soon. "
                      "Happy to work around your schedule."),
    (False, "opening_soon"): ("Would love to hop on a call before you open. "
                              "Happy to work around your schedule."),
    (False, "other"): ("Would love to hop on a call sometime soon. "
                       "Happy to work around your schedule."),
}
EMAIL_QUESTIONS = {
    "opening_soon": "When are you guys opening?",
    "just_opened": "How have the first few weeks been?",
    "new_owner": "Planning any changes to the place?",
}
EMAIL_SIGNOFF = "Best,\nDylan"

#: Call script, one line each, blank line between.
CALL_OPEN = "Hey, this is Dylan from Speakeasy. Is the owner or GM around?"
CALL_TIMING_LINES = {
    "opening_soon": "Congrats on the new spot! When are you guys opening?",
    "just_opened": "Congrats on opening! How have the first few weeks been?",
    "new_owner": "Congrats on taking over! Planning any changes to the place?",
}
CALL_PRODUCT = "{product}."
CALL_PLATFORM_LINE = "I know you're on {platform} right now. Happy to show you how we compare."
CALL_ASKS = {
    True: "Could I swing by this week and show you?",
    False: "Could I set up a call this week to show you?",
}

#: Metros (metros.py names) where the owner can stop by in person.
LOCAL_METROS = {"Chicago"}

#: Optional proof line per angle, one sentence, from a GitHub variable (not a
#: secret). Unset by default; when set it is its own paragraph: in a DM right
#: after the paragraph with the product sentence, in an email or call script
#: after the platform line. Never written here: the owner supplies real proof.
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


#: Suite, unit, floor and the like: everything from here on is dropped.
_UNIT = re.compile(r"\s*(?:,|#|\b(?:STE|SUITE|UNIT|APT|APARTMENT|FL|FLR|FLOOR|RM|ROOM|"
                   r"BLDG|BUILDING|SPC|SPACE|LEVEL|LVL|\d+(?:ST|ND|RD|TH)\s+(?:FL|FLR|FLOOR))"
                   r"\b\.?).*$", re.IGNORECASE)
#: A house number: 100, 100A, 100-102, 100 1/2.
_HOUSE_NUMBER = re.compile(r"^\d+[A-Z]?(?:\s*-\s*\d+[A-Z]?)?(?:\s+1/2)?\s+", re.IGNORECASE)
_PO_BOX = re.compile(r"^P\.?\s*O\.?\s*BOX\b", re.IGNORECASE)
_COMPASS = {"Ne": "NE", "Nw": "NW", "Se": "SE", "Sw": "SW"}


def _title_case(text: str) -> str:
    """Title-case ALL-CAPS text ("42ND ST" gives "42nd St"); mixed case is kept,
    except compass directions, which are always capitals ("Ne" gives "NE")."""
    if text != text.upper():
        return " ".join(_COMPASS.get(w.title(), w) if len(w) == 2 else w for w in text.split())
    text = re.sub(r"(\d)(St|Nd|Rd|Th)\b", lambda m: m.group(1) + m.group(2).lower(),
                  text.title())
    return " ".join(_COMPASS.get(w, w) for w in text.split())


def where_for(row: dict) -> str:
    """{where}: "on <street>" from the venue's address with the house number
    and any suite, unit or floor dropped ("100 N Clark St Ste 2" gives "on N
    Clark St"), else "in <City>", else ""."""
    address = " ".join(str(row.get("address") or "").split())
    street = "" if _PO_BOX.match(address) else _UNIT.sub("", _HOUSE_NUMBER.sub("", address))
    if re.search(r"[A-Za-z]{2}", street):
        return "on " + _title_case(street.strip())
    city = " ".join(str(row.get("city") or "").split())
    return "in " + _title_case(city) if city else ""


def is_local(row: dict) -> bool:
    """Is the venue in a metro the owner can visit (LOCAL_METROS)?"""
    return (row.get("market") or row.get("metro")) in LOCAL_METROS


def variant_for(venue_key: str | None) -> int:
    """0 or 1, the same for a venue every day: crc32 of its key (Python's
    hash() changes between runs)."""
    return zlib.crc32(str(venue_key or "").encode("utf-8")) % 2


def _with_proof(paragraphs: list[str], after: int, angle: str) -> list[str]:
    """Insert the owner's proof sentence (if set) as its own paragraph."""
    extra = proof(angle)
    return paragraphs[:after + 1] + [extra] + paragraphs[after + 1:] if extra else paragraphs


def compose_opener(channel: str, *, venue: str, angle: str, timing: str,
                   platform: str | None = None, first: str | None = None,
                   where: str = "", local: bool = False, variant: int = 0) -> str:
    """The opener text for one channel (dm, email or call). Paragraphs are
    separated by a blank line. An email starts with "Subject: ...".
    `variant` (0 or 1) picks the opening_soon DM wording, `where` is
    where_for() and `local` is is_local()."""
    product = PRODUCT_LINES[angle]
    platform_line = PLATFORM_LINE.format(platform=platform) if platform else ""
    if channel == EMAIL:
        first_line = (EMAIL_FIRST_LINE_NO_WHERE if timing == "opening_soon" and not where
                      else EMAIL_FIRST_LINES[timing])
        paragraphs = [
            "Subject: " + EMAIL_SUBJECT.format(venue=venue),
            GREETING_EMAIL_NAMED.format(first=first) if first else GREETING_EMAIL,
            first_line.format(venue=venue, where=where),
            INTRO_EMAIL.format(product=product), *([platform_line] if platform else [])]
        paragraphs = _with_proof(paragraphs, len(paragraphs) - 1, angle)
        soon = "opening_soon" if timing == "opening_soon" else "other"
        paragraphs += [EMAIL_ASKS[(bool(local), soon)], EMAIL_QUESTIONS[timing],
                       EMAIL_SIGNOFF]
        return "\n\n".join(paragraphs)
    if channel == CALL:
        paragraphs = [CALL_OPEN, CALL_TIMING_LINES[timing], CALL_PRODUCT.format(product=product),
                      *([CALL_PLATFORM_LINE.format(platform=platform)] if platform else [])]
        paragraphs = _with_proof(paragraphs, len(paragraphs) - 1, angle)
        return "\n\n".join(paragraphs + [CALL_ASKS[bool(local)]])
    variants = DM_OPENERS[timing]
    pick = variant % len(variants)
    paragraphs = variants[pick].split("\n\n")
    if timing == "opening_soon" and not where:
        paragraphs[0] = DM_OPENING_SOON_NO_WHERE[pick]
    after = next(i for i, p in enumerate(paragraphs) if "{product}" in p)
    slots = {"hi": DM_HI_NAMED.format(first=first) if first else DM_HI, "venue": venue,
             "where": where, "product": product,
             "platform_para": platform_line + "\n\n" if platform else "",
             "platform_inline": platform_line + " " if platform else ""}
    paragraphs = _with_proof([p.format(**slots) for p in paragraphs], after, angle)
    return "\n\n".join(paragraphs)


def opener(row: dict) -> str:
    """The opener for this venue's best way to reach it."""
    others = competitors(row)
    return compose_opener(
        channel_for(row.get("outreach_method")), venue=row.get("business_name") or "",
        angle=angle(row), timing=timing(row),
        platform=_join(others[:2]) if others else None,
        # Only the trade name: a sole proprietor's company name is the person.
        first=first_name(row.get("contact_person"), (row.get("business_name"),)),
        where=where_for(row), local=is_local(row),
        variant=variant_for(row.get("venue_key") or row.get("business_name")))


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
