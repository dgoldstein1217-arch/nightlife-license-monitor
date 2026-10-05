"""Deterministic lead qualification and scoring.

Rules, not AI, decide. Every decision carries a human-readable reason so the
operator can see why a record is (or is not) in the queue.

The owner sells event ticketing, table and VIP reservations and POS to
venues, so the tier is the kind of venue:

  A  nightclubs, lounges and ticketed venues: club / lounge / cabaret names,
     comedy, live music, stadiums, arenas, theaters, event centers, rooftops;
     nightclub and ticketed-venue licenses; stadium concessionaires
  B  obvious bars and not-quite-ticketed venues: tavern, pub, taproom,
     karaoke, billiards, bowling, cinemas; a lounge or venue word on a
     restaurant name; bar-only licenses (no food required)
  C  everything else that qualifies, mostly restaurants

Coffee shops, bakeries, dessert shops and national chains are dropped.
Licenses alone rarely tell a bar from a restaurant, so the business name
does most of the work. A restaurant name never reaches A: "Restaurant &
Lounge" is B.

Adult venues (gentlemen's clubs, strip clubs, topless bars) keep their tier
and score but get ``adult`` = True: never Hot, never sent to Attio, never
named in Slack.

Two numbers per record. ``score`` is the qualification threshold (unchanged
job: is this a lead at all). ``lead_score`` (0 to 100) ranks leads by
ticketing fit: venue tier, nightlife license, licensing stage, filing type.
``hot`` = tier A, not adult, with a lead_score of at least HOT_MIN_SCORE
(default 75).
Hot is a label on top of A/B/C, not a fourth tier.

Venue history (history.py) adjusts the lead score last: New owner -10 and
never Hot; Adding a permit -15 and never Hot (it also never goes to Attio
or Slack). New venue and Unknown change nothing.

Stage and nightlife-license signals come from each source (Source.stage,
Source.nightlife_license in sources/base.py); the points table below is
shared by every state.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from . import stage as stage_mod
from .models import Record

CATEGORY_POINTS = {
    "nightlife": 50,
    "on_premise": 35,
    "hospitality_mfg": 30,
    "catering_event": 20,
    "hotel": 15,  # deliberately low: hotels need a bar/lounge name to qualify
    # Unmapped license codes (e.g. WA approval privilege numbers): only a
    # hospitality-sounding name plus a meaningful action can qualify these.
    "other": 10,
}
# Categories that disqualify outright (PRD "normally exclude").
EXCLUDED_CATEGORIES = {"off_premise", "wholesale_mfg", "temporary"}

# Application types that signal something new is happening.
APPLICATION_TYPE_POINTS = [
    (re.compile(r"\b(NEW|ORIGINAL|ISSUE)\b"), 20, "new application"),
    (re.compile(r"CHANGE OF LOCATION"), 20, "change of location"),
    (re.compile(r"ASSUMPTION|CHANGE OF OWNER|TRANSFER"), 15, "ownership change"),
    (re.compile(r"EXPANSION|ADDED/CHANGE OF CLASS|CHANGE OF ACTIVITY|ADDED PRIVILEGE"),
     15, "expanded alcohol service"),
    (re.compile(r"TRADENAME|TRADE NAME"), 5, "new trade name"),
]
# Routine actions with no sign of a new venue/concept (PRD exclude).
ROUTINE_APPLICATION_TYPES = re.compile(
    r"RENEW|CHANGE OF CORPORATE OFFICER|DISCONTINU|DISC\. LIQUOR|RESUME BUSINESS")
ROUTINE_STATUSES = re.compile(r"DISCONTINU|SURREND|REVOK|CANCEL|WITHDRAW|DENIED|EXPIRED")

POSITIVE_WORDS = re.compile(
    r"\b(BAR|BARS|LOUNGE|NIGHT ?CLUB|COMEDY CLUB|DANCE CLUB|DANCE HALL|TAVERN|PUB|SALOON|COCKTAILS?|ROOFTOP|"
    r"SPEAKEASY|CANTINA|TAPROOM|TAP ROOM|TAP HOUSE|TAPHOUSE|BREWERY|BREWING|BREWPUB|"
    r"BEER GARDEN|BIERGARTEN|BEER HALL|WINE BAR|DISTILLERY|TASTING ROOM|CABARET|"
    r"MUSIC HALL|LIVE MUSIC|KARAOKE|HOOKAH|SOCIAL CLUB|ICEHOUSE|ICE HOUSE|"
    r"GASTROPUB|GRILL|KITCHEN|BISTRO|TRATTORIA|OSTERIA|IZAKAYA|STEAKHOUSE|"
    r"SUPPER CLUB|EVENT SPACE|VENUE|BALLROOM|BOWLING|BOWL|ARCADE|COMEDY|KTV|"
    r"CINEMAS?|BILLIARDS|POOL HALL)\b")

EXCLUDE_WORDS = re.compile(
    r"\b(GROCERY|GROCERIES|SUPERMARKET|SUPER MARKET|MARKET|MINI MART|MINIMART|"
    r"FOOD MART|MART|DELI & GROCERY|BODEGA|CONVENIENCE|PHARMACY|DRUG|CVS|WALGREENS?|"
    r"RITE AID|7-ELEVEN|7 ELEVEN|SEVEN ELEVEN|CIRCLE K|QUIKTRIP|BUC-EE'?S|WAWA|"
    r"GAS|FUEL|SHELL|CHEVRON|EXXON|MOBIL|VALERO|ARCO|TEXACO|SUNOCO|CITGO|"
    r"PETRO|TRUCK STOP|TRAVEL CENTER|DOLLAR GENERAL|FAMILY DOLLAR|WALMART|"
    r"WAL-MART|TARGET|COSTCO|SAM'?S CLUB|KROGER|SAFEWAY|ALBERTSONS|VONS|RALPHS|"
    r"H-E-B|HEB|WHOLE FOODS|TRADER JOE'?S|SPROUTS|ALDI|PUBLIX|FOOD LION|"
    r"LIQUOR STORE|LIQUORS|WINE & SPIRITS|WINE AND SPIRITS|BOTTLE SHOP|PACKAGE STORE|"
    r"SMOKE SHOP|TOBACCO|VAPE|DISTRIBUT\w*|WHOLESALE\w*|IMPORT\w*|WAREHOUSE|"
    r"LOGISTICS|AIRPORT|UNIVERSITY|COLLEGE|HOSPITAL|MEDICAL CENTER|"
    r"MILITARY|ARMY|NAVY|AIR FORCE|NAVAL|VETERANS OF FOREIGN WARS|VFW|"
    r"AMERICAN LEGION|CHURCH|PARISH|SCHOOL|HIGH SCHOOL)\b")

# --- Venue class (decides the tier) ---------------------------------------

# Nightclub names (tier A).
NIGHTCLUB_WORDS = re.compile(
    r"\b(NIGHT ?CLUBS?|NIGHTLIFE|ULTRA ?LOUNGE|LOUNGE|CABARET|DISCO|DISCOTHEQUE|"
    r"DANCE CLUB|DANCE HALL|DANCEHALL|GENTLEM[AE]N'?S? CLUB|STRIP ?CLUBS?|HOOKAH|AFTER ?HOURS|"
    r"DAY ?CLUB|BEACH CLUB)\b")
# "Lounge" that is not a nightlife lounge.
NOT_NIGHTCLUB = re.compile(
    r"\b(COFFEE|CAFE|TEA|CIGAR|NAIL|HAIR|BEAUTY|LASH|BROW|SPA|AIRPORT|ESPRESSO|"
    r"DESSERT|JUICE|MASSAGE) LOUNGE\b")

# Ticketed venues (tier A): places that sell admission or do VIP. Left out on
# purpose: PAVILION (restaurants and malls), BOWL (bowling, poke bowls).
TICKETED_WORDS = re.compile(
    r"\b(COMEDY|IMPROV|STADIUMS?|ARENAS?|BALLPARK|SPEEDWAY|RACEWAY|MOTORSPORTS?|"
    r"SPORTSPLEX|FIELD ?HOUSE|AMPHITHEA(TER|TRE)|THEATERS?|THEATRES?|PLAYHOUSE|"
    r"CONCERTS?|CONCERT HALL|MUSIC HALL|MUSIC VENUE|LIVE MUSIC|JAZZ CLUB|BALLROOM|"
    r"EVENTS? CENTER|EVENTS? CENTRE|EVENT VENUE|EVENT SPACE|RODEO|FAIRGROUNDS?|"
    r"EXPO CENTER|EXPOSITION CENTER|CONVENTION CENTER|PERFORMING ARTS|OPERA HOUSE|"
    r"COLISEUM|HOUSE OF BLUES|DAY ?CLUB|BEACH CLUB|ROOFTOP|SUPPER CLUB|CABARET)\b")
# Sells tickets, but not the owner's kind: cinemas, bowling, games. A name
# with one of these is B even with a ticketed word or license ("Movie
# Theater", a bowling alley with an amusement license).
NOT_TICKETED = re.compile(
    r"\b(CINEMAS?|CINEPLEX|MOVIES?|IMAX|DRIVE-?IN|DRAFTHOUSE|BOWLING|BOWL|ARCADE|"
    r"BILLIARDS|POOL HALL|ESCAPE ROOM|TRAMPOLINE|SKATING|GOLF|MUSEUM|BINGO)\b")

# Stadium and arena concessionaires. The legal name is the operator; the DBA
# usually names the venue. Never dropped as a chain; A with a venue name.
CONCESSIONAIRES = re.compile(
    r"\b(LEVY (PREMIUM|RESTAURANTS?)|ARAMARK|DELAWARE NORTH|SPORTSERVICE|"
    r"SODEXO LIVE|LEGENDS HOSPITALITY|CENTERPLATE|SPECTRA|OAK VIEW GROUP|OVG|"
    r"ASM GLOBAL|SMG|LIVE NATION|AEG( PRESENTS)?)\b")
# Venue words that count only next to a concessionaire ("Fake Field").
CONCESSION_VENUE_WORDS = re.compile(
    r"\b(FIELD|PARK|CENTER|CENTRE|GARDENS?|DOME|BOWL|ZOO|AQUARIUM)\b")

# Adult entertainment. Kept, but flagged: never Hot, Attio or Slack.
ADULT_WORDS = re.compile(
    r"\bGENTLEM[AE]N'?S? (CLUB|CABARET|LOUNGE|BAR)\b|\bSTRIP ?CLUBS?\b|\bSTRIPTEASE\b|"
    r"\bTOPLESS\b|\bBIKINI BAR\b|"
    r"\bADULT (ENTERTAINMENT|CABARET|CLUB|NIGHTCLUB|LOUNGE)\b|"
    # Well-known adult club brands whose names carry no generic adult word.
    r"\bPURE PLATINUM\b|\bSPEARMINT RHINO\b|\bRICK'?S CABARET\b|\bHUSTLER CLUB\b|"
    r"\bPENTHOUSE CLUB\b|\bDEJA VU SHOWGIRLS\b|\bSHOWGIRLS\b|\bLARRY FLYNT'?S\b|"
    r"\bTOOTSIE'?S CABARET\b|\bSCORES GENTLEMEN\b|\bSAPPHIRE GENTLEMEN\b")

# Obvious bars and event venues (tier B).
BAR_VENUE_WORDS = re.compile(
    r"\b(BAR|BARS|TAVERN|PUB|SALOON|COCKTAILS?|ROOFTOP|SPEAKEASY|CANTINA|"
    r"TAPROOM|TAP ROOM|TAP HOUSE|TAPHOUSE|BREWERY|BREWING|BREWPUB|GASTROPUB|"
    r"BEER GARDEN|BIERGARTEN|BEER HALL|WINE BAR|DISTILLERY|ICEHOUSE|ICE HOUSE|"
    r"KARAOKE|KTV|LIVE MUSIC|MUSIC HALL|MUSIC VENUE|CONCERTS?|COMEDY|THEATERS?|"
    r"THEATRES?|AMPHITHEATER|EVENT CENTER|EVENT SPACE|EVENT HALL|EVENTS|VENUE|"
    r"BALLROOM|BOWLING|ARCADE|BILLIARDS|POOL HALL|SOCIAL CLUB|SUPPER CLUB|"
    r"SPORTS BAR|ENTERTAINMENT|JAZZ|HONKY ?TONK|WHISKEY|WHISKY|TEQUILA|MEZCAL|"
    r"CINEMAS?|MOVIE|DRAFTHOUSE|PINSTRIPES|PUNCH BOWL SOCIAL)\b")
# "Bar" that is really food or a service (sushi bar, bar & grill, nail bar).
FOOD_BAR = re.compile(
    r"\b(SUSHI|OYSTER|RAW|JUICE|SALAD|ESPRESSO|COFFEE|NOODLE|TACO|POKE|RAMEN|"
    r"NAIL|BLOW ?DRY|BROW|LASH|SMOOTHIE|DESSERT|YOGURT|CEREAL|OXYGEN|CANDY|"
    r"MILK|TEA|PHO|DUMPLING|BURGER|WING|SNACK|HOT ?POT|KBBQ|BBQ|GRILL|RESTAURANT|"
    r"KITCHEN|EATERY|CAFE|PIZZA|PIZZERIA|BISTRO|CUISINE|TAQUERIA)S?,? ?(&|AND|\+)? ?BARS?\b"
    r"|\bBARS? ?(&|AND) ?(GRILL|KITCHEN|RESTAURANT|EATERY|BISTRO)\b")

# License descriptions that are a nightclub or a bar by definition.
NIGHTCLUB_LICENSES = re.compile(r"\bCABARET\b|NIGHTCLUB|NIGHT CLUB")
BAR_VENUE_LICENSES = re.compile(
    r"PUBLIC PREMISES|MUSIC VENUE|\bTAVERN\b|PUBLIC PLACE OF AMUSEMENT|"
    r"PERFORMING ARTS|BOWLING|CIVIC CENTER|CATERING ESTABLISHMENT|^ON-SALE BEER$",
    re.I)
#: Source.nightlife_license keys that make a venue A by license (unless the
#: name is a restaurant, cinema or bowling alley). A Chicago PPA is not one:
#: bowling alleys, arcades and theaters all hold it, so it only adds points.
TICKETED_LICENSES = {
    "nightclub_cabaret": "nightclub license",
    "music_venue": "music venue license",
    "theater": "theater license",
    "sports_venue": "sports venue license",
    "event_venue": "event center license",
}

# Restaurant names: a bar-type license alone does not make these a bar.
RESTAURANT_WORDS = re.compile(
    r"\b(RESTAURANTE?S?|GRILL|KITCHEN|EATERY|BISTRO|STEAK ?HOUSE|SMOKEHOUSE|BBQ|"
    r"BARBECUE|SUSHI|RAMEN|PIZZA|PIZZERIA|TAQUERIA|TACOS?|MARISCOS|CUISINE|DINER|"
    r"TRATTORIA|OSTERIA|IZAKAYA|NOODLES?|BURGERS?|WINGS?|CHICKEN|SEAFOOD|CRAB|"
    r"FOODS?|EMPANADAS?|EMPANADAZO|COCINA|CAFE|CAF\u00c9|BRASSERIE)\b")

# Not a ticketing lead even with an on-premises license.
DROP_WORDS = re.compile(
    r"\b(COFFEE|CAFE|CAF\u00c9|ESPRESSO|BAKERY|BAKE SHOP|BAKESHOP|PATISSERIE|"
    r"PASTRY|PASTRIES|DONUTS?|DOUGHNUTS?|BAGELS?|TEA HOUSE|TEAHOUSE|TEA ROOM|"
    r"BOBA|BUBBLE TEA|JUICE|SMOOTHIES?|CREAMERY|ICE CREAM|GELATO|FROZEN YOGURT|"
    r"FROYO|DESSERTS?|CREPES?|CUPCAKES?|CHOCOLATES?|BREAKFAST|PANCAKES?|WAFFLES?|"
    r"SANDWICH(ES)?|DELI|BUFFET|FOOD TRUCK|DAYCARE|SALON|SPA|NAILS?)\b")
CHAINS = re.compile(
    r"\b(APPLEBEE'?S|CHILI'?S|OLIVE GARDEN|RED LOBSTER|OUTBACK|TEXAS ROADHOUSE|"
    r"CHEESECAKE FACTORY|BJ'?S RESTAURANT|BUFFALO WILD WINGS|HOOTERS|TWIN PEAKS|"
    r"TGI ?FRIDAY'?S|RED ROBIN|DENNY'?S|IHOP|CRACKER BARREL|LONGHORN STEAKHOUSE|"
    r"CHUY'?S|PAPPADEAUX|PAPPASITO'?S|PAPPAS|TORCHY'?S|STARBUCKS|DUNKIN|PANERA|"
    r"CHIPOTLE|TACO BELL|MCDONALD'?S|WHATABURGER|P\.? ?F\.? CHANG'?S|CARRABBA'?S|"
    r"BONEFISH|RUTH'?S CHRIS|MORTON'?S|FOGO DE CHAO|YARD HOUSE|MAGGIANO'?S|"
    r"FIRST WATCH|HOUSE OF PIES|CHUCK E\.? CHEESE|PEI WEI|SHAKE SHACK|"
    r"CAVA|SWEETGREEN|WINGSTOP|PIZZA HUT|DOMINO'?S|PAPA JOHN'?S|CICI'?S|"
    r"FAT TUESDAY)\b")

TIER_BONUS = {"A": 60, "B": 30, "C": 0}
QUALIFY_MIN = 40

# --- Lead score (0 to 100) ---------------------------------------------------

TIER_POINTS = {"A": 45, "B": 25, "C": 5}
#: Keys a Source.nightlife_license() may return -> (points, plain label).
#: The highest one on a record counts.
NIGHTLIFE_LICENSE_POINTS = {
    "ppa": (20, "Public place of amusement (Chicago)"),
    "late_hours": (15, "Late hours (Texas LH, Chicago Late Hour)"),
    "public_premises": (15, "Public premises bar (California type 48)"),
    "music_venue": (15, "Music venue or concert hall (California type 90, New York)"),
    "nightclub_cabaret": (15, "Night club or cabaret (New York, Washington)"),
    "theater": (15, "Theater or performing arts (California 64/69/71/72, New York, "
                    "Washington, Florida 11PA)"),
    "sports_venue": (15, "Stadium, arena or sports venue (New York, Washington, "
                         "Florida pari-mutuel)"),
    "event_venue": (10, "Civic center or event center (Florida)"),
    "full_liquor_bar": (10, "Full-liquor bar, no restaurant modifier (Florida COP)"),
}
STAGE_POINTS = {stage_mod.LICENSED: 25, stage_mod.APPROVED: 20,
                stage_mod.IN_REVIEW: 10, stage_mod.RECEIVED: 5}
#: (pattern on the upper-cased application type, points, label). First wins.
FILING_POINTS = [
    (re.compile(r"\b(NEW|ORIGINAL|ISSUE)\b|CHANGE OF LOCATION"), 10, "new or new location"),
    (re.compile(r"ASSUMPTION|CHANGE OF OWNER|TRANSFER"), 5, "change of owner"),
]
#: Pending lists without an application type (CA export) are new filings.
BLANK_FILING_POINTS = 10
HOT_MIN_SCORE_DEFAULT = 75


def hot_min_score() -> int:
    """Hot threshold, tunable without a code change (HOT_MIN_SCORE)."""
    try:
        return int(os.environ.get("HOT_MIN_SCORE", "").strip() or HOT_MIN_SCORE_DEFAULT)
    except ValueError:
        return HOT_MIN_SCORE_DEFAULT


def venue_class(names: str, license_description: str | None,
                license_keys=()) -> tuple[str, str]:
    """(tier, reason) from the business names, the license description and
    the source's license keys (Source.nightlife_license)."""
    lic = (license_description or "").upper()
    conc = CONCESSIONAIRES.search(names)
    if conc:
        # The operator's own name ("Levy Restaurants") says nothing about
        # the venue: judge the rest.
        names = CONCESSIONAIRES.sub(" ", names)
        venue = TICKETED_WORDS.search(names) or CONCESSION_VENUE_WORDS.search(names)
        if venue:
            return "A", (f"stadium or arena concession ({conc.group(0).lower()} at "
                         f"{venue.group(0).lower()})")
    food = RESTAURANT_WORDS.search(names) or DROP_WORDS.search(names)
    not_ticketed = NOT_TICKETED.search(names)
    club = NIGHTCLUB_WORDS.search(names)
    if club and NOT_NIGHTCLUB.search(names):
        club = None
    ticketed = None if not_ticketed else TICKETED_WORDS.search(names)
    signal = club or ticketed
    if signal and not food:
        kind = "nightclub" if club else "ticketed venue"
        return "A", f"{kind} name ({signal.group(0).lower()})"
    lic_a = next((TICKETED_LICENSES[k] for k in license_keys if k in TICKETED_LICENSES), None)
    lic_club = NIGHTCLUB_LICENSES.search(lic)
    if lic_club and not lic_a:
        lic_a = f"nightclub license ({lic_club.group(0).lower()})"
    if lic_a and not food and not not_ticketed:
        return "A", lic_a
    if signal:  # "Restaurant & Lounge", "Sushi & KTV Lounge": a bar, not a club
        return "B", f"lounge or venue name on a restaurant ({signal.group(0).lower()})"
    bar_names = FOOD_BAR.sub(" ", names)
    bar = BAR_VENUE_WORDS.search(bar_names)
    if bar:
        return "B", f"bar or venue name ({bar.group(0).lower()})"
    if lic_a and not not_ticketed:
        return "B", f"{lic_a} on a restaurant name"
    if food:
        return "C", "restaurant or other (no bar or club signal)"
    for part in re.split(r"[,;]", lic):
        lic_bar = BAR_VENUE_LICENSES.search(part.strip())
        if lic_bar:
            return "B", f"bar or venue license ({part.strip().lower()})"
    return "C", "restaurant or other (no bar or club signal)"


def a_by_name_only(names: str, license_keys=(), license_description: str | None = None) -> bool:
    """True when a tier A venue is A only because its name has a club or
    lounge word: no nightlife license (any NIGHTLIFE_LICENSE_POINTS key or a
    cabaret or nightclub license description), no ticketed-venue word, no
    stadium concessionaire. Only these can be shown as B when Google lists
    the place as a restaurant (contact.google_tier)."""
    names = (names or "").upper()
    if any(k in NIGHTLIFE_LICENSE_POINTS or k in TICKETED_LICENSES for k in license_keys):
        return False
    if NIGHTCLUB_LICENSES.search((license_description or "").upper()):
        return False
    if CONCESSIONAIRES.search(names) or TICKETED_WORDS.search(names):
        return False
    return bool(NIGHTCLUB_WORDS.search(names)) and not NOT_NIGHTCLUB.search(names)


@dataclass
class Qualification:
    qualified: bool
    score: int
    tier: str | None  # "A", "B", "C" or None
    reason: str
    stage: str | None = None  # one of stage.STAGES, set for every record
    lead_score: int = 0  # 0 to 100, qualified records only
    hot: bool = False
    adult: bool = False  # adult entertainment: never Hot, Attio or Slack


_REGISTRY: dict | None = None


def _source_for(rec: Record, source):
    """The Source object for a record (None for unknown test sources)."""
    global _REGISTRY
    if source is not None:
        return source
    if _REGISTRY is None:
        from .sources import all_sources

        _REGISTRY = {s.name: s for s in all_sources()}
    return _REGISTRY.get(rec.source)


def lead_score(tier: str, license_keys, stage: str | None, stage_counts: bool,
               application_type: str | None) -> int:
    """The shared 0 to 100 ranking (see the module docstring)."""
    points = TIER_POINTS.get(tier, 0)
    points += max((NIGHTLIFE_LICENSE_POINTS[k][0] for k in license_keys
                   if k in NIGHTLIFE_LICENSE_POINTS), default=0)
    if stage_counts:
        points += STAGE_POINTS.get(stage, 0)
    app_type = (application_type or "").upper()
    if not app_type:
        points += BLANK_FILING_POINTS
    else:
        points += next((pts for pat, pts, _ in FILING_POINTS if pat.search(app_type)), 0)
    return min(points, 100)


def apply_history(q: Qualification, label: str | None) -> Qualification:
    """Adjust a qualified record for its venue history (history.py labels):
    New owner and Adding a permit lose points and are never Hot."""
    from . import history

    if q.qualified and label in history.SCORE_ADJUST:
        q.lead_score = max(0, q.lead_score + history.SCORE_ADJUST[label])
        q.hot = False
    return q


def qualify(rec: Record, metro: str | None, source=None,
            today: date | None = None, history: str | None = None) -> Qualification:
    """Gate, tier, qualification score, then stage and lead score.
    `source` defaults to the registered Source named rec.source. `history`
    is the record's venue history label, if known (apply_history)."""
    src = _source_for(rec, source)
    license_keys = src.nightlife_license(rec) if src else ()
    q = _qualify(rec, metro, license_keys)
    q.stage = src.stage(rec) if src else stage_mod.from_status(rec.status)
    if q.qualified:
        today = today or datetime.now(timezone.utc).date()
        counts = src.stage_counts(rec, today) if src else True
        q.lead_score = lead_score(q.tier, license_keys, q.stage, counts,
                                  rec.application_type)
        q.adult = bool(ADULT_WORDS.search(
            " ".join(x for x in (rec.dba, rec.legal_name) if x).upper()))
        q.hot = q.tier == "A" and not q.adult and q.lead_score >= hot_min_score()
    return apply_history(q, history)


def _qualify(rec: Record, metro: str | None, license_keys=()) -> Qualification:
    reasons: list[str] = []
    if not metro:
        return Qualification(False, 0, None, "outside target metros")
    if rec.category in EXCLUDED_CATEGORIES:
        return Qualification(False, 0, None, f"excluded license category: {rec.category}")

    app_type = (rec.application_type or "").upper()
    status = (rec.status or "").upper()
    if app_type and ROUTINE_APPLICATION_TYPES.search(app_type):
        return Qualification(False, 0, None, f"routine action: {app_type.lower()}")
    if status and ROUTINE_STATUSES.search(status):
        return Qualification(False, 0, None, f"inactive status: {status.lower()}")

    names = " ".join(x for x in (rec.dba, rec.legal_name) if x).upper()
    bad = EXCLUDE_WORDS.search(names)
    good = POSITIVE_WORDS.search(names) or TICKETED_WORDS.search(names)
    if bad and not good:
        return Qualification(False, 0, None, f"non-target business name ({bad.group(0).lower()})")
    chain = next((m for m in (CHAINS.match(n.strip().upper().removeprefix("THE "))
                              for n in (rec.dba, rec.legal_name) if n) if m), None)
    if chain:
        return Qualification(False, 0, None, f"national chain ({chain.group(0).lower()})")
    # A Chicago PPA no longer lifts a bar to A by itself: a club or ticketed
    # name is A anyway, and a bar name with a PPA stays B with the PPA points.
    tier, class_reason = venue_class(names, rec.license_description, license_keys)
    drop = DROP_WORDS.search(names)
    if drop and tier == "C":
        return Qualification(False, 0, None,
                             f"not a nightlife venue ({drop.group(0).lower()})")

    score = CATEGORY_POINTS.get(rec.category, 0)
    if rec.category == "other":
        reasons.append("license type not classified"
                       + (f" ({rec.license_description})" if rec.license_description else ""))
    elif score:
        reasons.append(f"{rec.category.replace('_', ' ')} license"
                       + (f" ({rec.license_description})" if rec.license_description else ""))

    matched_type = False
    for pattern, points, label in APPLICATION_TYPE_POINTS:
        if app_type and pattern.search(app_type):
            score += points
            reasons.append(label)
            matched_type = True
            break
    if not matched_type and not app_type:
        # Pending-application lists without a type (e.g. CA export) are
        # applications by definition; give partial credit.
        score += 10
        reasons.append("pending application")

    if good:
        score += 15
        reasons.append(f"name suggests hospitality ({good.group(0).lower()})")
    if bad:
        score -= 20
        reasons.append(f"name also matches exclusion ({bad.group(0).lower()})")

    reasons.insert(0, metro)
    if score < QUALIFY_MIN and tier == "C":
        return Qualification(False, score, None, "; ".join(reasons + ["score below threshold"]))
    if score < QUALIFY_MIN - 15:
        # A/B names still need some license or filing signal.
        return Qualification(False, score, None, "; ".join(reasons + ["score below threshold"]))
    reasons.insert(1, class_reason)
    return Qualification(True, score + TIER_BONUS[tier], tier, "; ".join(reasons))
