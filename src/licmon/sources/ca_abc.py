"""California ABC Daily Data Export connector (pending/new applications).

Source: California ABC Daily Data Export (CSV inside a zip), refreshed each
business day ~7am PT:
    https://www.abc.ca.gov/wp-content/uploads/DailyExport-CSV.zip

Format notes (verified against the real export 2026-09-28):
- The zip holds one file (ABC-DailyDataExport.csv, ~28 MB unzipped).
- First line is a title like
  ``"Updated Monday 28th of September 2026 03:50:28 AM"`` (UTF-8 BOM).
- Second line is the header; some header names carry leading spaces
  (e.g. ``" Prem Addr 2"``) so header names are stripped.
- Blank values are a single space (``" "``).
- Only rows with ``"Lic or App" == "APP"`` are applications; ``LIC`` rows
  are existing licenses and are excluded.
- Rows share a ``File Number`` when one application covers several license
  types; one Record is emitted per file number.

License-type descriptions were verified against the official list at
https://www.abc.ca.gov/licensing/license-types/ (fetched 2026-09-29).

Per-record deep links: ``source_url`` is the official single-license lookup
page, ``...single-license/?RPTTYPE=12&LICENSE=<file number without leading
zeros>``. Verified in a browser 2026-09-29 (renders the application's owner,
business name and address). The page sits behind Cloudflare bot protection,
so the pipeline never fetches it; it is only a link for the human reviewer.
The raw export URL is kept in ``raw["export_url"]``.

Venue history (``venue_history``): the export has no filing type, but the
same file lists existing licenses (``LIC`` rows: Type Status ACTIVE, SUREND,
REVPEN, SUSPEN, PEND, R64B; Type Orig Iss Date and Expir Date as
``12-JUN-2027``; verified 2026-09-30). A lead's premises is compared with
the LIC rows at that address, one prior per file number. ACTIVE, SUSPEN
(suspended) and REVPEN (revocation pending) licenses are still held; others
ended on their Expir Date. A LIC row with the application's own file number
is the application's own license and is left out unless its owner differs
(a transfer that kept the file number). On 2026-09-30 about 60% of the
15.6k application file numbers had no license at the address, 34% had an
active one under another owner, 5% under the same owner.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable
from datetime import date

from .. import history, stage
from ..http import Http
from ..models import CATEGORIES, Record, Snapshot, parse_date
from .base import Source

EXPORT_URL = "https://www.abc.ca.gov/wp-content/uploads/DailyExport-CSV.zip"

#: Human-facing per-record lookup (see module doc). Never fetched by the pipeline.
LOOKUP_URL = ("https://www.abc.ca.gov/licensing/license-lookup/single-license/"
              "?RPTTYPE=12&LICENSE={}")
SOURCE_URL = EXPORT_URL


def lookup_url(file_number: str) -> str:
    return LOOKUP_URL.format(int(file_number) if file_number.isdigit() else file_number)

#: Official license-type code -> human description (abc.ca.gov, 2026-09-29).
LICENSE_TYPES = {
    "01": "Beer Manufacturer",
    "02": "Winegrower",
    "03": "Brandy Manufacturer",
    "04": "Distilled Spirits Manufacturer",
    "05": "Distilled Spirits Manufacturer's Agent",
    "06": "Still",
    "07": "Rectifier",
    "08": "Wine Rectifier",
    "09": "Beer and Wine Importer",
    "10": "Beer and Wine Importer's General",
    "11": "Brandy Importer",
    "12": "Distilled Spirits Importer",
    "13": "Distilled Spirits Importer's General",
    "14": "Public Warehouse",
    "15": "Customs Broker",
    "16": "Wine Broker",
    "17": "Beer and Wine Wholesaler",
    "18": "Distilled Spirits Wholesaler",
    "19": "Industrial Alcohol Dealer",
    "20": "Off-Sale Beer & Wine",
    "21": "Off-Sale General",
    "22": "Wine Blender",
    "23": "Small Beer Manufacturer",
    "24": "Distilled Spirits Rectifier's General",
    "25": "California Brandy Wholesaler",
    "26": "Out-of-State Beer Manufacturer's Certificate",
    "27": "California Winegrower's Agent",
    "28": "Out-of-State Distilled Spirits Shipper's Certificate",
    "29": "Wine Grape Grower's Storage",
    "31": "Special Daily (beer, wine, distilled spirits)",
    "34": "Daily Beer and Wine",
    "37": "Daily General",
    "40": "On-Sale Beer",
    "41": "On-Sale Beer & Wine - Eating Place",
    "42": "On-Sale Beer & Wine - Public Premises",
    "43": "On-Sale Beer and Wine Train",
    "44": "On-Sale Beer Fishing Party Boat",
    "45": "On-Sale Beer and Wine Boat",
    "46": "On-Sale Beer and Wine Airplane",
    "47": "On-Sale General - Eating Place",
    "48": "On-Sale General - Public Premises",
    "49": "On-Sale General - Seasonal",
    "50": "On-Sale General Club",
    "51": "Club",
    "52": "Veteran\u2019s Club",
    "53": "On-Sale General Train",
    "54": "On-Sale General Boat",
    "55": "On-Sale General Airplane",
    "56": "On-Sale General Vessel 1000 Tons",
    "57": "Special On-Sale General",
    "58": "Caterer's Permit",
    "59": "On-Sale Beer and Wine - Seasonal",
    "60": "On-Sale Beer - Seasonal",
    "61": "On-Sale Beer - Public Premises",
    "62": "On-Sale General Dockside, 7000 tons",
    "63": "On-Sale Special Beer and Wine Hospital",
    "64": "Special On-Sale General for Nonprofit Theater Company",
    "65": "Special On-Sale Beer and Wine Symphony",
    "66": "Controlled Access Cabinet Permits",
    "67": "Bed and Breakfast Inn",
    "68": "Portable Bar License",
    "69": "Special On-Sale Beer and Wine Theater",
    "70": "On-Sale General - Restrictive Service",
    "71": "Special On-Sale General for a For-Profit Theater "
          "within the City and County of San Francisco",
    "72": "Special On-Sale General for a For-Profit Theater "
          "within the county of Napa",
    "73": "Special Non-Profit Sales License",
    "74": "Craft Distiller",
    "75": "Brewpub-Restaurant",
    "76": "On-Sale General Maritime Museum Association",
    "77": "Event Permit",
    "78": "On-Sale General for Wine, Food and Art Cultural Museum, "
          "and Educational Center",
    "79": "Certified Farmers' Market Permit",
    "80": "Bed and Breakfast Inn \u2013 General",
    "81": "Wine Sales Event Permit",
    "82": "Wine Direct Shipper Permit",
    "83": "General On-Sale License to Caterer",
    "84": "Certified Farmers' Market Beer Sales Permit",
    "85": "Limited Off-Sale - Wine License",
    "86": "Instructional Tasting License",
    "87": "Special On-Sale General License for Specified Census Tracts "
          "in the City/County of San Francisco",
    "88": "Special On-Sale General License for a For-Profit Cemetery "
          "with Specified Characteristics",
    "90": "On-Sale General \u2013 Music Venue",
    "91": "Beer Manufacturer\u2019s Caterer's Permit",
    "93": "Estate Tasting Event Permit",
    "94": "Craft Distillers Direct Shipper Permit",
    "99": "On-Sale General for Special Use",
}

# Category sets, checked in CATEGORY_PRIORITY order (most
# nightlife-relevant first). Type 75 (brewpub-restaurant) sits in
# hospitality_mfg: it is a manufacturer/retail hybrid with a tasting-room
# character, and hospitality_mfg explicitly covers brewpubs. Type 90 (music
# venue) is nightlife in spirit (bar/nightclub/amusement). Types 67/80
# (bed & breakfast inns) join 66/70 under hotel.
_NIGHTLIFE = {"40", "42", "48", "61", "90"}
_ON_PREMISE = {"41", "47", "49", "50", "51", "52", "57", "59", "60", "69", "71", "72"}
_HOSPITALITY_MFG = {"01", "02", "04", "23", "74", "75"}
_CATERING_EVENT = {"58", "64", "77", "81", "83", "93"}
_HOTEL = {"66", "67", "70", "80"}
_OFF_PREMISE = {"20", "21", "79", "84", "85", "86"}
_WHOLESALE_MFG = {
    "03", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14",
    "15", "16", "17", "18", "19", "22", "24", "25", "26", "27", "28",
    "29", "82", "94",
}
_TEMPORARY = {"31", "34", "37"}

CATEGORY_PRIORITY: tuple[tuple[str, frozenset[str]], ...] = (
    ("nightlife", frozenset(_NIGHTLIFE)),
    ("on_premise", frozenset(_ON_PREMISE)),
    ("hospitality_mfg", frozenset(_HOSPITALITY_MFG)),
    ("catering_event", frozenset(_CATERING_EVENT)),
    ("hotel", frozenset(_HOTEL)),
    ("off_premise", frozenset(_OFF_PREMISE)),
    ("wholesale_mfg", frozenset(_WHOLESALE_MFG)),
    ("temporary", frozenset(_TEMPORARY)),
)

assert all(cat in CATEGORIES for cat, _ in CATEGORY_PRIORITY)


# Nightlife license codes -> qualify.NIGHTLIFE_LICENSE_POINTS keys. The
# theater types (64 nonprofit theater company, 69 beer and wine theater,
# 71/72 for-profit theaters) sell tickets by definition. The code table has
# no stadium or sports type: CA stadiums file ordinary on-sale types, so the
# name decides those.
NIGHTLIFE_CODES = {"48": "public_premises", "90": "music_venue",
                   "64": "theater", "69": "theater", "71": "theater", "72": "theater"}


def categorize(codes: list[str]) -> str:
    """Map license-type codes to one of models.CATEGORIES.

    When a file carries several types, the most nightlife-relevant wins:
    nightlife > on_premise > hospitality_mfg > catering_event > hotel >
    off_premise > wholesale_mfg > temporary > other.
    """
    have = {str(code).strip() for code in codes if str(code).strip()}
    for category, members in CATEGORY_PRIORITY:
        if have & members:
            return category
    return "other"


def _clean(value: object) -> str | None:
    """Turn CSV blanks (``" "``) into None, else stripped text."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class CaAbcSource(Source):
    name = "ca_abc_applications"
    title = "California ABC pending/new applications (daily export)"
    state = "CA"
    homepage = "https://www.abc.ca.gov/licensing/licensing-reports/"
    tracks_removals = True
    min_records = 1000

    def stage(self, rec: Record) -> str | None:
        # "Type Status" per license type, comma-joined: PEND or ACTIVE.
        found = []
        for part in (rec.status or "").upper().split(","):
            part = part.strip()
            if part == "ACTIVE":
                found.append(stage.LICENSED)
            elif part == "PEND":
                found.append(stage.RECEIVED)
            else:
                found.append(stage.from_status(part))
        return stage.best(found)

    def nightlife_license(self, rec: Record) -> tuple[str, ...]:
        codes = {c.strip() for c in (rec.license_type or "").split(",")}
        return tuple(dict.fromkeys(key for code, key in NIGHTLIFE_CODES.items()
                                   if code in codes))

    def contact(self, raw: dict) -> dict:
        rows = raw.get("rows") or []
        out = self.people(*(_clean(row.get("Primary Name")) for row in rows))
        for row in rows:
            parts = [_clean(row.get(k)) for k in ("Mail Addr 1", "Mail Addr 2")]
            street = " ".join(p for p in parts if p)
            if not street:
                continue
            city, state, zip_code = (_clean(row.get(k)) for k in
                                     ("Mail City", "Mail State", "Mail Zip"))
            tail = " ".join(p for p in (state, zip_code) if p)
            line = ", ".join(p for p in (street, city, tail) if p)
            return {**out, "mailing_address": line}
        return out

    def fetch(self, http: Http) -> list[Snapshot]:
        return [http.get(EXPORT_URL)]

    def venue_history(self, http: Http, records: list[Record],
                      snapshots: list[Snapshot] | None = None,
                      today: date | None = None) -> dict:
        today = today or date.today()
        records = [r for r in records if r.zip and history.parse_address(r.address)]
        if not records:
            return {}
        wanted = {_near_key(r.address, r.zip) for r in records}
        files: dict[str, list[dict]] = {}
        for snapshot in snapshots or self.fetch(http):
            for row in _export_rows(snapshot):
                if _clean(row.get("Lic or App")) != "LIC":
                    continue
                if _near_key(_row_address(row), row.get("Prem Zip")) in wanted:
                    files.setdefault(_clean(row.get("File Number")) or "", []).append(row)
        licenses: dict[tuple, list] = {}
        for rows in files.values():
            key = _near_key(_row_address(rows[0]), rows[0].get("Prem Zip"))
            licenses.setdefault(key, []).append((rows[0], license_prior(rows)))
        out = {}
        for rec in records:
            candidates = [(row, p) for row, p in licenses.get(_near_key(rec.address, rec.zip), [])
                          if not (p.license_id == rec.source_record_id
                                  and history.same_owner(p.owner, None, rec.legal_name, None))]
            priors = history.match_priors(
                rec, candidates,
                lambda row: (_row_address(row), row.get("Prem Zip"), row.get("Prem City")))
            out[rec.source_record_id] = history.classify(
                rec.legal_name, None, rec.application_date, priors, today)
        return out

    def parse(self, snapshots: list[Snapshot]) -> Iterable[Record]:
        for snapshot in snapshots:
            yield from self._parse_snapshot(snapshot)

    def _parse_snapshot(self, snapshot: Snapshot) -> Iterable[Record]:
        groups: dict[str, list[dict]] = {}
        for row in _export_rows(snapshot):
            lic_or_app = _clean(row.get("Lic or App"))
            if lic_or_app != "APP":
                continue
            file_number = _clean(row.get("File Number"))
            if not file_number:
                continue
            groups.setdefault(file_number, []).append(row)

        for file_number in sorted(groups):
            rows = groups[file_number]
            codes = sorted({
                code for code in
                (_clean(r.get("License Type")) for r in rows) if code
            })
            statuses = sorted({
                status for status in
                (_clean(r.get("Type Status")) for r in rows) if status
            })
            first_row = rows[0]
            addr1 = _clean(first_row.get("Prem Addr 1"))
            addr2 = _clean(first_row.get("Prem Addr 2"))
            if addr1 and addr2:
                address = f"{addr1} {addr2}"
            else:
                address = addr1
            yield Record(
                source=self.name,
                source_record_id=file_number,  # leading zeros kept
                source_url=lookup_url(file_number),
                legal_name=_clean(first_row.get("Primary Name")),
                dba=_clean(first_row.get("DBA Name")),
                license_type=",".join(codes) or None,
                license_description=",".join(
                    LICENSE_TYPES.get(code, code) for code in codes
                ) or None,
                application_type=None,  # the export carries none
                status=",".join(statuses) or None,
                application_date=None,  # the export carries none
                address=address,
                city=_clean(first_row.get("Prem City")),
                state=_clean(first_row.get("Prem State")) or "CA",
                zip=_clean(first_row.get("Prem Zip")),  # ZIP+4 kept as given
                county=_clean(first_row.get("Prem County")),
                category=categorize(codes),
                raw={"export_url": EXPORT_URL, "rows": rows},
            )


def _export_rows(snapshot: Snapshot) -> Iterable[dict]:
    """Every data row of the export (APP and LIC), header names stripped."""
    with zipfile.ZipFile(io.BytesIO(snapshot.body)) as archive:
        names = archive.namelist()
        csv_names = [n for n in names if n.lower().endswith(".csv")]
        picked = csv_names[0] if csv_names else (names[0] if names else None)
        if picked is None:
            return
        raw_bytes = archive.read(picked)
    # utf-8-sig consumes the BOM; replace keeps one bad byte from
    # killing a ~28 MB file.
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    stream = io.StringIO(text)
    first = stream.readline()
    if "File Number" in first:  # pragma: no cover - real exports carry a title
        stream = io.StringIO(text)
    reader = csv.DictReader(stream)
    if not reader.fieldnames:
        return
    reader.fieldnames = [(h or "").strip() for h in reader.fieldnames]
    yield from reader


def _row_address(row: dict) -> str | None:
    parts = [_clean(row.get("Prem Addr 1")), _clean(row.get("Prem Addr 2"))]
    return " ".join(p for p in parts if p) or None


def _near_key(address: str | None, zip_code: str | None) -> tuple:
    return ((zip_code or "").strip()[:5], history.house_number(address))


#: LIC Type Status values for a license that is still held.
HELD_STATUSES = {"ACTIVE", "SUSPEN", "REVPEN"}


def license_prior(rows: list[dict]) -> history.Prior:
    """One file number's LIC rows as a history.Prior (memory only)."""
    active = any(_clean(r.get("Type Status")) in HELD_STATUSES for r in rows)
    issued = [d for d in (parse_date(_clean(r.get("Type Orig Iss Date"))) for r in rows) if d]
    ends = [d for d in (parse_date(_clean(r.get("Expir Date"))) for r in rows) if d]
    return history.Prior(license_id=_clean(rows[0].get("File Number")),
                         owner=_clean(rows[0].get("Primary Name")), active=active,
                         issued=min(issued) if issued else None,
                         ended=None if active or not ends else max(ends))
