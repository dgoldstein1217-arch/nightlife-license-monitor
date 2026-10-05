"""City of Chicago BACP pending liquor/PPA applications (6-week notice list).

Source: the BACP "Liquor and Public Place of Amusement Applications" pages,
a rolling list of liquor applications received in the past ~six weeks, posted
within 5 days of fee payment (Mun. Code 4-60-040(e))::

    https://webapps1.chicago.gov/bacplicenseapplications/liquor
    https://webapps1.chicago.gov/bacplicenseapplications/liqppa

Each page is plain server-rendered HTML with a single ``<table id=resultstable>``
(6 columns: Legal Name, Doing Business As, Address, Application Applied for,
Date of Payment, Ownership). The DataTables paging is client-side only, so one
GET per page captures every row; there is no server-side pagination to follow.
No per-row detail pages exist -- the only per-row link opens an
``ownership?acct=<acct>&site=<site>`` Owners/Officers popup, which is used as
the record's ``source_url`` (falling back to the list URL when absent).
``fetch`` performs exactly those two list GETs and never per-row requests.

ID STRATEGY: the ``showOwnership(acct,site)`` arguments look like a stable
application/account id, but they are NOT unique per row -- one account+site
routinely files several license-type rows (e.g. Outdoor Patio plus
Consumption On Premises), and sometimes the same type twice with different
payment dates. So ``source_record_id`` is ``"<acct>-<site>-<digest>"`` where
``<digest>`` is the first 12 hex chars of a sha256 over the normalized
(legal name, dba, address, license text, payment date); rows without an
ownership link use ``"noid-<digest>"``. This is deterministic across runs and
independent of row order.

Only the stdlib is used for parsing (same regex approach as wa_lcb.py).

Venue history: the list addresses carry no ZIP, so they are matched by city
(Chicago) and street against the BACP business-license dataset
(chicago_bacp.chicago_history). The ``acct`` in the ownership link is the
BACP account number, the owner id there.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import date
from typing import Iterable

from .. import stage
from ..http import Http
from ..models import Record, Snapshot, parse_date
from .base import Source

#: Official landing page for the dataset (provenance).
LANDING_URL = "https://webapps1.chicago.gov/bacplicenseapplications/"
#: Rolling list of liquor applications received in the past ~six weeks.
LIQUOR_URL = "https://webapps1.chicago.gov/bacplicenseapplications/liquor"
#: Rolling list of Public Place of Amusement applications (same window/shape).
PPA_URL = "https://webapps1.chicago.gov/bacplicenseapplications/liqppa"
#: Per-row Owners/Officers popup, relative to the list pages.
OWNERSHIP_BASE = "https://webapps1.chicago.gov/bacplicenseapplications/ownership"

LIST_URLS = (LIQUOR_URL, PPA_URL)

_COLUMNS = (
    "Legal Name",
    "Doing Business As",
    "Address",
    "Application Applied for",
    "Date of Payment",
)

_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr\s*>", re.S | re.I)
_TD_RE = re.compile(r"<td\b([^>]*)>(.*?)</td\s*>", re.S | re.I)
_TBODY_RE = re.compile(r"<tbody\b[^>]*>(.*?)</tbody\s*>", re.S | re.I)
_THEAD_RE = re.compile(r"<thead\b[^>]*>(.*?)</thead\s*>", re.S | re.I)
_TH_RE = re.compile(r"<th\b[^>]*>(.*?)</th\s*>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")
_OWNERSHIP_RE = re.compile(
    r"showOwnership\(\s*(\d+)\s*,\s*(\d+)\s*\)", re.I
)
#: The Ownership cell's link label, not a name.
_OWNERSHIP_LABEL = re.compile(r"(owners?\s*/?\s*(officers?)?|ownership|officers?)", re.I)
_DATA_ORDER_RE = re.compile(r"data-order\s*=\s*['\"]([^'\"]+)['\"]", re.I)
_ZIP_RE = re.compile(r"\b(\d{5})(?:\s*-\s*(\d{4}))?\b")
_SPLIT_TYPES_RE = re.compile(r"[;/|]")

# Best-first category order for multi-type rows.
PRIORITY = ("nightlife", "on_premise", "catering_event", "off_premise", "other")


def _part_category(part: str) -> str:
    """Classify one license-type fragment (already uppercased, hyphens->spaces)."""
    if (
        "TAVERN" in part
        or "LATE HOUR" in part
        or "AMUSEMENT" in part
    ):
        return "nightlife"
    if (
        "CONSUMPTION ON PREMISES" in part
        or "OUTDOOR PATIO" in part
    ):
        return "on_premise"
    if (
        "CATERER" in part
        or "CATERING" in part
        or "SPECIAL EVENT" in part
    ):
        return "catering_event"
    if "PACKAGE GOODS" in part or "PACKAGED GOODS" in part:
        return "off_premise"
    return "other"


def categorize(license_text: str | None) -> str:
    """Map an "Application Applied for" string to a category.

    Multi-type strings (``;``/``/``-separated) resolve to the most
    nightlife-relevant category: nightlife > on_premise > catering_event >
    off_premise > other. Anything unrecognized maps to ``"other"``.
    """
    if not license_text:
        return "other"
    best = len(PRIORITY) - 1
    for raw_part in _SPLIT_TYPES_RE.split(license_text.upper()):
        part = _WS_RE.sub(" ", raw_part.replace("-", " ")).strip()
        if not part:
            continue
        rank = PRIORITY.index(_part_category(part))
        if rank < best:
            best = rank
            if best == 0:
                break
    return PRIORITY[best]


def _decode(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("windows-1252")


def _text(cell_html: str) -> str:
    text = _TAG_RE.sub("", cell_html)
    text = html.unescape(text).replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def _norm(value: str) -> str:
    return _WS_RE.sub(" ", value.strip()).upper()


def _record_id(
    acct: str | None,
    site: str | None,
    legal: str,
    dba: str,
    address: str,
    license_text: str,
    date_text: str,
) -> str:
    # (acct, site) alone is not unique per row (one account+site files
    # several license-type rows), so the id binds them to the normalized
    # row fields. Deterministic and independent of row order.
    fingerprint = "|".join(
        _norm(v) for v in (acct or "", site or "", legal, dba, address,
                           license_text, date_text)
    )
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:12]
    if acct and site:
        return f"{int(acct)}-{int(site)}-{digest}"
    return f"noid-{digest}"


def account_of(source_record_id: str) -> str | None:
    """BACP account number from an "<acct>-<site>-<digest>" record id."""
    head = (source_record_id or "").split("-", 1)[0]
    return head if head.isdigit() else None


def _headers(page_html: str) -> list[str]:
    match = _THEAD_RE.search(page_html)
    if not match:
        return []
    return [_text(cell) for cell in _TH_RE.findall(match.group(1))]


class ChicagoPendingSource(Source):
    name = "chicago_bacp_pending"
    title = "Chicago BACP pending liquor/PPA applications (6-week notice list)"
    state = "IL"
    homepage = LANDING_URL
    tracks_removals = False  # rolling ~six-week window, not a stable pending list
    min_records = 5

    def stage(self, rec: Record) -> str | None:
        # Fee paid, on the six-week public notice list: past intake.
        return stage.IN_REVIEW

    def nightlife_license(self, rec: Record) -> tuple[str, ...]:
        text = _WS_RE.sub(" ", (rec.license_description or "").upper().replace("-", " "))
        found = []
        if "AMUSEMENT" in text:
            found.append("ppa")
        if "LATE HOUR" in text:
            found.append("late_hours")
        return tuple(found)

    def venue_history(self, http: Http, records: list[Record],
                      snapshots: list[Snapshot] | None = None,
                      today: date | None = None) -> dict:
        from .chicago_bacp import chicago_history

        return chicago_history(http, records, today or date.today(), owner_ids={
            r.source_record_id: account_of(r.source_record_id) for r in records})

    def contact(self, raw: dict) -> dict:
        """Names in the Ownership cell. The list shows only the
        Owners/Officers popup link there (its label is no name), and the
        popup is never fetched, so this is usually empty."""
        text = " ".join(str(raw.get("Ownership") or "").split())
        if not text or _OWNERSHIP_LABEL.fullmatch(text):
            return {}
        return self.people(*text.split(";"))

    def fetch(self, http: Http) -> list[Snapshot]:
        # The two list pages only; no per-row detail requests.
        return [http.get(url) for url in LIST_URLS]

    def parse(self, snapshots: list[Snapshot]) -> Iterable[Record]:
        for snapshot in snapshots:
            text = _decode(snapshot.body)
            headers = _headers(text)
            for tbody in _TBODY_RE.findall(text):
                for row_html in _TR_RE.findall(tbody):
                    cells = _TD_RE.findall(row_html)
                    if len(cells) < 5:
                        continue
                    values = [_text(inner) for _, inner in cells[:5]]
                    legal, dba, address, license_text, date_text = values
                    if not any(values):
                        continue
                    ownership_html = cells[5][1] if len(cells) > 5 else ""
                    link = _OWNERSHIP_RE.search(ownership_html)
                    acct, site = link.groups() if link else (None, None)
                    if link:
                        source_url = (
                            f"{OWNERSHIP_BASE}?acct={int(acct)}&site={int(site)}"
                        )
                    else:
                        source_url = snapshot.url or LIST_URLS[0]
                    app_date = parse_date(date_text)
                    if app_date is None:
                        order = _DATA_ORDER_RE.search(cells[4][0])
                        if order:
                            app_date = parse_date(order.group(1).strip())
                    zip_match = _ZIP_RE.search(address)
                    zip_code = None
                    if zip_match:
                        zip_code = zip_match.group(1)
                        if zip_match.group(2):
                            zip_code += "-" + zip_match.group(2)
                    raw = dict(zip(headers[:5], values)) if len(headers) >= 5 else {
                        name: value for name, value in zip(_COLUMNS, values)
                    }
                    raw["Ownership"] = _text(ownership_html) or None
                    yield Record(
                        source=self.name,
                        source_record_id=_record_id(
                            acct, site, legal, dba, address,
                            license_text, date_text,
                        ),
                        source_url=source_url,
                        legal_name=legal or None,
                        dba=dba or None,
                        license_type=license_text or None,
                        license_description=license_text or None,
                        application_type="NEW",
                        status="PENDING NOTICE",
                        application_date=app_date,
                        address=address or None,
                        city="CHICAGO",
                        state="IL",
                        zip=zip_code,
                        county="Cook",
                        category=categorize(license_text),
                        raw=raw,
                    )
