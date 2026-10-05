"""City of Chicago BACP business licenses: liquor and amusement applications.

Dataset: "Business Licenses" on data.cityofchicago.org (r5kz-chrr). Rows are
license applications (new issue, change of location, expansion, ...). The city
publishes rows around issuance, so this is a rolling window of recent
non-renewal liquor/amusement applications rather than a pending list.

Venue history: expansion and change-of-activity filings (C_EXPA, C_CAPA,
C_SBA) are an existing licensee changing its own license, so "Adding a
permit" straight from the filing type. New issues and changes of location
are checked against the same dataset (``chicago_history``): other liquor or
amusement licenses at the premises, grouped by license_number (one row per
license term), owner id = account_number. A license is current when its
latest term has license_status AAI and has not expired; otherwise it ended
at that term's expiration_date (or license_status_change_date for AAC
cancelled and REV revoked). Vocabularies verified 2026-09-30:
application_type ISSUE / RENEW / C_LOC / C_EXPA / C_CAPA / C_SBA;
license_status AAI / AAC / REV / REA / INQ.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from .. import history, stage
from ..http import Http
from ..models import Record, Snapshot, parse_date
from . import socrata
from .base import Source

DOMAIN = "data.cityofchicago.org"
DATASET = "r5kz-chrr"
WINDOW_DAYS = 180

LICENSE_CODES = {
    "1470": "nightlife",  # Tavern
    "1471": "nightlife",  # Late Hour
    "1050": "nightlife",  # Public Place of Amusement
    "1475": "on_premise",  # Consumption on Premises - Incidental Activity
    "1477": "on_premise",  # Outdoor Patio
    "1058": "catering_event",  # Indoor Special Event
    "1481": "catering_event",  # Caterer's Liquor License
    "1474": "off_premise",  # Package Goods
}

APPLICATION_TYPES = {
    "ISSUE": "NEW",
    "C_LOC": "CHANGE OF LOCATION",
    "C_EXPA": "EXPANSION",
    "C_CAPA": "CHANGE OF ACTIVITY",
    "C_SBA": "CHANGE OF BUSINESS ACTIVITY",
    "RENEW": "RENEWAL",
}


# license_status values seen in the dataset (probe, 2026-09-29): AAI = issued
# and active, AAC = cancelled. Every row in the window already carries
# date_issued, and conditional_approval (Y/N) is set on issued rows too, so
# a row here is a license that has been issued.
LICENSED_STATUSES = {"AAI"}

#: Filing types that are the licensee changing its own license.
ADDING_PERMIT_TYPES = {"EXPANSION", "CHANGE OF ACTIVITY", "CHANGE OF BUSINESS ACTIVITY"}
_HISTORY_FIELDS = ("license_number,account_number,legal_name,address,city,zip_code,"
                   "license_status,license_start_date,date_issued,expiration_date,"
                   "license_status_change_date")

# Nightlife license codes -> qualify.NIGHTLIFE_LICENSE_POINTS keys.
NIGHTLIFE_CODES = {"1050": "ppa", "1471": "late_hours"}


def categorize(code: str | None) -> str:
    return LICENSE_CODES.get((code or "").strip(), "other")


class ChicagoBacpSource(Source):
    name = "chicago_bacp_liquor"
    title = "Chicago BACP liquor & amusement license applications"
    state = "IL"
    homepage = f"https://{DOMAIN}/d/{DATASET}"
    tracks_removals = False  # rolling window
    min_records = 10

    def __init__(self, today: date | None = None):
        self.today = today

    def stage(self, rec: Record) -> str | None:
        status = (rec.status or "").strip().upper()
        if status in LICENSED_STATUSES:
            return stage.LICENSED
        return stage.from_status(rec.status)  # AAC (cancelled) -> None

    def nightlife_license(self, rec: Record) -> tuple[str, ...]:
        key = NIGHTLIFE_CODES.get((rec.license_type or "").strip())
        return (key,) if key else ()

    def venue_history(self, http: Http, records: list[Record],
                      snapshots: list[Snapshot] | None = None,
                      today: date | None = None) -> dict:
        today = today or date.today()
        out, lookup = {}, []
        for rec in records:
            if (rec.application_type or "").upper() in ADDING_PERMIT_TYPES:
                out[rec.source_record_id] = history.History(history.ADDING_PERMIT)
            else:
                lookup.append(rec)
        found = chicago_history(
            http, lookup, today,
            owner_ids={r.source_record_id: (r.raw or {}).get("account_number")
                       for r in lookup},
            own_licenses={r.source_record_id: (r.raw or {}).get("license_number")
                          for r in lookup})
        return {**out, **found}

    def contact(self, raw: dict) -> dict:
        """The licensee's legal name (a person for a sole proprietor)."""
        return self.people(raw.get("legal_name"))

    def fetch(self, http: Http) -> list[Snapshot]:
        since = ((self.today or date.today()) - timedelta(days=WINDOW_DAYS)).isoformat()
        codes = ",".join(f"'{c}'" for c in sorted(LICENSE_CODES))
        where = (
            f"license_code in ({codes}) AND application_type != 'RENEW' AND "
            f"(application_created_date >= '{since}' OR date_issued >= '{since}')"
        )
        return socrata.fetch_all(http, DOMAIN, DATASET, where=where, order="id")

    def parse(self, snapshots: list[Snapshot]) -> Iterable[Record]:
        for row in socrata.rows(snapshots):
            rid = (row.get("id") or "").strip()
            if not rid:
                continue
            app_type = (row.get("application_type") or "").strip().upper()
            yield Record(
                source=self.name,
                source_record_id=rid,
                source_url=f"https://{DOMAIN}/resource/{DATASET}.json?id={rid}",
                legal_name=row.get("legal_name"),
                dba=row.get("doing_business_as_name"),
                license_type=row.get("license_code"),
                license_description=row.get("license_description"),
                application_type=APPLICATION_TYPES.get(app_type, app_type or None),
                status=row.get("license_status"),
                application_date=parse_date(row.get("application_created_date")),
                address=row.get("address"),
                city=row.get("city"),
                state=row.get("state") or "IL",
                zip=row.get("zip_code"),
                county="Cook",
                category=categorize(row.get("license_code")),
                raw=row,
            )


def _license_prior(rows: list[dict], today: date) -> history.Prior:
    """One license (all its term rows) as a history.Prior (memory only)."""
    rows = sorted(rows, key=lambda r: r.get("expiration_date") or "")
    last = rows[-1]
    expires = parse_date(last.get("expiration_date"))
    status = (last.get("license_status") or "").strip().upper()
    active = status == "AAI" and bool(expires and expires >= today)
    ended = None
    if not active:
        changed = parse_date(last.get("license_status_change_date"))
        ended = changed if status in ("AAC", "REV") and changed else expires
    starts = [parse_date(r.get("license_start_date")) or parse_date(r.get("date_issued"))
              for r in rows]
    starts = [d for d in starts if d]
    return history.Prior(license_id=last.get("license_number"), owner=last.get("legal_name"),
                         owner_id=last.get("account_number"), active=active,
                         issued=min(starts) if starts else None, ended=ended)


def chicago_history(http: Http, records: list[Record], today: date, *,
                    owner_ids: dict, own_licenses: dict | None = None) -> dict:
    """Venue history for Chicago filings from the business-license dataset.
    `owner_ids` maps source_record_id -> BACP account number (may be None);
    `own_licenses` maps it to the filing's own license_number, left out of
    its priors. Records without a house number stay Unknown."""
    records = [r for r in records if history.parse_address(r.address)]
    if not records:
        return {}
    codes = ",".join(f"'{c}'" for c in sorted(LICENSE_CODES))
    rows = socrata.rows_near(http, DOMAIN, DATASET, records, zip_field="zip_code",
                             address_field="address", city_field="city",
                             select=_HISTORY_FIELDS, where=f"license_code in ({codes})")
    by_license: dict[str, list[dict]] = {}
    for row in rows:
        by_license.setdefault(row.get("license_number") or row.get("address") or "", []
                              ).append(row)
    licenses = [(group[-1], _license_prior(group, today)) for group in by_license.values()]
    own_licenses = own_licenses or {}
    out = {}
    for rec in records:
        own = own_licenses.get(rec.source_record_id)
        candidates = [(row, p) for row, p in licenses if not (own and p.license_id == own)]
        priors = history.match_priors(
            rec, candidates, lambda row: (row.get("address"), row.get("zip_code"),
                                          row.get("city")))
        out[rec.source_record_id] = history.classify(
            rec.legal_name, owner_ids.get(rec.source_record_id), rec.application_date,
            priors, today)
    return out
