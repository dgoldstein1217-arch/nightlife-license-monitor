"""New York State Liquor Authority: current pending license applications.

Dataset: "Current SLA Pending Licenses" on data.ny.gov (f8i8-k2gm), a full
list of applications still pending. Pulled statewide once per day.

The pending list carries no filing type, so every row looks new. Venue
history (``venue_history``) checks the SLA's two license lists on the same
portal (field names verified 2026-09-30):

- "Current Liquor Authority Active Licenses" (9s3h-dpkz, ~60k rows):
  licensepermitid, legalname, actualaddressofpremises,
  additionaladdressinformation, city, zipcode, originalissuedate,
  expirationdate.
- "Current SLA Inactive Licenses" (6dg3-2z7i, ~63k rows): the same fields
  in snake_case (license_permit_id, actual_address_of_premises, zip_code,
  original_issue_date, expiration_date). The list has no end date, so an
  inactive license counts as ended on its expiration date, or today if that
  is later.

Neither list has an owner id, so owners are compared by legal name.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Iterable

from .. import history, stage
from ..http import Http
from ..models import Record, Snapshot, parse_date
from . import socrata
from .base import Source

DOMAIN = "data.ny.gov"
DATASET = "f8i8-k2gm"
#: Existing licenses, for venue history.
ACTIVE_DATASET = "9s3h-dpkz"
INACTIVE_DATASET = "6dg3-2z7i"
_ACTIVE_FIELDS = ("licensepermitid,legalname,actualaddressofpremises,"
                  "additionaladdressinformation,city,zipcode,originalissuedate,"
                  "expirationdate")
_INACTIVE_FIELDS = ("license_permit_id,legalname,actual_address_of_premises,"
                    "additional_address_information,city,zip_code,original_issue_date,"
                    "expiration_date")

# Keyed on the dataset's human "description" column (lower-cased).
_CATEGORY_WORDS = [
    # (substring, category) checked in order; first hit wins
    ("night club", "nightlife"),
    ("cabaret", "nightlife"),
    ("tavern", "nightlife"),
    ("bottle club", "nightlife"),
    ("concert hall", "nightlife"),
    ("legitimate theatre", "nightlife"),
    ("bowling", "nightlife"),
    ("club", "on_premise"),  # club / for-profit club / summer club
    ("restaurant brewer", "hospitality_mfg"),
    ("restaurant", "on_premise"),
    ("food & beverage", "on_premise"),
    ("vessel", "on_premise"),
    ("railroad car", "on_premise"),
    ("corporate dining", "on_premise"),
    ("hotel", "hotel"),
    ("bed & breakfast", "hotel"),
    ("catering", "catering_event"),
    ("caterer", "catering_event"),
    ("large gathering venue", "catering_event"),
    ("stadium", "catering_event"),
    ("golf", "catering_event"),
    ("ice skating", "catering_event"),
    ("farm brewer", "hospitality_mfg"),
    ("micro-brewer", "hospitality_mfg"),
    ("farm winery", "hospitality_mfg"),
    ("microfarm winery", "hospitality_mfg"),
    ("farm cidery", "hospitality_mfg"),
    ("farm distiller", "hospitality_mfg"),
    ("micro-distiller", "hospitality_mfg"),
    ("grocery", "off_premise"),
    ("drug store", "off_premise"),
    ("liquor store", "off_premise"),
    ("wine store", "off_premise"),
    ("farm market", "off_premise"),
    ("wholesale", "wholesale_mfg"),
    ("importer", "wholesale_mfg"),
    ("direct shipper", "wholesale_mfg"),
    ("brand owner", "wholesale_mfg"),
    ("distiller", "wholesale_mfg"),
    ("rectifier", "wholesale_mfg"),
    ("brewer", "wholesale_mfg"),
    ("winery", "wholesale_mfg"),
]


# Description wording -> qualify.NIGHTLIFE_LICENSE_POINTS keys. Seen in the
# dataset (probe, 2026-09-29): "Night Club", "Cabaret", "Legitimate Theatre",
# "Summer Concert Hall", "Athletic/Sporting Event/Expositions/Large Gathering
# Venue", "Outdoor Athletic Fields and Stadiums" (and Summer variants).
# "Club" / "For-Profit Club" are membership clubs, not ticketed: left out.
# "Catering Establishment" (banquet halls) is a B license in qualify.py.
_NIGHTLIFE_DESCRIPTIONS = (
    (re.compile(r"NIGHT ?CLUB|CABARET"), "nightclub_cabaret"),
    (re.compile(r"CONCERT HALL"), "music_venue"),
    (re.compile(r"LEGITIMATE THEAT"), "theater"),
    (re.compile(r"STADIUM|ATHLETIC|SPORTING EVENT|LARGE GATHERING|ARENA"), "sports_venue"),
)


def categorize(description: str | None) -> str:
    text = (description or "").lower()
    for word, category in _CATEGORY_WORDS:
        if word in text:
            return category
    return "other"


class NySlaSource(Source):
    name = "ny_sla_pending"
    title = "New York SLA pending license applications"
    state = "NY"
    homepage = f"https://{DOMAIN}/d/{DATASET}"
    tracks_removals = True
    min_records = 200

    def stage(self, rec: Record) -> str | None:
        status = (rec.status or "").upper().replace(" ", "")
        if status == "UNDERREVIEW":
            return stage.IN_REVIEW
        if status == "INTAKECOMPLETE":
            return stage.RECEIVED
        return stage.from_status(rec.status)

    def nightlife_license(self, rec: Record) -> tuple[str, ...]:
        text = (rec.license_description or "").upper()
        return tuple(key for pattern, key in _NIGHTLIFE_DESCRIPTIONS if pattern.search(text))

    def contact(self, raw: dict) -> dict:
        """The applicant's legal name (a person for a sole proprietor)."""
        return self.people(raw.get("legalname"))

    def fetch(self, http: Http) -> list[Snapshot]:
        return socrata.fetch_all(http, DOMAIN, DATASET, order="application_id")

    def venue_history(self, http: Http, records: list[Record],
                      snapshots: list[Snapshot] | None = None,
                      today: date | None = None) -> dict:
        today = today or date.today()
        records = [r for r in records if r.zip and history.parse_address(r.address)]
        if not records:
            return {}
        active = socrata.rows_near(http, DOMAIN, ACTIVE_DATASET, records,
                                   zip_field="zipcode",
                                   address_field="actualaddressofpremises",
                                   select=_ACTIVE_FIELDS)
        inactive = socrata.rows_near(http, DOMAIN, INACTIVE_DATASET, records,
                                     zip_field="zip_code",
                                     address_field="actual_address_of_premises",
                                     select=_INACTIVE_FIELDS)
        candidates = ([(row, active_prior(row)) for row in active]
                      + [(row, inactive_prior(row, today)) for row in inactive])
        out = {}
        for rec in records:
            priors = history.match_priors(rec, candidates, _row_address)
            out[rec.source_record_id] = history.classify(
                rec.legal_name, None, rec.application_date, priors, today)
        return out

    def parse(self, snapshots: list[Snapshot]) -> Iterable[Record]:
        for row in socrata.rows(snapshots):
            app_id = (row.get("application_id") or "").strip()
            if not app_id:
                continue
            address = row.get("actual_address_of_premises")
            extra = row.get("additional_address_information")
            if extra:
                address = f"{address or ''} {extra}"
            yield Record(
                source=self.name,
                source_record_id=app_id,
                # Row-level link into the official dataset (SODA filter).
                source_url=(f"https://{DOMAIN}/resource/{DATASET}.json"
                            f"?application_id={app_id}"),
                legal_name=row.get("legalname"),
                dba=row.get("dba"),
                license_type=row.get("class"),
                license_description=row.get("description"),
                application_type="NEW",  # dataset holds pending applications
                status=row.get("status"),
                application_date=parse_date(row.get("received_date")),
                address=address,
                city=row.get("city"),
                state="NY",
                zip=row.get("zip_code"),
                county=row.get("premises_county"),
                category=categorize(row.get("description")),
                raw=row,
            )


def _row_address(row: dict) -> tuple:
    """(address, zip, city) of an active or inactive license row."""
    street = row.get("actualaddressofpremises") or row.get("actual_address_of_premises")
    extra = (row.get("additionaladdressinformation")
             or row.get("additional_address_information"))
    address = f"{street or ''} {extra}" if extra else street
    return address, row.get("zipcode") or row.get("zip_code"), row.get("city")


def active_prior(row: dict) -> history.Prior:
    return history.Prior(license_id=row.get("licensepermitid"), owner=row.get("legalname"),
                         active=True, issued=parse_date(row.get("originalissuedate")))


def inactive_prior(row: dict, today: date) -> history.Prior:
    expires = parse_date(row.get("expiration_date"))
    return history.Prior(license_id=row.get("license_permit_id"), owner=row.get("legalname"),
                         active=False, issued=parse_date(row.get("original_issue_date")),
                         ended=min(expires, today) if expires else None)
