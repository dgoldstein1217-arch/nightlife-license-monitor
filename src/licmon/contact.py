"""Contact lookup: can the owner actually reach this venue, and how sure are we?

For each eligible lead (the same Hot, A and strong B venues that go to Attio,
``attio.eligible``) this module finds and checks contact details, scores how
sure we are that each one belongs to the venue, and suggests how to reach
out. It never contacts a business: it only reads public listings, the
venue's own website, Instagram's public business profiles and web search
results for the venue's name. The owner
decides whether and how to reach out.

Per venue (keyed by daily_leads.venue_key):

1. Google Places Text Search (New) with the venue name and address. A place
   is used only when its address is the same premises as the filing, by the
   venue-history rule (``history.same_premises``: house number, street, ZIP
   or city; floors ignored), except that a suite on one side only still
   matches (Google often drops it; two different suites never match). Name
   similarity is a second signal: a listing at the same address under
   another name may be the old business or the building.
2. The listing's website, fetched once with the polite client (http.py):
   Instagram and Facebook page links, mailto emails and tel links.
3. Instagram Business Discovery (Graph API) for handles that website links.
   The profile's own website and bio are compared with the listing. A
   personal account cannot be read by the API: "found, unverifiable".
4. A web search for the venue's Instagram (Brave Search API), only when the
   website gave no Verified or Likely Instagram: one query per venue per
   check, ``site:instagram.com "<venue name>" <city>``. Only the search
   results are read (profile URL, title, snippet); instagram.com itself is
   never fetched. A profile counts when its name or handle matches the
   venue, and is Likely at most (instagram_from_search).
5. The filing's own phone is a weak signal (often a lawyer or expediter):
   it never makes a venue reachable on its own.

Each channel gets a 0 to 100 score and a label (POINTS and LABEL_MIN below;
the table is in AGENTS.md). A venue is reachable when at least one channel
is Verified or Likely. METHOD_RULES pick the best and second-best way to
reach out. Venues that are not reachable wait: they are rechecked every
RECHECK_DAYS and given up after GIVE_UP_DAYS from the first check.

People are never looked up: a person's name from a filing never counts
toward these scores, and nothing about people is stored here.

Privacy: contact details live only in the private database
(contact_checks). Errors carry the lookup name and HTTP status or error type
only, never a URL, key or response body. Logs are counts only.
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from . import metros
from .history import normalize_owner, parse_address, same_premises

log = logging.getLogger("licmon")

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
#: Only what the checks need (Places bills by the fields asked for).
PLACES_FIELDS = ("id", "displayName", "formattedAddress", "addressComponents",
                 "nationalPhoneNumber", "websiteUri", "googleMapsUri", "businessStatus")
FIELD_MASK = ",".join("places." + f for f in PLACES_FIELDS)
PLACES_RESULTS = 5
SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
SEARCH_RESULTS = 10
#: Seconds between web searches (the Brave plan's rate limit is 1 a second).
SEARCH_MIN_INTERVAL = 1.1
GRAPH_URL = "https://graph.facebook.com/v21.0"
IG_FIELDS = ("business_discovery.username({handle}){{username,name,biography,website,"
             "followers_count,media_count,media.limit(1){{timestamp}}}}")

DEFAULT_DAILY_CAP = 150
RECHECK_DAYS = 7
GIVE_UP_DAYS = 120
RECENT_POST_DAYS = 60
#: Eligible open leads this recent that were never checked (over a past
#: day's cap) are checked after today's leads and the due rechecks.
BACKLOG_DAYS = 7
#: Website and Instagram handles looked at per venue.
MAX_HANDLES = 2
MAX_PAGE_BYTES = 2_000_000

REACHABLE, WAITING, GAVE_UP = "reachable", "waiting", "gave_up"
STATUS_TEXT = {REACHABLE: "Reachable", WAITING: "Waiting", GAVE_UP: "Gave up"}

VERIFIED, LIKELY, UNVERIFIED, NONE = "Verified", "Likely", "Unverified", "None"
REACHABLE_LABELS = (VERIFIED, LIKELY)
#: Lowest score for each label.
LABEL_MIN = {VERIFIED: 75, LIKELY: 50, UNVERIFIED: 1}

PHONE, WEBSITE, EMAIL, INSTAGRAM, FACEBOOK, FILING_PHONE = (
    "phone", "website", "email", "instagram", "facebook", "filing_phone")
#: An Instagram account found by web search, not linked from the website.
INSTAGRAM_SEARCH = "instagram_search"
KIND_TEXT = {PHONE: "Phone (Google)", WEBSITE: "Website", EMAIL: "Email",
             INSTAGRAM: "Instagram", FACEBOOK: "Facebook",
             FILING_PHONE: "Filing phone (may be a lawyer)",
             INSTAGRAM_SEARCH: "Instagram (found by search)"}

#: Points per signal. A channel's score is the sum of its signals, 0 to 100,
#: then capped (see score_channel). Shown in AGENTS.md and the workbook.
POINTS = {
    "listing_address": 35,     # Google listing at the same address as the filing
    "listing_name": 20,        # the listing's name matches the venue name
    "listing_open": 10,        # phone: Google says the place is open
    "phone_on_website": 10,    # phone: the venue website shows the same number
    "website_loads": 10,       # website: the site answered
    "linked_from_website": 20,  # email or Instagram linked from that website
    "facebook_linked": 10,     # Facebook page linked from that website
    "ig_links_back": 15,       # Instagram profile links to the same website
    "ig_bio_address": 15,      # Instagram bio names the street address or ZIP
    "ig_bio_city": 5,          # Instagram bio names the city or neighborhood
    "ig_unreadable": -15,      # Instagram account the API cannot read (personal)
    "filing_phone": 15,        # the phone on the license filing
    # Instagram found by web search (never linked from the website):
    "ig_search_name": 35,      # the profile's name or handle matches the venue name
    "ig_search_address": 20,   # the result's snippet names the street address or ZIP
    "ig_search_city": 15,      # the title, snippet or handle names the city or metro
}
#: Highest score with no independent proof of the venue's identity: a
#: listing under another name (may be the old business) or the filing phone.
CAP_UNPROVEN = 49
#: Facebook pages cannot be checked by API, so a linked page tops out Likely.
CAP_FACEBOOK = 74
#: An Instagram found only by web search is never Verified: a different venue
#: with the same name in the same city would look the same. Two or more
#: accounts matching equally well ("ig_search_ambiguous") stay Unverified.
CAP_SEARCH = 74

REASONS = {
    "listing_address": "Google listing at the same address",
    "listing_name": "same name",
    "listing_other_name": "under another name (may be the old business)",
    "listing_closed": "Google says it closed for good",
    "listing_open": "open on Google",
    "phone_on_website": "the website shows this phone",
    "website_loads": "the website loads",
    "linked_from_website": "the website links this {kind}",
    "facebook_linked": "the website links this Facebook page",
    "ig_links_back": "the Instagram links back to the website",
    "ig_bio_address": "the Instagram bio names the address",
    "ig_bio_city": "the Instagram bio names the city",
    "ig_unreadable": "Instagram's API cannot read this account",
    "filing_phone": "phone from the license filing (may be a lawyer)",
}

_REASON_KIND = {INSTAGRAM: "Instagram", EMAIL: "email address"}
_SEARCH_PARTS = (("ig_search_name", "name"), ("ig_search_address", "address"),
                 ("ig_search_city", "city"))


def search_reason(ch: Channel) -> str:
    """"Found by web search: name and city match" and the like."""
    if "ig_search_ambiguous" in ch.signals:
        return ("Found by web search: more than one account matches as well; "
                "check by hand")
    parts = [word for sig, word in _SEARCH_PARTS if sig in ch.signals]
    if len(parts) <= 1:
        return "Found by web search: name match only; check by hand"
    return ("Found by web search: " + ", ".join(parts[:-1]) + " and " + parts[-1]
            + " match")

WAIT_METHOD = "Wait: no verified contact yet"


class ContactError(RuntimeError):
    """Lookup failure with a value-free message: "<lookup> failed (HTTP 403)"
    or "<lookup> failed (<ErrorType>)". Never a URL, key or body."""

    def __init__(self, lookup: str, detail: str, fatal: bool = False):
        super().__init__(f"{lookup} failed ({detail})")
        self.lookup = lookup
        self.detail = detail
        self.fatal = fatal  # bad key or quota: stop calling it today


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def places_key() -> str:
    return os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()


def ig_token() -> str:
    return os.environ.get("IG_GRAPH_ACCESS_TOKEN", "").strip()


def ig_account_id() -> str:
    return os.environ.get("IG_BUSINESS_ACCOUNT_ID", "").strip()


def search_key() -> str:
    return os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()


def search_configured() -> bool:
    """The Instagram web search runs when its key is set. Optional."""
    return bool(search_key())


def configured() -> bool:
    """Enrichment runs when the Places key is set. Instagram is optional."""
    return bool(places_key())


def ig_configured() -> bool:
    return bool(ig_token() and ig_account_id())


def daily_cap() -> int:
    try:
        return max(0, int(os.environ.get("ENRICH_DAILY_CAP", "").strip()
                          or DEFAULT_DAILY_CAP))
    except ValueError:
        return DEFAULT_DAILY_CAP


# ---------------------------------------------------------------------------
# Channels and scores
# ---------------------------------------------------------------------------

@dataclass
class Channel:
    kind: str
    value: str  # handle, phone, email or URL as shown to the owner
    url: str | None = None
    signals: list[str] = field(default_factory=list)
    score: int = 0
    label: str = NONE
    last_post: str | None = None  # Instagram: ISO date of the latest post


def label_for(score: int) -> str:
    for label in (VERIFIED, LIKELY, UNVERIFIED):
        if score >= LABEL_MIN[label]:
            return label
    return NONE


def score_channel(ch: Channel) -> Channel:
    """Fill in score and label from the signals (POINTS, then the caps)."""
    sig = set(ch.signals)
    if "listing_closed" in sig:
        ch.score, ch.label = 0, NONE
        return ch
    score = sum(POINTS.get(s, 0) for s in ch.signals)
    score = max(0, min(100, score))
    proven = "listing_name" in sig or "ig_bio_address" in sig
    if ch.kind == FILING_PHONE or ("listing_address" in sig and not proven):
        score = min(score, CAP_UNPROVEN)
    if ch.kind == FACEBOOK:
        score = min(score, CAP_FACEBOOK)
    if ch.kind == INSTAGRAM_SEARCH:
        score = min(score, CAP_UNPROVEN if "ig_search_ambiguous" in sig else CAP_SEARCH)
    ch.score, ch.label = score, label_for(score)
    return ch


def reachable(ch: Channel | None) -> bool:
    return bool(ch) and ch.kind != FILING_PHONE and ch.label in REACHABLE_LABELS


def reason(ch: Channel | None) -> str:
    """Short plain reason, e.g. "Google listing at the same address, same
    name; the website links this Instagram"."""
    if not ch or not ch.signals:
        return "Nothing found yet"
    if ch.kind == INSTAGRAM_SEARCH:
        return search_reason(ch)
    listing = [s for s in ch.signals if s.startswith("listing_")]
    head = ", ".join(REASONS[s] for s in listing if s in REASONS and s != "listing_open")
    rest = [REASONS[s].format(kind=_REASON_KIND.get(ch.kind, ch.kind)) for s in ch.signals
            if s in REASONS and (not s.startswith("listing_") or s == "listing_open")]
    text = "; ".join(p for p in [head] + rest if p)
    return text[:1].upper() + text[1:]


# ---------------------------------------------------------------------------
# Outreach method
# ---------------------------------------------------------------------------

def _recent(ch: Channel, today: date) -> bool:
    if not ch.last_post:
        return False
    try:
        posted = date.fromisoformat(ch.last_post[:10])
    except ValueError:
        return False
    return (today - posted).days <= RECENT_POST_DAYS


#: (method, channel kind, extra test, plain rule). The first rule whose
#: channel is Verified or Likely and whose test passes is the best way; the
#: next rule on a different channel is the second best. AGENTS.md and the
#: workbook's How scoring works tab show the plain rules.
METHOD_RULES = [
    ("Instagram DM", INSTAGRAM, lambda ch, r, today: _recent(ch, today),
     f"Instagram is Verified or Likely and posted in the last {RECENT_POST_DAYS} days."),
    ("Call", PHONE, lambda ch, r, today: r.business_status == "OPERATIONAL",
     "The Google listing's phone is Verified or Likely and Google says it is open."),
    ("Facebook message", FACEBOOK, lambda ch, r, today: True,
     "A Facebook page linked from the venue's verified website."),
    ("Email", EMAIL, lambda ch, r, today: True,
     "An email address linked from the venue's verified website."),
    ("Instagram DM (found by search)", INSTAGRAM_SEARCH, lambda ch, r, today: True,
     "An Instagram account found by web search: its name matches the venue, and its "
     "address or city does too. Likely at most; a same-named venue in the same city "
     "would look the same, so check the profile first."),
    ("Instagram DM (no recent posts seen)", INSTAGRAM, lambda ch, r, today: True,
     f"Instagram is Verified or Likely, but no post in the last {RECENT_POST_DAYS} days "
     "was seen (or Instagram's API could not read the account)."),
    ("Website contact form", WEBSITE, lambda ch, r, today: True,
     "Only the venue's website is Verified or Likely."),
    ("Call (Google does not say it is open)", PHONE, lambda ch, r, today: True,
     "The Google listing's phone is Verified or Likely, but Google does not say it is open."),
]
METHOD_TEXT = [(method, text) for method, _, _, text in METHOD_RULES]


def best_channels(channels: list[Channel]) -> dict[str, Channel]:
    """The highest-scoring channel of each kind."""
    out: dict[str, Channel] = {}
    for ch in channels:
        if ch.kind not in out or ch.score > out[ch.kind].score:
            out[ch.kind] = ch
    return out


def outreach(result: "Result", today: date) -> list[tuple[str, Channel]]:
    """Up to two (method, channel) picks, best first, on different channels."""
    by_kind = best_channels(result.channels)
    picks: list[tuple[str, Channel]] = []
    for method, kind, test, _ in METHOD_RULES:
        ch = by_kind.get(kind)
        if not reachable(ch) or not test(ch, result, today):
            continue
        if any(c.kind == kind for _, c in picks):
            continue
        picks.append((method, ch))
        if len(picks) == 2:
            break
    return picks


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

_GENERIC_NAME_WORDS = {"BAR", "LOUNGE", "RESTAURANT", "GRILL", "CLUB", "KITCHEN", "AND",
                       "CAFE", "TAVERN", "PUB", "NIGHTCLUB", "ROOFTOP", "COCKTAIL",
                       "COCKTAILS", "SPORTS", "BISTRO", "EATERY", "HOUSE"}


def _name_tokens(name: str | None) -> set[str]:
    tokens = set((normalize_owner(name) or "").split())
    core = tokens - _GENERIC_NAME_WORDS
    return core or tokens


def names_match(a: str | None, b: str | None) -> bool:
    """Half or more of the shorter name's distinctive words appear in the
    other ("Zebra Lounge" and "The Zebra" match; generic words like Bar or
    Lounge do not count unless a name has nothing else)."""
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    shared = len(ta & tb)
    return shared >= 1 and shared / min(len(ta), len(tb)) >= 0.5


def _component(place: dict, kind: str) -> str | None:
    for comp in place.get("addressComponents") or []:
        if kind in (comp.get("types") or []):
            return comp.get("longText") or comp.get("shortText")
    return None


def place_address(place: dict) -> tuple[str | None, str | None, str | None]:
    """(street address with suite, ZIP, city) from a Places result."""
    number, route = _component(place, "street_number"), _component(place, "route")
    if not number or not route:
        return None, _component(place, "postal_code"), _component(place, "locality")
    sub = (_component(place, "subpremise") or "").lstrip("#").strip()
    street = f"{number} {route}" + (f" STE {sub}" if sub else "")
    city = (_component(place, "locality") or _component(place, "postal_town")
            or _component(place, "sublocality"))
    return street, _component(place, "postal_code"), city


def match_place(row: dict, places: list[dict]) -> dict | None:
    """The first result at the same premises as the filing, else None."""
    for place in places:
        street, zip_code, city = place_address(place)
        if same_premises(row.get("address"), row.get("zip"), row.get("city"),
                         street, zip_code, city, suite_one_side_ok=True):
            return place
    return None


def search_query(row: dict) -> str:
    name = row.get("business_name") or row.get("company") or ""
    where = ", ".join(p for p in (row.get("address"), row.get("city"),
                                  " ".join(p for p in (row.get("state"), row.get("zip")) if p))
                      if p)
    return " ".join(p for p in (name, where) if p)


# ---------------------------------------------------------------------------
# Website links
# ---------------------------------------------------------------------------

class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for key, value in attrs:
                if key == "href" and value:
                    self.hrefs.append(value.strip())


_IG_RESERVED = {"p", "reel", "reels", "explore", "stories", "tv", "accounts", "share",
                "s", "direct", "about", "developer", "developers", "legal", "instagram",
                "web", "privacy", "terms", "help", "api", "oauth", "challenge", "embed",
                "static", "emails", "press", "blog", "session", "login", "signup"}
_HANDLE = re.compile(r"[a-z0-9._]{1,30}")
_FB_RESERVED = {"sharer", "sharer.php", "share", "share.php", "tr", "plugins", "groups",
                "events", "dialog", "login", "login.php", "help", "policies", "policy.php",
                "privacy", "terms", "watch", "marketplace", "gaming", "hashtag", "photo",
                "photo.php", "photos", "permalink.php", "story.php", "facebook", "meta",
                "business", "ads", "l.php", "home.php", "search", "settings", "people",
                "notifications", "messages", "reel", "reels", "stories", "fundraisers",
                "video.php", "legal", "careers", "badges", "pages_reaction_units",
                "media", "games", "fbml", "connect", "recover", "r.php", "about"}
_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PLACEHOLDER_DOMAINS = {"example.com", "example.org", "example.net", "domain.com",
                        "email.com", "yourdomain.com", "yoursite.com", "sentry.io",
                        "wixpress.com", "sentry.wixpress.com"}


def _host(url: str) -> tuple[str, list[str], dict]:
    parsed = urlparse(url if "//" in url else "https://" + url)
    host = (parsed.hostname or "").lower()
    for prefix in ("www.", "m.", "web.", "mobile."):
        host = host.removeprefix(prefix)
    parts = [p for p in parsed.path.split("/") if p]
    return host, parts, parse_qs(parsed.query)


def instagram_handle(url: str) -> str | None:
    """The account handle in an instagram.com link; None for posts, reels,
    explore, share links and Instagram's own pages."""
    host, parts, _ = _host(url)
    if host not in ("instagram.com", "instagr.am") or not parts:
        return None
    if parts[0] == "_u" and len(parts) > 1:
        parts = parts[1:]
    handle = parts[0].lower().lstrip("@")
    if handle in _IG_RESERVED or not _HANDLE.fullmatch(handle):
        return None
    return handle


def facebook_page(url: str) -> str | None:
    """A canonical facebook.com page URL; None for share and pixel links,
    plugins, groups, events, personal profile paths and Facebook's own pages."""
    host, parts, query = _host(url)
    if host not in ("facebook.com", "fb.com", "fb.me") or not parts:
        return None
    first = parts[0].lower()
    if first == "profile.php":
        page_id = (query.get("id") or [""])[0]
        return (f"https://www.facebook.com/profile.php?id={page_id}"
                if page_id.isdigit() else None)
    if first == "pages" and len(parts) >= 2:
        return "https://www.facebook.com/" + "/".join(parts[:3])
    if first == "pg" and len(parts) >= 2:
        first, parts = parts[1].lower(), parts[1:]
    if first in _FB_RESERVED or first.endswith(".php") or not re.fullmatch(
            r"[a-z0-9.\-]{2,80}", first):
        return None
    return "https://www.facebook.com/" + parts[0]


def _email(href: str) -> str | None:
    address = href[len("mailto:"):].split("?")[0].strip().lower()
    address = address.split(",")[0]
    if not _EMAIL.fullmatch(address):
        return None
    if address.split("@")[1] in _PLACEHOLDER_DOMAINS:
        return None
    return address


def phone_digits(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def format_phone(digits: str) -> str:
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


@dataclass
class SiteLinks:
    instagram: list[str] = field(default_factory=list)
    facebook: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)  # 10 digits


def extract_links(html_text: str) -> SiteLinks:
    """Instagram handles, Facebook pages, mailto emails and tel numbers
    linked from a page, in page order, without duplicates or junk."""
    parser = _Links()
    try:
        parser.feed(html_text or "")
        parser.close()
    except Exception:  # noqa: BLE001 - a broken page is just less data
        pass
    out = SiteLinks()
    for href in parser.hrefs:
        low = href.lower()
        if low.startswith("mailto:"):
            found = _email(href)
            target = out.emails
        elif low.startswith("tel:"):
            found = phone_digits(href[4:])
            target = out.phones
        elif "instagr" in low:
            found = instagram_handle(href)
            target = out.instagram
        elif "facebook" in low or "fb.com" in low or "fb.me" in low:
            found = facebook_page(href)
            target = out.facebook
        else:
            continue
        if found and found not in target:
            target.append(found)
    return out


def domain(url: str | None) -> str | None:
    if not url:
        return None
    host, _, _ = _host(url)
    return host or None


def same_site(a: str | None, b: str | None) -> bool:
    da, db = domain(a), domain(b)
    if not da or not db:
        return False
    return da == db or da.endswith("." + db) or db.endswith("." + da)


# ---------------------------------------------------------------------------
# Instagram by web search: decided from the search results alone
# ---------------------------------------------------------------------------

@dataclass
class SearchHit:
    """One web search result: only what the search API returned."""
    url: str
    title: str = ""
    description: str = ""


#: Instagram's and Meta's own accounts, never a venue.
_IG_OWN = {"instagram", "creators", "instagramforbusiness", "meta", "threads", "facebook",
           "whatsapp", "instagramforcreators", "about", "explore"}
_TITLE_HANDLE = re.compile(r"^(.*?)\s*\(\s*@([A-Za-z0-9._]{1,30})\s*\)")
_ENTITY_SUFFIX = re.compile(r"[\s,]+(L\.?L\.?C|INC|INCORPORATED|CORP|CORPORATION|LTD|LLP|PLLC|"
                            r"LP|DBA)\b\.?", re.IGNORECASE)
#: Metro aliases too short or too common to count in free text ("la" in a
#: Spanish bio, "sea" in "by the sea"): they count only as a handle part.
_HANDLE_ONLY_ALIASES = {"sea", "oak", "chi", "hou", "sa", "la", "li", "ny", "sf", "sj", "sd",
                        "oc", "bk", "305"}


def search_profile_handle(url: str | None) -> str | None:
    """The handle in an Instagram profile URL (instagram.com/<handle>/, any
    query string ignored); None for posts, reels, explore, stories, tv,
    accounts and Instagram's own accounts."""
    host, parts, _ = _host(url or "")
    if host not in ("instagram.com", "instagr.am") or len(parts) != 1:
        return None
    handle = instagram_handle(url or "")
    return None if not handle or handle in _IG_OWN else handle


def _plain(text: str | None) -> str:
    """Search snippets carry <strong> tags and entities."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


def profile_name(title: str | None) -> str | None:
    """The display name in an Instagram result title, "Fake Lounge
    (@fakelounge.atx) \u2022 Instagram photos and videos" -> "Fake Lounge"."""
    found = _TITLE_HANDLE.match(_plain(title))
    return (found.group(1).strip(" -|\u2022") or None) if found else None


def search_name(row: dict) -> str | None:
    """The name the Places query uses: the DBA, else the business name."""
    return row.get("business_name") or row.get("company") or None


def instagram_query(row: dict) -> str | None:
    """``site:instagram.com "Fake Lounge" Austin``: the venue name quoted,
    entity words (LLC, Inc ...) dropped, then the filing's city."""
    name = _ENTITY_SUFFIX.sub("", search_name(row) or "").replace('"', " ")
    name = " ".join(name.split()).strip(" ,.")
    if not name:
        return None
    city = " ".join((row.get("city") or "").split())
    return f'site:instagram.com "{name}"' + (f" {city}" if city else "")


def _search_words(name: str | None) -> list[str]:
    return [w for w in (normalize_owner(name) or "").split() if w != "AND"]


def search_name_match(name: str | None, display: str | None, handle: str) -> bool:
    """Does an Instagram profile match the venue name? Every distinctive word
    of the name (generic words like Bar, Lounge, Club, Kitchen, Grill do not
    count while one distinctive word remains) must be in the profile's
    display name, or inside the handle with dots and underscores removed
    ("fakelounge.atx" holds "fake"). A name made only of generic words must
    match whole: the display name exactly, or the start of the handle."""
    words = _search_words(name)
    if not words:
        return False
    core = [w for w in words if w not in _GENERIC_NAME_WORDS]
    shown = _search_words(display)
    flat = re.sub(r"[._]", "", handle or "").upper()
    if not core:
        return shown == words or (bool(flat) and flat.startswith("".join(words)))
    if set(core) <= set(shown):
        return True
    # Very short names ("Bo") are inside too many handles to count there.
    return sum(len(w) for w in core) >= 4 and all(w in flat for w in core)


def search_city_match(row: dict, title: str, description: str, handle: str) -> bool:
    """The filing's city, or a short name for its metro (metros.METRO_ALIASES:
    "atx", "chi", "nyc"), in the result's title or snippet, or in the handle
    (as a part between dots or underscores, or at its start or end)."""
    city = " ".join((row.get("city") or "").split()).lower()
    text = f"{title} {description}".lower()
    if city and re.search(r"\b" + re.escape(city) + r"\b", text):
        return True
    aliases = metros.city_aliases(row.get("city"), row.get("market") or row.get("metro"))
    free = {a for a in aliases if len(a) >= 3 and a not in _HANDLE_ONLY_ALIASES}
    if free & set(re.sub(r"[^a-z0-9]+", " ", text).split()):
        return True
    parts = [p for p in re.split(r"[._]", (handle or "").lower()) if p]
    if aliases & set(parts):
        return True
    flat = "".join(parts)
    return any(flat != a and (flat.endswith(a) or flat.startswith(a)) for a in free)


def _names_address(text: str, row: dict) -> bool:
    """Does the text name the filing's street address (house number and
    street name) or its ZIP?"""
    words = set(re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()).split())
    addr = parse_address(row.get("address"))
    zip5 = (row.get("zip") or "")[:5]
    street_word = next((w for w in (addr.street_no_type if addr else ())
                        if len(w) > 2 and not w.isdigit()), None)
    return bool((addr and addr.number in words and street_word and street_word in words)
                or (zip5 and zip5 in words))


def instagram_from_search(row: dict, hits: list[SearchHit]) -> list[Channel]:
    """Instagram channels from one web search's results, scored. Only profile
    URLs whose name or handle matches the venue count; the snippet naming
    the street address or ZIP, and the city or metro anywhere in the
    result, add points (POINTS, capped at CAP_SEARCH: Likely at most).
    Returns the best match, or, when two or more different handles match
    equally well, up to MAX_HANDLES of them marked ambiguous (Unverified).
    Nothing is fetched: instagram.com pages are never opened."""
    name = search_name(row)
    found: dict[str, list[str]] = {}
    for hit in hits:
        handle = search_profile_handle(hit.url)
        if not handle:
            continue
        title, desc = _plain(hit.title), _plain(hit.description)
        if not search_name_match(name, profile_name(title), handle):
            continue
        sig = ["ig_search_name"]
        if _names_address(desc, row):
            sig.append("ig_search_address")
        if search_city_match(row, title, desc, handle):
            sig.append("ig_search_city")
        have = found.get(handle)
        if have is None or len(sig) > len(have):
            found[handle] = sig
    channels = [score_channel(Channel(INSTAGRAM_SEARCH, "@" + h,
                                      f"https://www.instagram.com/{h}/", sig))
                for h, sig in found.items()]
    if not channels:
        return []
    channels.sort(key=lambda c: -c.score)  # stable: search order breaks ties
    top = [c for c in channels if c.score == channels[0].score]
    if len(top) == 1:
        return top
    for ch in top[:MAX_HANDLES]:
        ch.signals.append("ig_search_ambiguous")
        score_channel(ch)
    return top[:MAX_HANDLES]


# ---------------------------------------------------------------------------
# Lookups (each injectable; tests use fakes, never the network)
# ---------------------------------------------------------------------------

class Api:
    """Small JSON client for Places (POST) and the Graph API (GET). http.py
    stays GET-only for the scrapers. Error messages never hold the key."""

    def __init__(self, session=None, sleep=time.sleep):
        import requests

        self.session = session or requests.Session()
        self.sleep = sleep

    def call(self, lookup: str, method: str, url: str, *, body=None, params=None,
             headers=None):
        for attempt in range(3):
            try:
                resp = self.session.request(method, url, json=body, params=params,
                                            headers=headers, timeout=30)
            except Exception as exc:  # noqa: BLE001 - type only
                raise ContactError(lookup, type(exc).__name__) from None
            if resp.status_code in (429, 500, 502, 503) and attempt < 2:
                self.sleep(2 * (attempt + 1))
                continue
            return resp
        return resp


class Places:
    """Google Places Text Search (New)."""

    def __init__(self, key: str | None = None, api: Api | None = None):
        self.key = key or places_key()
        self.api = api or Api()

    def search(self, query: str) -> list[dict]:
        resp = self.api.call("places", "POST", PLACES_URL,
                             body={"textQuery": query, "pageSize": PLACES_RESULTS,
                                   "regionCode": "US"},
                             headers={"X-Goog-Api-Key": self.key,
                                      "X-Goog-FieldMask": FIELD_MASK,
                                      "Content-Type": "application/json"})
        if resp.status_code != 200:
            raise ContactError("places", f"HTTP {resp.status_code}",
                               fatal=resp.status_code in (400, 401, 403, 429))
        try:
            data = resp.json()
        except ValueError:
            raise ContactError("places", "bad JSON") from None
        return [p for p in (data.get("places") or []) if isinstance(p, dict)]


#: Graph error codes that mean the key or the quota, not the account.
_GRAPH_FATAL = {1, 2, 4, 10, 17, 32, 102, 190, 200, 341, 613}


class Instagram:
    """Instagram Business Discovery through the owner's business account."""

    def __init__(self, token: str | None = None, account_id: str | None = None,
                 api: Api | None = None):
        self.token = token or ig_token()
        self.account_id = account_id or ig_account_id()
        self.api = api or Api()

    def discover(self, handle: str) -> dict | None:
        """The public business profile, or None when the API cannot read the
        account (a personal account or no such handle). Raises ContactError
        for a bad token or quota."""
        resp = self.api.call("instagram", "GET", f"{GRAPH_URL}/{self.account_id}",
                             params={"fields": IG_FIELDS.format(handle=handle),
                                     "access_token": self.token})
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code == 200:
            found = (data or {}).get("business_discovery")
            return found if isinstance(found, dict) else None
        err = (data or {}).get("error") if isinstance(data, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        if resp.status_code in (401, 403) or code in _GRAPH_FATAL or resp.status_code >= 429:
            raise ContactError("instagram", f"HTTP {resp.status_code}"
                               + (f" code {code}" if isinstance(code, int) else ""),
                               fatal=True)
        return None  # e.g. code 110: not a business or creator account


#: Status codes that mean the search key or plan, not the query: stop
#: searching for the rest of the run (429 only after the retries).
_SEARCH_FATAL = {401, 402, 403, 429}


class BraveSearch:
    """Brave Search API, web search: one GET per query, at least
    SEARCH_MIN_INTERVAL seconds apart. Errors carry the status only, never
    the query or the key. Any provider with ``search(query) -> [SearchHit]``
    can stand in for it (see web_search)."""

    def __init__(self, key: str | None = None, api: Api | None = None,
                 clock=time.monotonic, sleep=time.sleep):
        self.key = key or search_key()
        self.api = api or Api(sleep=sleep)
        self.clock = clock
        self.sleep = sleep
        self._last: float | None = None

    def search(self, query: str) -> list[SearchHit]:
        if self._last is not None:
            wait = SEARCH_MIN_INTERVAL - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        try:
            resp = self.api.call("instagram search", "GET", SEARCH_URL,
                                 params={"q": query, "count": SEARCH_RESULTS, "country": "us"},
                                 headers={"X-Subscription-Token": self.key,
                                          "Accept": "application/json"})
        finally:
            self._last = self.clock()
        if resp.status_code != 200:
            raise ContactError("instagram search", f"HTTP {resp.status_code}",
                               fatal=resp.status_code in _SEARCH_FATAL)
        try:
            data = resp.json()
        except ValueError:
            raise ContactError("instagram search", "bad JSON") from None
        results = ((data or {}).get("web") or {}).get("results") if isinstance(data, dict) \
            else None
        return [SearchHit(str(r.get("url")), str(r.get("title") or ""),
                          str(r.get("description") or ""))
                for r in (results or []) if isinstance(r, dict) and r.get("url")]


def web_search():
    """The web search provider for the Instagram lookup, or None when no key
    is set (the lookup then runs exactly as before, without searching)."""
    return BraveSearch() if search_configured() else None


class Website:
    """Fetches a venue's own home page with the polite client."""

    def __init__(self, http=None):
        if http is None:
            from .http import Http

            http = Http(timeout=20, min_interval=1.0, retries=1)
        self.http = http

    def links(self, url: str) -> SiteLinks | None:
        """Links on the page, or None when it cannot be read (any failure is
        just "no data")."""
        try:
            snap = self.http.get(url)
        except Exception:  # noqa: BLE001 - a dead site is no data, not an error
            return None
        if "html" not in (snap.content_type or "").lower() and \
                not snap.body[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
            return None
        return extract_links(snap.body[:MAX_PAGE_BYTES].decode("utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# One venue
# ---------------------------------------------------------------------------

@dataclass
class Result:
    channels: list[Channel] = field(default_factory=list)
    place_id: str | None = None
    maps_url: str | None = None
    business_status: str | None = None
    score: int = 0
    label: str = NONE
    reason: str = "Nothing found yet"
    method: str = WAIT_METHOD
    second: str | None = None
    best: Channel | None = None  # the channel the method uses
    lookups_failed: list[str] = field(default_factory=list)  # e.g. "instagram failed (HTTP 400)"
    fatal_lookups: set[str] = field(default_factory=set)  # e.g. {"instagram search"}
    searched: bool = False  # a web search for the venue's Instagram was made

    @property
    def reachable(self) -> bool:
        return any(reachable(ch) for ch in self.channels)


def _bio_signals(profile: dict, row: dict, place: dict) -> list[str]:
    bio = " ".join(str(profile.get(k) or "") for k in ("biography", "name")).upper()
    out = []
    if _names_address(bio, row):
        out.append("ig_bio_address")
    places = [row.get("city"), _component(place, "neighborhood"),
              _component(place, "sublocality")]
    if any(p and re.search(r"\b" + re.escape(p.upper()) + r"\b", bio) for p in places):
        out.append("ig_bio_city")
    return out


def _last_post(profile: dict) -> str | None:
    media = ((profile.get("media") or {}).get("data") or [])
    stamp = media[0].get("timestamp") if media and isinstance(media[0], dict) else None
    return stamp[:10] if isinstance(stamp, str) and len(stamp) >= 10 else None


def check_venue(row: dict, places: Places, website: Website | None = None,
                instagram: Instagram | None = None, today: date | None = None,
                search=None) -> Result:
    """Find and score contact channels for one venue row (leadsheet shape).
    Raises ContactError only when the Places lookup itself fails; a website,
    Instagram or search failure just means less data (noted in
    lookups_failed). `search` (web_search) looks for the venue's Instagram
    only when the website gave none that is Verified or Likely."""
    today = today or datetime.now(timezone.utc).date()
    result = Result()
    filing = phone_digits((row.get("phone") or "").split(",")[0])
    if filing:
        result.channels.append(Channel(FILING_PHONE, format_phone(filing),
                                       f"tel:+1{filing}", ["filing_phone"]))

    place = match_place(row, places.search(search_query(row)))
    if place:
        result.place_id = place.get("id")
        result.maps_url = place.get("googleMapsUri")
        result.business_status = place.get("businessStatus")
        listing_name = (place.get("displayName") or {}).get("text")
        same_name = any(names_match(listing_name, n) for n in
                        (row.get("business_name"), row.get("company")) if n)
        base = ["listing_address", "listing_name" if same_name else "listing_other_name"]
        if result.business_status == "CLOSED_PERMANENTLY":
            base.append("listing_closed")

        phone = phone_digits(place.get("nationalPhoneNumber"))
        phone_ch = None
        if phone:
            sig = base + (["listing_open"] if result.business_status == "OPERATIONAL" else [])
            phone_ch = Channel(PHONE, format_phone(phone), f"tel:+1{phone}", sig)
            result.channels.append(phone_ch)

        site = place.get("websiteUri")
        if site:
            links = website.links(site) if website else None
            result.channels.append(Channel(WEBSITE, domain(site) or site, site,
                                           base + (["website_loads"] if links else [])))
            if links:
                if phone_ch and phone in links.phones:
                    phone_ch.signals.append("phone_on_website")
                for address in links.emails[:MAX_HANDLES]:
                    result.channels.append(Channel(EMAIL, address, f"mailto:{address}",
                                                   base + ["linked_from_website"]))
                for page in links.facebook[:1]:
                    result.channels.append(Channel(FACEBOOK, page, page,
                                                   base + ["facebook_linked"]))
                for handle in links.instagram[:MAX_HANDLES]:
                    ch = Channel(INSTAGRAM, "@" + handle,
                                 f"https://www.instagram.com/{handle}/",
                                 base + ["linked_from_website"])
                    if instagram:
                        try:
                            profile = instagram.discover(handle)
                        except ContactError as exc:
                            result.lookups_failed.append(str(exc))
                            instagram = None if exc.fatal else instagram
                            profile = False
                        if profile is None:
                            ch.signals.append("ig_unreadable")
                        elif profile:
                            if same_site(profile.get("website"), site):
                                ch.signals.append("ig_links_back")
                            ch.signals += _bio_signals(profile, row, place)
                            ch.last_post = _last_post(profile)
                    result.channels.append(ch)

    for ch in result.channels:
        score_channel(ch)
    if search is not None and not any(ch.kind == INSTAGRAM and reachable(ch)
                                      for ch in result.channels):
        query = instagram_query(row)
        if query:
            result.searched = True
            try:
                hits = search.search(query)
            except ContactError as exc:
                result.lookups_failed.append(str(exc))
                if exc.fatal:
                    result.fatal_lookups.add(exc.lookup)
            else:
                result.channels += instagram_from_search(row, hits)
    picks = outreach(result, today)
    if picks:
        result.method, result.best = picks[0]
        result.second = picks[1][0] if len(picks) > 1 else None
    else:
        ranked = sorted(result.channels, key=lambda c: -c.score)
        result.best = ranked[0] if ranked else None
    top = result.best
    if top:
        result.score, result.label, result.reason = top.score, top.label, reason(top)
    return result


# ---------------------------------------------------------------------------
# Recheck state
# ---------------------------------------------------------------------------

@dataclass
class State:
    status: str
    first_checked_at: datetime
    next_check_at: datetime | None
    became_reachable_at: datetime | None
    newly_reachable_on: date | None
    gave_up_at: datetime | None


def next_state(prev: dict | None, is_reachable: bool, now: datetime,
               from_today: bool) -> State:
    """Status after a check. `prev` is the stored row (or None for a first
    check); `from_today` is True when the venue is in today's queue.

    Reachable venues are not rechecked. Others wait RECHECK_DAYS, until
    GIVE_UP_DAYS after the first check. A venue that becomes reachable after
    waiting, or on a first check after its queue day (it was over a past
    day's cap), is newly reachable today: it shows on today's sheet and
    goes to Attio and Slack today."""
    prev = prev or {}
    first = prev.get("first_checked_at") or now
    was = prev.get("status")
    if is_reachable:
        newly = was in (WAITING, GAVE_UP) or (was is None and not from_today)
        return State(REACHABLE, first, None,
                     prev.get("became_reachable_at") if was == REACHABLE else now,
                     now.date() if newly else prev.get("newly_reachable_on"), None)
    if now - first >= timedelta(days=GIVE_UP_DAYS):
        return State(GAVE_UP, first, None, None, None, prev.get("gave_up_at") or now)
    return State(WAITING, first, now + timedelta(days=RECHECK_DAYS), None, None, None)


# ---------------------------------------------------------------------------
# Daily run
# ---------------------------------------------------------------------------

_LOAD_SQL = """SELECT venue_key, status, first_checked_at, became_reachable_at,
                      newly_reachable_on, gave_up_at, next_check_at
               FROM contact_checks"""

_SAVE_SQL = """
INSERT INTO contact_checks AS c
    (venue_key, status, channels, confidence_score, confidence_label,
     confidence_reason, outreach_method, outreach_second, contact_kind,
     contact_value, contact_url, place_id, maps_url, business_status, attempts,
     first_checked_at, last_checked_at, next_check_at, became_reachable_at,
     newly_reachable_on, gave_up_at)
VALUES (%(venue_key)s, %(status)s, %(channels)s, %(score)s, %(label)s, %(reason)s,
        %(method)s, %(second)s, %(kind)s, %(value)s, %(url)s, %(place_id)s,
        %(maps_url)s, %(business_status)s, 1, %(first)s, %(now)s, %(next)s,
        %(became)s, %(newly)s, %(gave_up)s)
ON CONFLICT (venue_key) DO UPDATE SET
    status = EXCLUDED.status, channels = EXCLUDED.channels,
    confidence_score = EXCLUDED.confidence_score,
    confidence_label = EXCLUDED.confidence_label,
    confidence_reason = EXCLUDED.confidence_reason,
    outreach_method = EXCLUDED.outreach_method,
    outreach_second = EXCLUDED.outreach_second,
    contact_kind = EXCLUDED.contact_kind, contact_value = EXCLUDED.contact_value,
    contact_url = EXCLUDED.contact_url, place_id = EXCLUDED.place_id,
    maps_url = EXCLUDED.maps_url, business_status = EXCLUDED.business_status,
    attempts = c.attempts + 1, last_checked_at = EXCLUDED.last_checked_at,
    next_check_at = EXCLUDED.next_check_at,
    became_reachable_at = EXCLUDED.became_reachable_at,
    newly_reachable_on = EXCLUDED.newly_reachable_on,
    gave_up_at = EXCLUDED.gave_up_at
"""


def save(conn, venue_key: str, result: Result, state: State, now: datetime) -> None:
    from . import db

    best = result.best
    with conn.cursor() as cur:
        cur.execute(_SAVE_SQL, {
            "venue_key": venue_key, "status": state.status,
            "channels": db.jsonb([asdict(ch) for ch in result.channels]),
            "score": result.score, "label": result.label, "reason": result.reason,
            "method": result.method, "second": result.second,
            "kind": best.kind if best else None, "value": best.value if best else None,
            "url": best.url if best else None, "place_id": result.place_id,
            "maps_url": result.maps_url, "business_status": result.business_status,
            "first": state.first_checked_at, "now": now, "next": state.next_check_at,
            "became": state.became_reachable_at, "newly": state.newly_reachable_on,
            "gave_up": state.gave_up_at})


def plan(today_rows: list[dict], due_rows: list[dict], backlog_rows: list[dict],
         checked: dict[str, dict], cap: int) -> tuple[list[tuple[dict, str]], int]:
    """Which venues to check today, in order: today's eligible leads not yet
    checked, waiting venues due a recheck, then recent unchecked leads left
    over from a past day's cap. Returns ([(row, kind)], over_cap)."""
    from . import attio

    work: list[tuple[dict, str]] = []
    seen: set[str] = set()
    for rows, kind, need_unchecked in ((today_rows, "new", True), (due_rows, "recheck", False),
                                       (backlog_rows, "backlog", True)):
        for row in attio.candidates(rows, require_contact=False):
            key = row["venue_key"]
            if key in seen or (need_unchecked and key in checked):
                continue
            seen.add(key)
            work.append((row, kind))
    return work[:cap], max(0, len(work) - cap)


def run(conn, *, places: Places, website: Website | None = None,
        instagram: Instagram | None = None, now: datetime | None = None,
        cap: int | None = None, search=None) -> dict:
    """Check today's eligible leads, due rechecks and the backlog, up to the
    daily cap, and save the results. Never raises for a lookup failure: a
    failed venue keeps its old state and is tried on the next run. A bad
    search key or plan stops the Instagram search for the rest of the run
    only. Logs counts only. Returns counts."""
    from . import leadsheet

    now = now or datetime.now(timezone.utc)
    today = now.date()
    cap = daily_cap() if cap is None else cap
    with conn.cursor() as cur:
        cur.execute(_LOAD_SQL)
        names = [d.name for d in cur.description]
        checked = {r[0]: dict(zip(names, r)) for r in cur.fetchall()}
    due_keys = [k for k, c in sorted(checked.items(), key=lambda kv: kv[1]["next_check_at"]
                                     or now)
                if c["status"] == WAITING and c["next_check_at"] and c["next_check_at"] <= now]
    today_rows = leadsheet.load_rows(conn, today, with_contacts=False)
    due_rows = (leadsheet.load_rows(conn, None, open_only=True, venue_keys=due_keys,
                                    with_contacts=False) if due_keys else [])
    since = today - timedelta(days=BACKLOG_DAYS)
    backlog = [r for r in leadsheet.load_rows(conn, None, open_only=True, with_contacts=False)
               if r.get("queue_date") and r["queue_date"] >= since]
    work, over_cap = plan(today_rows, due_rows, backlog, checked, cap)

    counts = {"checked": 0, "new": 0, "recheck": 0, "backlog": 0, "reachable": 0,
              "newly_reachable": 0, "waiting": 0, "gave_up": 0, "over_cap": over_cap,
              "failed": 0, "instagram": int(instagram is not None),
              "instagram_search": int(search is not None), "searches": 0,
              "search_found": 0, "search_likely": 0}
    failures: dict[str, int] = {}
    today_keys = {r["venue_key"] for r in today_rows}
    for row, kind in work:
        if places is None:
            break
        try:
            result = check_venue(row, places, website, instagram, today, search=search)
        except ContactError as exc:
            counts["failed"] += 1
            failures[str(exc)] = failures.get(str(exc), 0) + 1
            if exc.fatal:
                places = None  # bad key or quota: stop calling it today
            continue
        for err in result.lookups_failed:
            failures[err] = failures.get(err, 0) + 1
            if instagram is not None and err.startswith("instagram failed"):
                instagram = None  # every lookup failure here is fatal
        if "instagram search" in result.fatal_lookups:
            search = None  # bad key or plan: stop searching today
        found = [ch for ch in result.channels if ch.kind == INSTAGRAM_SEARCH]
        counts["searches"] += int(result.searched)
        counts["search_found"] += len(found)
        counts["search_likely"] += sum(1 for ch in found if reachable(ch))
        state = next_state(checked.get(row["venue_key"]), result.reachable, now,
                           row["venue_key"] in today_keys)
        save(conn, row["venue_key"], result, state, now)
        conn.commit()
        counts["checked"] += 1
        counts[kind] += 1
        counts[state.status] += 1
        if state.newly_reachable_on == today and state.status == REACHABLE:
            counts["newly_reachable"] += 1
    if places is None and len(work) > counts["checked"] + counts["failed"]:
        counts["failed"] += len(work) - counts["checked"] - counts["failed"]
    for err, n in failures.items():
        log.warning("enrich FAILED %s x%d", err, n)  # "places failed (HTTP 403)": no values
    return counts
