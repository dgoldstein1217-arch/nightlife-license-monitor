"""Texas Alcoholic Beverage Commission: pending original (new) applications.

Dataset: "Pending Original New Primary and Subordinate License Application(s)"
on data.texas.gov (mxm5-tdpj). Holds original applications with status
Received / In Review, as of midnight the prior day. Pulled statewide daily.

Every row is an original application, so application_type is always
"ORIGINAL" even when the premises has been licensed for years. Venue
history (``venue_history``) checks "TABC License Information" (7hf9-qc9f,
every license with its status, owner, master_file_id, original_issue_date,
expiration_date and address; verified 2026-09-30). Two checks:

- Subordinate permits (FB, LH, LP, BP rows carry subordinate_license_id)
  name their primary license in primary_license_id. When that license is
  already in 7hf9-qc9f (current, same owner, issued before this filing) the
  filing is "Adding a permit". On 2026-09-30, 28 of 293 subordinate rows
  pointed at an existing Active license, all with the same master_file_id.
- Every filing: licenses at the same premises (history.same_premises). The
  owner id is master_file_id (on about 40% of pending rows), else the legal
  name. Current = primary_status Active, Suspended or Temporarily
  Surrendered; other statuses (Expired, Expired - Original Required,
  Surrendered, Cancelled) ended on status_change_date or expiration_date.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

from .. import history, stage
from ..http import Http
from ..models import Record, Snapshot, clean_id, parse_date
from . import socrata
from .base import Source

DOMAIN = "data.texas.gov"
DATASET = "mxm5-tdpj"
#: Every TABC license (active and inactive), for venue history.
LICENSES_DATASET = "7hf9-qc9f"
_LICENSE_FIELDS = ("license_id,primary_status,original_issue_date,expiration_date,"
                   "status_change_date,owner,master_file_id,address,address_2,zip,city")
#: primary_status values that mean the license is still held.
CURRENT_STATUSES = {"ACTIVE", "SUSPENDED", "TEMPORARILY SURRENDERED"}

# Verified against https://www.tabc.texas.gov/services/tabc-licenses-permits/
# tabc-license-permit-types/ (Sep 2026).
LICENSE_TYPES = {
    "MB": ("Mixed Beverage Permit", "on_premise"),
    "FB": ("Food and Beverage Certificate", "on_premise"),
    "LH": ("Late Hours Certificate", "nightlife"),
    "BG": ("Wine and Malt Beverage Retailer's Permit", "on_premise"),
    "BE": ("Retail Dealer's On-Premise License", "on_premise"),
    "BP": ("Brewpub License", "hospitality_mfg"),
    "N": ("Private Club Registration Permit", "on_premise"),
    "NE": ("Private Club Exemption Certificate", "on_premise"),
    "BQ": ("Wine and Malt Beverage Retailer's Off-Premise Permit", "off_premise"),
    "BF": ("Retail Dealer's Off-Premise License", "off_premise"),
    "P": ("Package Store Permit", "off_premise"),
    "Q": ("Wine-Only Package Store Permit", "off_premise"),
    "LP": ("Local Distributor's Permit", "wholesale_mfg"),
    "BW": ("Brewer's License", "hospitality_mfg"),
    "G": ("Winery Permit", "hospitality_mfg"),
    "D": ("Distiller's and Rectifier's Permit", "hospitality_mfg"),
    "DS": ("Out-of-State Winery Direct Shipper's Permit", "wholesale_mfg"),
    "BN": ("Nonresident Brewer's License", "wholesale_mfg"),
    "S": ("Nonresident Seller's Permit", "wholesale_mfg"),
    "BB": ("General Distributor's License", "wholesale_mfg"),
    "BC": ("Branch Distributor's License", "wholesale_mfg"),
    "W": ("Wholesaler's Permit", "wholesale_mfg"),
    "X": ("General Class B Wholesaler's Permit", "wholesale_mfg"),
    "J/JD": ("Bonded Warehouse Permit", "wholesale_mfg"),
    "E": ("Local Cartage Permit", "wholesale_mfg"),
    "ET": ("Third-Party Local Cartage Permit", "wholesale_mfg"),
    "PR": ("Promotional Permit", "wholesale_mfg"),
    "NT": ("Nonprofit Entity Temporary Event Permit", "temporary"),
}


def categorize(code: str | None) -> str:
    return LICENSE_TYPES.get((code or "").strip().upper(), (None, "other"))[1]


class TxTabcSource(Source):
    name = "tx_tabc_pending"
    title = "Texas TABC pending original applications"
    state = "TX"
    homepage = f"https://{DOMAIN}/d/{DATASET}"
    tracks_removals = True
    min_records = 100

    def stage(self, rec: Record) -> str | None:
        # Dataset holds "Received" and "Pending - In Review" (en dash upstream).
        status = (rec.status or "").upper()
        if "REVIEW" in status:
            return stage.IN_REVIEW
        if "RECEIVED" in status:
            return stage.RECEIVED
        return stage.from_status(rec.status)

    def nightlife_license(self, rec: Record) -> tuple[str, ...]:
        return ("late_hours",) if (rec.license_type or "").upper() == "LH" else ()

    def contact(self, raw: dict) -> dict:
        """The filing's owner (a person for a sole proprietor)."""
        return self.people(raw.get("owner"))

    def fetch(self, http: Http) -> list[Snapshot]:
        return socrata.fetch_all(http, DOMAIN, DATASET, order="applicationid")

    def venue_history(self, http: Http, records: list[Record],
                      snapshots: list[Snapshot] | None = None,
                      today: date | None = None) -> dict:
        today = today or date.today()
        primary_of = {}
        for rec in records:
            pid = clean_id((rec.raw or {}).get("primary_license_id"))
            if (rec.raw or {}).get("subordinate_license_id") and pid and pid.isdigit():
                primary_of[rec.source_record_id] = pid
        primaries: dict[str, dict] = {}
        ids = sorted(set(primary_of.values()))
        for i in range(0, len(ids), 100):
            where = f"license_id in({','.join(ids[i:i + 100])})"
            for row in socrata.rows(socrata.fetch_all(
                    http, DOMAIN, LICENSES_DATASET, where=where, select=_LICENSE_FIELDS,
                    page_size=5000)):
                primaries[clean_id(row.get("license_id"))] = row
        nearby = socrata.rows_near(http, DOMAIN, LICENSES_DATASET, records,
                                   zip_field="zip", address_field="address",
                                   select=_LICENSE_FIELDS)
        out = {}
        for rec in records:
            priors = {clean_id(row.get("license_id")): license_prior(row)
                      for row in nearby
                      if history.same_premises(rec.address, rec.zip, rec.city,
                                               _address(row), row.get("zip"),
                                               row.get("city"))}
            primary = primaries.get(primary_of.get(rec.source_record_id))
            if primary:
                priors[clean_id(primary.get("license_id"))] = license_prior(primary)
            elif not history.parse_address(rec.address) or not rec.zip:
                continue  # nothing to match on: Unknown
            out[rec.source_record_id] = history.classify(
                rec.legal_name, clean_id((rec.raw or {}).get("master_file_id")),
                rec.application_date, list(priors.values()), today)
        return out

    def parse(self, snapshots: list[Snapshot]) -> Iterable[Record]:
        for row in socrata.rows(snapshots):
            app_id = clean_id(row.get("applicationid"))
            if not app_id:
                continue
            code = (row.get("license_type") or "").strip().upper()
            address = row.get("address")
            if row.get("address_2"):
                address = f"{address or ''} {row['address_2']}"
            zip_code = (row.get("zip") or "").strip()
            if len(zip_code) == 9 and zip_code.isdigit():
                zip_code = f"{zip_code[:5]}-{zip_code[5:]}"
            yield Record(
                source=self.name,
                source_record_id=app_id,
                source_url=(f"https://{DOMAIN}/resource/{DATASET}.json"
                            f"?applicationid={row.get('applicationid')}"),
                legal_name=row.get("owner"),
                dba=row.get("trade_name"),
                license_type=code or None,
                license_description=LICENSE_TYPES.get(code, (code, None))[0],
                application_type="ORIGINAL",
                status=row.get("applicationstatus"),
                application_date=parse_date(row.get("submission_date")),
                address=address,
                city=row.get("city"),
                state=row.get("state") or "TX",
                zip=zip_code or None,
                county=row.get("county"),
                category=categorize(code),
                raw=row,
            )


def _address(row: dict) -> str | None:
    parts = [row.get("address"), row.get("address_2")]
    return " ".join(p for p in parts if p) or None


def license_prior(row: dict) -> history.Prior:
    """One 7hf9-qc9f license row as a history.Prior (memory only)."""
    active = (row.get("primary_status") or "").strip().upper() in CURRENT_STATUSES
    ended = None if active else (parse_date(row.get("status_change_date"))
                                 or parse_date(row.get("expiration_date")))
    return history.Prior(license_id=clean_id(row.get("license_id")),
                         owner=row.get("owner"),
                         owner_id=clean_id(row.get("master_file_id")),
                         active=active, issued=parse_date(row.get("original_issue_date")),
                         ended=ended)
