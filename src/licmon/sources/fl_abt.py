"""Florida DBPR Division of Alcoholic Beverages and Tobacco (ABT) connector.

Source: retail alcoholic beverage licensee extract, profession 4006,
refreshed daily:
    https://www2.myfloridalicense.com/sto/file_download/extracts/bd4006lic.csv

Format notes (verified against the real extract 2026-09-29, ~53k rows):
- CSV with every field double-quoted, CRLF line endings, latin-1 bytes.
- Header: "Board","Profession","Owner Name","Series","Modifier",
  "Mail Address 1".."Mail County","DBA","Location Address 1",
  "Location Address 2","Location Address 3","Location City",
  "Location State","Location ZIP","Location County","License Number",
  "Primary Status","Secondary Status","Original Licensure Date",
  "Effective Date","Expiration Date","Tax Stamp Designation",
  "Smoking Designation","Retail Tobacco Indicator".
- Location County is a numeric code, not a name; the 67-county code table
  is from the official "Understanding DBPR Codes" page
  (https://www2.myfloridalicense.com/alcoholic-beverages-and-tobacco/understanding-dbpr-codes/,
  fetched 2026-09-29; verified Broward=16, Hillsborough=39, Palm Beach=60,
  Pinellas=62, Dade=23).
- Series/modifier meanings are from the official ABT license-types list
  (https://www2.myfloridalicense.com/abt/rules_statutes/license_types.pdf,
  fetched 2026-09-29): 4COP/5COP/6COP/7COP/8COP are quota full-liquor
  on-premises licenses; SFS = Special Food Service (restaurant);
  S / SH = hotel/motel; 2COP/1COP = beer (& wine) on-premises;
  1APS/2APS/3PS/... = package (off-premises); 11C/11CG = clubs;
  13CT = caterer.
- Status codes are from the official layout page
  (https://www2.myfloridalicense.com/alcoholic-beverages-and-tobacco/public-records-layout-information/,
  fetched 2026-09-29): primary 20 = Current, secondary 20 = Active, etc.
- Data quirks seen in the real file: one row carries the literal string
  "NULL" as Modifier (treated as blank); some rows carry primary-series
  status codes (60, 61, ...) in the Secondary Status column and a blank
  Original Licensure Date (3973 rows); Location County is blank on a few
  rows. 128 license numbers appear twice: a transfer in progress, pairing
  the outgoing licensee (primary 20, secondary 35 = transfer pending)
  with the incoming one (primary 21 = temporary certificate, recent
  Original Licensure Date). The parser keeps the row with the latest
  Original Licensure Date so the new operator is the lead.

This file lists LICENSED businesses, not pending applications, and Florida
publishes no pending-application file. Each license therefore enters the
pipeline as application_type "NEW LICENSE" dated at its Original Licensure
Date, so newly issued licenses surface as new-venue leads.

Per-record deep links: DBPR's license search
(https://www.myfloridalicense.com/datamart/mainMenuFLDBPR.do) requires a
login/JS session, so no per-license GET URL could be verified with curl;
source_url is the extract URL for every record (verified 2026-09-29 that
the search landing page offers no stable per-license link).

Venue history (``venue_history``) needs no extra download: the same extract
lists every license, so a lead's premises is compared with the other rows
at that address (history.same_premises). A license number listed twice
with a different owner is a transfer in progress, so the incoming operator
is "New owner". A license is current when its status maps to Licensed or
Approved (stage.from_status); otherwise it ended on its Expiration Date.
Under ``licmon requalify --history`` the extract is downloaded once more.

material_fields: left at the default. The columns that churn on every
extract (Effective/Expiration dates, tax-stamp/smoking/tobacco flags, mail
address) live only in raw, outside MATERIAL_FIELDS, so no override is
needed.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import date

from .. import history, stage
from ..http import Http
from ..models import CATEGORIES, Record, Snapshot, parse_date
from .base import Source

EXPORT_URL = "https://www2.myfloridalicense.com/sto/file_download/extracts/bd4006lic.csv"
SOURCE_URL = EXPORT_URL

#: Location County code -> county NAME, from the official "Understanding
#: DBPR Codes" page (2026-09-29). Code 23 is "Dade" there; the connector
#: emits "MIAMI-DADE" to match metros.py. Codes 78/99 (Unknown),
#: 79/701-799 (out of state) and 80/801-899 (foreign) have no Florida
#: county and map to None.
COUNTY_CODES = {
    "11": "ALACHUA", "12": "BAKER", "13": "BAY", "14": "BRADFORD",
    "15": "BREVARD", "16": "BROWARD", "17": "CALHOUN", "18": "CHARLOTTE",
    "19": "CITRUS", "20": "CLAY", "21": "COLLIER", "22": "COLUMBIA",
    "23": "MIAMI-DADE", "24": "DESOTO", "25": "DIXIE", "26": "DUVAL",
    "27": "ESCAMBIA", "28": "FLAGLER", "29": "FRANKLIN", "30": "GADSDEN",
    "31": "GILCHRIST", "32": "GLADES", "33": "GULF", "34": "HAMILTON",
    "35": "HARDEE", "36": "HENDRY", "37": "HERNANDO", "38": "HIGHLANDS",
    "39": "HILLSBOROUGH", "40": "HOLMES", "41": "INDIAN RIVER",
    "42": "JACKSON", "43": "JEFFERSON", "44": "LAFAYETTE", "45": "LAKE",
    "46": "LEE", "47": "LEON", "48": "LEVY", "49": "LIBERTY",
    "50": "MADISON", "51": "MANATEE", "52": "MARION", "53": "MARTIN",
    "54": "MONROE", "55": "NASSAU", "56": "OKALOOSA", "57": "OKEECHOBEE",
    "58": "ORANGE", "59": "OSCEOLA", "60": "PALM BEACH", "61": "PASCO",
    "62": "PINELLAS", "63": "POLK", "64": "PUTNAM", "65": "ST. JOHNS",
    "66": "ST. LUCIE", "67": "SANTA ROSA", "68": "SARASOTA",
    "69": "SEMINOLE", "70": "SUMTER", "71": "SUWANNEE", "72": "TAYLOR",
    "73": "UNION", "74": "VOLUSIA", "75": "WAKULLA", "76": "WALTON",
    "77": "WASHINGTON",
}

#: Primary status code -> human text (official layout page, 2026-09-29).
#: Wording keeps qualify.py's ROUTINE_STATUSES substrings working:
#: "Withdrawn" matches WITHDRAW, "Denied" matches DENIED, "Expired"
#: matches EXPIRED, "Revoked ..." matches REVOK.
PRIMARY_STATUS = {
    "10": "Applicant (application in process)",
    "11": "Withdrawn (application withdrawn)",
    "12": "Expired (application expired)",
    "13": "Denied (not qualified)",
    "14": "Denied (disciplined)",
    "18": "Eligible for exam",
    "19": "Exam taken",
    "20": "Current",
    "21": "Temporary certificate",
    "22": "Transfer approved",
    "30": "Current (probation)",
    "31": "Current (obligations)",
    "32": "Current (conditional)",
    "41": "Escrow",
    "42": "Suspended",
    "45": "Delinquent",
    "46": "Voluntary relinquishment",
    "47": "Relinquishment (discipline)",
    "60": "Null and void",
    "61": "Revoked",
    "62": "Revoked without prejudice to transfer",
    "63": "Revoked without prejudice to location",
    "64": "Revoked with prejudice to location",
    "80": "Deceased/closed",
    "90": "Conversion",
    "98": "Error",
    "99": "Deleted",
}

#: Secondary status code -> human text (official layout page, 2026-09-29).
SECONDARY_STATUS = {
    "10": "Inactive",
    "20": "Active",
    "21": "Litigation pending",
    "30": "Tax non-payment alert",
    "35": "Transfer pending",
    "37": "Pending payment",
    "39": "Administrative hold",
}

#: Series -> human description (official ABT license-types list, 2026-09-29).
SERIES_DESCRIPTIONS = {
    "1APS": "Package sales, beer only (off-premises)",
    "2APS": "Package sales, beer and wine (off-premises)",
    "3PS": "Quota package sales, beer/wine/liquor (off-premises)",
    "3APS": "Quota package sales, beer/wine/liquor (off-premises)",
    "3BPS": "Quota package sales, beer/wine/liquor (off-premises)",
    "3CPS": "Quota package sales, beer/wine/liquor (off-premises)",
    "3DPS": "Quota package sales, beer/wine/liquor (off-premises)",
    "1COP": "Beer on-premises",
    "2COP": "Beer and wine on-premises",
    "4COP": "Quota liquor on-premises (full liquor)",
    "5COP": "Quota liquor on-premises (full liquor)",
    "6COP": "Quota liquor on-premises (full liquor)",
    "7COP": "Quota liquor on-premises (full liquor)",
    "8COP": "Quota liquor on-premises (full liquor)",
    "11AL": "American Legion post club",
    "11C": "Club (fraternal/lodge)",
    "11CG": "Golf club",
    "11CS": "Club (Hillsborough County special act)",
    "11CT": "Museum beverage license",
    "11PA": "Performing arts facility",
    "12RT": "Pari-mutuel facility",
    "13CT": "Caterer",
    "14BC": "Bottle club (no sales)",
    "CEP": "Culinary education program",
    "HBX": "Horse breeders association",
    "RTS": "Railroad/train station vendor",
    "ODP": "Temporary permit",
    "SODP": "Temporary permit (special act)",
    "TXP": "Temporary premises extension",
    "STXP": "Temporary premises extension (stadium district)",
    "TCP": "Temporary convention permit",
    "NMSP": "Non-member sales permit (golf club)",
    "SSL": "Special sales license",
    "TSE": "Temporary special event permit",
}

#: Class modifier -> human description (official ABT license-types list).
MODIFIER_DESCRIPTIONS = {
    "SFS": "Special Food Service (restaurant)",
    "S": "Hotel/motel",
    "SH": "Historic hotel/motel",
    "SBX": "Bowling alley",
    "SAL": "Airport",
    "SCX": "Civic center",
    "SCF": "Civic center facility",
    "SCC": "County facility",
    "SA": "Special Act",
    "SAX": "Special Act (limited location)",
    "SR": "Special restaurant (prior law)",
    "SPX": "Excursion/charter boat",
    "SPXE": "Excursion/charter boat",
    "HBX": "Horse breeders association",
    "MFDV": "Food truck park",
    "DEV": "Destination entertainment venue",
    "EVNT": "Event center",
}

#: Ticketed-venue series and modifiers -> qualify.NIGHTLIFE_LICENSE_POINTS
#: keys (official license-types list; counts in the 2026-09-29 extract:
#: 11PA 117, 12RT 24, SCX 97, SCF 1; EVNT and DEV none yet). SBX bowling
#: alleys stay B and SCC county facilities are left out.
TICKETED_SERIES = {"11PA": "theater", "12RT": "sports_venue"}
TICKETED_MODIFIERS = {"SCX": "event_venue", "SCF": "event_venue",
                      "EVNT": "event_venue", "DEV": "event_venue"}

#: A Florida license counts as a newly licensed lead for this many days.
LICENSED_FRESH_DAYS = 60

_QUOTA_COP = {"4COP", "5COP", "6COP", "7COP", "8COP"}
_PACKAGE = {"1APS", "2APS", "3PS", "3APS", "3BPS", "3CPS", "3DPS"}
_TEMPORARY = {"ODP", "SODP", "TXP", "STXP", "TCP", "NMSP", "SSL", "TSE"}

assert set(_PACKAGE) <= set(SERIES_DESCRIPTIONS)
assert set(_TEMPORARY) <= set(SERIES_DESCRIPTIONS)


def categorize(series: str | None, modifier: str | None) -> str:
    """Map an ABT series + modifier to one of models.CATEGORIES.

    Choices (per official license-types list):
    - Blank-modifier quota COP (4COP etc.) is the classic full-liquor bar,
      so nightlife; with restaurant modifiers (SFS/SR/MFDV) it operates as
      a restaurant -> on_premise; with hotel modifiers (S/SH) -> hotel.
    - 2COP/1COP (beer & wine on-premises) and club series (11C/11CG/11CS/
      11AL) serve drinks on site -> on_premise. SBX bowling alleys pour
      full liquor alongside amusement, closest to the golf-club case ->
      on_premise.
    - 13CT caterers, performing-arts/museum venues (11PA/11CT) and civic/
      county event facilities (SCX/SCF/SCC/EVNT/DEV) are event service ->
      catering_event.
    - Package series (APS/PS) -> off_premise (excluded downstream).
    - Pari-mutuel/cardroom (12RT), bottle clubs (14BC, no sales allowed),
      airport (SAL), boats (SPX), horse-breeder (HBX) and rail (RTS)
      licenses, Special Act (SA/SAX) licenses and unknown modifiers ->
      other. Unknown modifiers stay other (conservative) rather than
      inheriting nightlife.
    - Temporary permits -> temporary (excluded downstream).
    - The 4006 retail extract has no brewery/brewpub/manufacturer series
      (those are profession 4005, a separate file), so nothing maps to
      hospitality_mfg or wholesale_mfg here.
    """
    series_code = (series or "").upper().strip()
    mod = (modifier or "").upper().strip()
    if mod == "NULL" or mod == "NONE":
        # Literal "NULL" appears in the real file; it means no modifier.
        mod = ""
    if series_code in _TEMPORARY:
        return "temporary"
    if series_code in _PACKAGE:
        return "off_premise"
    if series_code in ("11C", "11CS", "11AL", "11CG"):
        return "on_premise"
    if series_code in ("11CT", "11PA"):
        return "catering_event"
    if series_code == "13CT":
        return "catering_event"
    if series_code == "CEP":
        # CEP culinary schools serve food and drink on site, most like
        # a restaurant operation.
        return "on_premise"
    if series_code in ("12RT", "14BC", "HBX", "RTS"):
        return "other"
    if series_code in ("1COP", "2COP"):
        return "on_premise"
    if series_code in _QUOTA_COP:
        if mod in ("SFS", "SR", "MFDV"):
            return "on_premise"
        if mod in ("S", "SH"):
            return "hotel"
        if mod == "SBX":
            return "on_premise"
        if mod in ("SCX", "SCF", "SCC", "EVNT", "DEV"):
            return "catering_event"
        if mod == "":
            return "nightlife"
        return "other"
    return "other"


assert categorize("4COP", "") == "nightlife"
assert categorize("x", None) == "other"
assert "nightlife" in CATEGORIES


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def county_name(code: str | None) -> str | None:
    """Map a Location County code to its county NAME (or None)."""
    if code is None:
        return None
    return COUNTY_CODES.get(str(code).strip())


def status_text(primary: str | None, secondary: str | None) -> str | None:
    """Human status text; secondary shown unless it is plain Active."""
    first = PRIMARY_STATUS.get((primary or "").strip(), None) if primary and primary.strip() else None
    if first is None and primary and primary.strip():
        first = f"Code {primary.strip()}"
    second_code = (secondary or "").strip()
    second = None
    if second_code and second_code != "20":
        second = SECONDARY_STATUS.get(second_code, PRIMARY_STATUS.get(second_code))
        if second is None:
            second = f"Code {second_code}"
    if first and second:
        return f"{first} / {second}"
    return first or second


def license_type_code(series: str | None, modifier: str | None) -> str | None:
    series_code = (series or "").strip().upper()
    mod = (modifier or "").strip().upper()
    if mod in ("", "NULL", "NONE"):
        mod = ""
    if not series_code:
        return None
    return f"{series_code}-{mod}" if mod else series_code


def license_description(series: str | None, modifier: str | None) -> str | None:
    series_code = (series or "").strip().upper()
    mod = (modifier or "").strip().upper()
    if mod in ("NULL", "NONE"):
        mod = ""
    parts = []
    if series_code:
        parts.append(SERIES_DESCRIPTIONS.get(series_code, series_code))
    if mod:
        parts.append(MODIFIER_DESCRIPTIONS.get(mod, mod))
    return "; ".join(parts) or None


def _orig_key(row: dict) -> tuple[int, int, int]:
    """Sort key for duplicate rows: latest Original Licensure Date wins."""
    parsed = parse_date((row.get("Original Licensure Date") or "").strip())
    if parsed is None:
        return (0, 0, 0)
    return (parsed.year, parsed.month, parsed.day)


class FlAbtSource(Source):
    name = "fl_abt_licenses"
    title = "Florida ABT retail alcoholic beverage licenses (daily extract)"
    state = "FL"
    homepage = ("https://www2.myfloridalicense.com/alcoholic-beverages-and-tobacco/"
                "daily-license-status-reporting-data/")
    tracks_removals = True
    min_records = 20000

    def stage(self, rec: Record) -> str | None:
        return stage.from_status(rec.status)

    def stage_counts(self, rec: Record, today: date) -> bool:
        # Every row is a license, most of them years old. Only a recent
        # issue date makes "Licensed" news.
        return bool(rec.application_date
                    and (today - rec.application_date).days <= LICENSED_FRESH_DAYS)

    def nightlife_license(self, rec: Record) -> tuple[str, ...]:
        # Blank-modifier quota liquor license: the classic full-liquor bar.
        # license_type is "4COP" etc. only when there is no modifier.
        code = (rec.license_type or "").upper()
        series, _, modifier = code.partition("-")
        found = []
        if code in _QUOTA_COP:
            found.append("full_liquor_bar")
        if series in TICKETED_SERIES:
            found.append(TICKETED_SERIES[series])
        if modifier in TICKETED_MODIFIERS:
            found.append(TICKETED_MODIFIERS[modifier])
        return tuple(found)

    def contact(self, raw: dict) -> dict:
        out = self.people(_clean(raw.get("Owner Name")))
        parts = [_clean(raw.get(f"Mail Address {i}")) for i in (1, 2, 3)]
        street = " ".join(p for p in parts if p)
        if not street:
            return out
        city, state, zip_code = (_clean(raw.get(k)) for k in
                                 ("Mail City", "Mail State", "Mail ZIP"))
        tail = " ".join(p for p in (state, zip_code) if p)
        return {**out, "mailing_address": ", ".join(p for p in (street, city, tail) if p)}

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
        nearby: dict[tuple, list[tuple[dict, history.Prior]]] = {}
        for snapshot in snapshots or self.fetch(http):
            text = snapshot.body.decode("latin-1")
            for row in csv.DictReader(io.StringIO(text)):
                key = _near_key(_row_address(row), row.get("Location ZIP"))
                if key in wanted:
                    nearby.setdefault(key, []).append((row, license_prior(row)))
        out = {}
        for rec in records:
            candidates = [
                (row, prior) for row, prior in nearby.get(_near_key(rec.address, rec.zip), [])
                # This license's own row; a second row under another owner
                # is the outgoing licensee of a transfer and stays.
                if not (prior.license_id == rec.source_record_id
                        and history.same_owner(prior.owner, None, rec.legal_name, None))]
            priors = history.match_priors(
                rec, candidates,
                lambda row: (_row_address(row), row.get("Location ZIP"),
                             row.get("Location City")))
            out[rec.source_record_id] = history.classify(
                rec.legal_name, None, rec.application_date, priors, today)
        return out

    def parse(self, snapshots: list[Snapshot]) -> Iterable[Record]:
        for snapshot in snapshots:
            yield from self._parse_snapshot(snapshot)

    def _parse_snapshot(self, snapshot: Snapshot) -> Iterable[Record]:
        # latin-1 per the official file; decoding never fails on it.
        text = snapshot.body.decode("latin-1")
        reader = csv.DictReader(io.StringIO(text))
        groups: dict[str, dict] = {}
        order: list[str] = []
        for row in reader:
            if row is None:
                continue
            license_number = _clean(row.get("License Number"))
            if not license_number:
                continue
            if license_number not in groups:
                groups[license_number] = row
                order.append(license_number)
            else:
                # Duplicate license numbers are transfers in progress
                # (outgoing licensee + incoming temporary certificate).
                # Keep the row with the latest Original Licensure Date,
                # i.e. the incoming operator; ties keep the first row.
                prev = groups[license_number]
                if _orig_key(row) > _orig_key(prev):
                    groups[license_number] = row
        for license_number in order:
            row = groups[license_number]
            series = _clean(row.get("Series"))
            modifier = _clean(row.get("Modifier"))
            if modifier and modifier.upper() in ("NULL", "NONE"):
                modifier = None
            addr_parts = [
                _clean(row.get("Location Address 1")),
                _clean(row.get("Location Address 2")),
                _clean(row.get("Location Address 3")),
            ]
            address = " ".join(p for p in addr_parts if p) or None
            yield Record(
                source=self.name,
                source_record_id=license_number,
                source_url=SOURCE_URL,
                legal_name=_clean(row.get("Owner Name")),
                dba=_clean(row.get("DBA")),
                license_type=license_type_code(series, modifier),
                license_description=license_description(series, modifier),
                # Licensed-business extract, not pending applications:
                # every row counts as a new license so qualify.py's
                # \bNEW\b pattern credits "new application".
                application_type="NEW LICENSE",
                status=status_text(
                    _clean(row.get("Primary Status")),
                    _clean(row.get("Secondary Status")),
                ),
                application_date=parse_date(
                    _clean(row.get("Original Licensure Date"))),
                address=address,
                city=_clean(row.get("Location City")),
                state=_clean(row.get("Location State")) or "FL",
                zip=_clean(row.get("Location ZIP")),
                county=county_name(_clean(row.get("Location County"))),
                category=categorize(series, modifier),
                raw=dict(row),
            )


def _row_address(row: dict) -> str | None:
    parts = [_clean(row.get(f"Location Address {i}")) for i in (1, 2, 3)]
    return " ".join(p for p in parts if p) or None


def _near_key(address: str | None, zip_code: str | None) -> tuple:
    return ((zip_code or "").strip()[:5], history.house_number(address))


def license_prior(row: dict) -> history.Prior:
    """One extract row as a history.Prior (memory only)."""
    status = status_text(_clean(row.get("Primary Status")), _clean(row.get("Secondary Status")))
    active = stage.from_status(status) in (stage.LICENSED, stage.APPROVED)
    return history.Prior(license_id=_clean(row.get("License Number")),
                         owner=_clean(row.get("Owner Name")), active=active,
                         issued=parse_date(_clean(row.get("Original Licensure Date"))),
                         ended=None if active else parse_date(
                             _clean(row.get("Expiration Date"))))
