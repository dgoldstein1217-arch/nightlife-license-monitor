"""Venue history: has this address been licensed before, and by whom?

A filing can look brand new when the venue has been open for years under
another company, or when the same company is only adding a permit. Every
qualified lead gets one of four labels:

  New venue        no prior license at the address, or every prior one
                   ended more than RECENT_DAYS (about two years) ago
  New owner        a current or recent license at the address is held by a
                   DIFFERENT owner (the venue is changing hands)
  Adding a permit  the SAME owner already holds a current or recent license
                   there (late hours, a new class, a relicense)
  Unknown          the source cannot check (no public license list, or no
                   street number or ZIP to match on)

Where a state's own filing type already says it (Washington ASSUMPTION,
Chicago expansion filings), the source uses that directly. Otherwise each
Source compares the filing with that state's list of existing licenses
(``Source.venue_history`` in sources/base.py). This module holds the shared
rules: address matching, owner-name matching and the classification.

Address match rule (``same_premises``): same house number (a hyphenated
number such as 31-01 is one token), same street after abbreviating the usual
words (Street -> ST, North -> N ...) and the same ZIP5 (or the same city
when a source has no ZIP). The street type may be missing on one side
("216 Corinth" matches "216 Corinth St"). Suites must agree, and a suite on
one side only is a different premises: a shopping center's anchor store at
"4301 Fake Dr" is not the tenant in "4301 Fake Dr Ste B108". Floors (Floor
1, 1ST, a bare one or two digit number after the street, basement) are
ignored, because lists write them differently. Anything unclear is not a
match: a miss only means the lead keeps looking new, which is how it looked
before this check existed.

Owner match rule (``same_owner``): the source's own owner id when both sides
have one (Texas master file id, Chicago account number), else the legal
names after dropping LLC, INC, CORP, CO, LTD, THE and punctuation. Trade
names (DBAs) are never compared: a new owner often keeps the old sign.

Privacy: other businesses' owner names are compared in memory only. Nothing
but the label, a count of prior licenses and the earliest prior issue date
is stored, and logs carry counts only.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

from .models import _ADDR_WORDS

log = logging.getLogger("licmon")

NEW_VENUE = "New venue"
NEW_OWNER = "New owner"
ADDING_PERMIT = "Adding a permit"
UNKNOWN = "Unknown"

#: Best first. A venue with several filings shows the best one (daily_leads
#: in schema.sql uses the same order).
LABELS = (NEW_VENUE, UNKNOWN, NEW_OWNER, ADDING_PERMIT)
#: Labels that mean the venue already exists (the Existing venues tab).
EXISTING = (NEW_OWNER, ADDING_PERMIT)

#: A prior license that ended within this many days still counts.
RECENT_DAYS = 730

#: Lead score change per label (qualify.apply_history).
SCORE_ADJUST = {NEW_OWNER: -10, ADDING_PERMIT: -15}


@dataclass
class History:
    label: str = UNKNOWN
    prior_licenses: int | None = None  # prior licenses found at the address
    prior_since: date | None = None  # earliest original issue date among them


@dataclass
class Prior:
    """One existing license at the address, from a source's own license list.
    Held in memory only; never stored or logged."""

    license_id: str | None
    owner: str | None
    owner_id: str | None = None
    active: bool = False
    issued: date | None = None
    ended: date | None = None


def best(labels) -> str | None:
    """The best label of several (LABELS order); None if none."""
    found = [lab for lab in labels if lab in LABELS]
    return min(found, key=LABELS.index) if found else None


# ---------------------------------------------------------------------------
# Owner names
# ---------------------------------------------------------------------------

_ENTITY_WORDS = {"LLC", "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY",
                 "LTD", "LIMITED", "LP", "LLP", "PLLC", "PC", "PLC", "LC", "THE", "DBA"}


def normalize_owner(name: str | None) -> str | None:
    """Legal name for comparison: upper case, & as AND, no punctuation, no
    entity words. "Farace Beverages, L.L.C." -> "FARACE BEVERAGES"."""
    text = (name or "").upper().replace("&", " AND ").replace("'", "")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    text = re.sub(r"\bL L (C|P)\b", r"LL\1", text)
    text = re.sub(r"\b(I N C|L P|P C)\b", lambda m: m.group(0).replace(" ", ""), text)
    words = [w for w in text.split() if w not in _ENTITY_WORDS]
    return " ".join(words) or None


def same_owner(name_a: str | None, id_a: str | None,
               name_b: str | None, id_b: str | None) -> bool:
    if id_a and id_b and str(id_a) == str(id_b):
        return True
    a, b = normalize_owner(name_a), normalize_owner(name_b)
    return bool(a and a == b)


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------

_MORE_WORDS = {
    "AV": "AVE", "STR": "ST", "PLAZA": "PLZ", "TERRACE": "TER", "TRAIL": "TRL",
    "CIRCLE": "CIR", "SQUARE": "SQ", "ALLEY": "ALY", "TURNPIKE": "TPKE",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
    "FLOOR": "FL", "FLR": "FL", "ROOM": "RM", "APARTMENT": "APT", "BUILDING": "BLDG",
    "SPACE": "SPC", "LEVEL": "LVL", "BASEMENT": "BSMT",
}
_WORDS = {**_ADDR_WORDS, **_MORE_WORDS}
_WORDS.pop("#", None)
_SUFFIXES = {"ST", "AVE", "BLVD", "RD", "DR", "LN", "PL", "CT", "PKWY", "HWY", "EXPY",
             "FWY", "WAY", "TER", "TRL", "CIR", "SQ", "PLZ", "PIKE", "ROW", "WALK",
             "LOOP", "XING", "ALY", "TPKE", "PATH"}
_DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}
_UNIT_WORDS = {"STE", "APT", "FL", "RM", "BLDG", "SPC", "LVL", "BSMT", "LOT", "NO",
               "#", "UNIT", "SUITE"}
_FLOOR_WORDS = {"FL", "LVL"}
_ORDINAL = re.compile(r"^(\d+)(ST|ND|RD|TH)$")


def _suite(tokens: list[str]) -> str:
    """The suite part of the unit tokens; floors dropped (see module doc)."""
    out, floor_next, suite_next = [], False, False
    for tok in tokens:
        if floor_next:
            floor_next = False
            continue
        if tok in _FLOOR_WORDS:
            floor_next = True  # "FL 2"
            continue
        if tok == "BSMT" or _ORDINAL.match(tok):
            continue
        if tok in _UNIT_WORDS:
            suite_next = True  # "STE 12", "# 4"
            continue
        if tok.isdigit() and len(tok) <= 2 and not suite_next and not out:
            continue  # a bare "... AVE 1" is a floor
        out.append(tok)
        suite_next = False
    return " ".join(out)


@dataclass(frozen=True)
class Address:
    number: str
    street: tuple[str, ...]  # name words, street type and trailing direction
    unit: str  # the suite, "" when none (floors dropped)

    @property
    def street_no_type(self) -> tuple[str, ...]:
        return tuple(w for w in self.street if w not in _SUFFIXES)


def house_number(address: str | None) -> str | None:
    """Leading digits of the street number (for a Socrata starts_with
    filter); None when the address does not start with one."""
    m = re.match(r"\s*(\d+)", address or "")
    return m.group(1) if m else None


@lru_cache(maxsize=65536)
def parse_address(address: str | None) -> Address | None:
    """Split a premises address into number, street and unit. None when there
    is no leading house number (nothing reliable to match on)."""
    text = (address or "").upper()
    main, _, extra = text.partition(",")
    main = re.sub(r"(\d+)\s*-\s*(\d+)", r"\1-\2", main).replace("#", " # ")
    tokens = [_WORDS.get(t, t) for t in re.sub(r"[^A-Z0-9#\- ]", " ", main).split()]
    tokens = [t.strip("-") for t in tokens if t.strip("-")]
    if not tokens or not tokens[0][:1].isdigit():
        return None
    number, rest = tokens[0], tokens[1:]
    street: list[str] = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in _UNIT_WORDS:
            break
        street.append(tok)
        i += 1
        # "ST JAMES PL": a type word first is a name ("Saint").
        if tok in _SUFFIXES and len(street) > 1:
            if tok == "HWY" and i < len(rest) and rest[i].isdigit():
                street.append(rest[i])
                i += 1
            if i < len(rest) and rest[i] in _DIRECTIONS:
                street.append(rest[i])
                i += 1
            break
    unit_tokens = rest[i:]
    if not any(t in _SUFFIXES for t in street[1:]):
        # No street type: trailing bare numbers are a floor or suite.
        while len(street) > 1 and street[-1].isdigit():
            unit_tokens.insert(0, street.pop())
    extra_tokens = [_WORDS.get(t, t) for t in re.sub(r"[^A-Z0-9# ]", " ", extra).split()]
    if not street:
        return None
    return Address(number, tuple(street), _suite(unit_tokens + extra_tokens))


def _place(zip_code: str | None, city: str | None) -> tuple[str, str]:
    return ((zip_code or "").strip()[:5], " ".join((city or "").upper().split()))


def same_premises(addr_a: str | None, zip_a: str | None, city_a: str | None,
                  addr_b: str | None, zip_b: str | None, city_b: str | None,
                  suite_one_side_ok: bool = False) -> bool:
    """True when two addresses are the same premises (rule in the module
    docstring). With ``suite_one_side_ok`` a suite on one side only still
    matches; two different suites never do. The contact lookup uses it,
    because Google often drops the suite a filing names."""
    a, b = parse_address(addr_a), parse_address(addr_b)
    if not a or not b or a.number != b.number:
        return False
    (za, ca), (zb, cb) = _place(zip_a, city_a), _place(zip_b, city_b)
    if za and zb:
        if za != zb:
            return False
    elif not (ca and ca == cb):
        return False
    if a.street != b.street:
        has_type_a = any(w in _SUFFIXES for w in a.street[1:])
        has_type_b = any(w in _SUFFIXES for w in b.street[1:])
        if has_type_a == has_type_b or a.street_no_type != b.street_no_type:
            return False
    if suite_one_side_ok and not (a.unit and b.unit):
        return True
    return a.unit == b.unit


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify(owner: str | None, owner_id: str | None, filed: date | None,
             priors: list[Prior], today: date) -> History:
    """Label one filing from the existing licenses at its address.

    A prior license counts when it is current, or ended within RECENT_DAYS.
    The same owner's licenses issued on or after the filing date are the
    venue's own new licenses (a new bar's permit issued before its late
    hours certificate), so they are left out entirely. Same owner beats a
    different owner: a company adding a permit in a building that also
    holds another business is still adding a permit."""
    cutoff = today - timedelta(days=RECENT_DAYS)
    considered, same, other = [], [], []
    for p in priors:
        mine = same_owner(owner, owner_id, p.owner, p.owner_id)
        if mine and filed and p.issued and p.issued >= filed:
            continue
        considered.append(p)
        if not (p.active or (p.ended and p.ended >= cutoff)):
            continue
        (same if mine else other).append(p)
    issued = [p.issued for p in considered if p.issued]
    label = ADDING_PERMIT if same else NEW_OWNER if other else NEW_VENUE
    return History(label, len(considered), min(issued) if issued else None)


def lookup(source, http, records: list, snapshots=None, today: date | None = None
           ) -> dict[str, History]:
    """History for each record (by source_record_id). Never raises: a failed
    lookup logs its error type only and every record falls back to Unknown.
    Logs counts only (Actions logs are public)."""
    if not records:
        return {}
    today = today or date.today()
    try:
        found = source.venue_history(http, records, snapshots=snapshots, today=today) or {}
    except Exception as exc:  # noqa: BLE001 - one lookup must not fail the run
        log.warning("history FAILED %s (%s)", source.name, type(exc).__name__)
        found = {}
    out = {r.source_record_id: found.get(r.source_record_id) or History() for r in records}
    counts = {lab: 0 for lab in LABELS}
    for h in out.values():
        counts[h.label] = counts.get(h.label, 0) + 1
    log.info("history %s: checked %d; new venue %d, new owner %d, adding a permit %d, "
             "unknown %d", source.name, len(out), counts[NEW_VENUE], counts[NEW_OWNER],
             counts[ADDING_PERMIT], counts[UNKNOWN])
    return out


def match_priors(rec, candidates: list[tuple[dict, Prior]], address_of) -> list[Prior]:
    """Priors whose address is the same premises as `rec`. `address_of(row)`
    returns (address, zip, city) for a candidate row."""
    out = []
    for row, prior in candidates:
        addr, zip_code, city = address_of(row)
        if same_premises(rec.address, rec.zip, rec.city, addr, zip_code, city):
            out.append(prior)
    return out
