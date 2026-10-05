"""Command line. `licmon run` is what the scheduled workflow calls.

Commands that print lead data (queue, export) are for the operator's own
machine only. Never run them in the public GitHub Actions workflow.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import date, datetime, timezone

from . import db, pipeline
from .metros import assign_metro
from .models import Record, venue_key
from .qualify import qualify
from .sources import get_sources

LEAD_COLUMNS = [
    "queue_date", "tier", "score", "legal_name", "dba", "metro", "address", "city",
    "state", "zip", "county", "license_types", "license_descriptions",
    "application_types", "statuses", "application_date", "event_types",
    "source", "source_record_ids", "source_url", "first_seen_at", "qualify_reason",
    "review_status", "review_notes", "record_ids", "stage", "lead_score", "hot",
    "adult", "venue_history", "prior_licenses", "prior_since",
]
RECORD_EXPORT_COLUMNS = [
    "queue_date", "event_type", "tier", "score", "legal_name", "dba",
    "source_record_id", "license_type", "license_description", "application_type",
    "status", "application_date", "address", "city", "state", "zip", "county",
    "metro", "source", "source_url", "first_seen_at", "qualify_reason",
    "review_status", "review_notes", "changes", "record_id", "stage", "lead_score",
    "hot", "adult", "venue_history", "prior_licenses", "prior_since",
]
REVIEW_STATUSES = ("new", "approved", "rejected", "contacted", "snoozed")


# Neon Free stops accepting writes at 512 MB. Fail loudly well before that so
# the owner gets a red run + email instead of silent data loss.
STORAGE_ALARM_MB = float(os.environ.get("STORAGE_ALARM_MB", "400"))


def cmd_run(args) -> int:
    log = logging.getLogger("licmon")
    results = pipeline.run(get_sources(args.source), trigger=args.trigger)
    code = 0
    failed = [r.source for r in results if r.status != "success"]
    if failed:
        log.error("failed sources: %s", ", ".join(failed))
        code = 1
    with db.connect() as conn:
        size = db.database_mb(conn)
    if size >= STORAGE_ALARM_MB:
        log.error("STORAGE ALARM: database is %.0f MB (alarm at %.0f MB, Neon Free "
                  "limit 512 MB). Lower RAW_RETENTION_DAYS or upgrade the plan.",
                  size, STORAGE_ALARM_MB)
        code = 1
    else:
        log.info("database size %.0f MB (alarm at %.0f MB)", size, STORAGE_ALARM_MB)
    return code


def cmd_probe(args) -> int:
    """Fetch + parse without a database. Counts only; safe for public logs."""
    import time
    from collections import Counter

    from .http import Http

    log = logging.getLogger("licmon")
    http, failed = Http(), 0
    for source in get_sources(args.source):
        t0 = time.monotonic()
        try:
            snaps = source.fetch(http)
            recs = list(source.parse(snaps))
            size = sum(len(s.body) for s in snaps)
            metros = sum(1 for r in recs if assign_metro(r.state, r.county, r.city))
            cats = Counter(r.category for r in recs)
            ok = len(recs) >= source.min_records
            failed += not ok
            log.info("probe %s: %s %d bytes, %d records (%d in target metros) in %.1fs; %s",
                     source.name, "ok" if ok else "BELOW FLOOR", size, len(recs), metros,
                     time.monotonic() - t0, dict(cats.most_common()))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            from .http import SourceHTTPError
            msg = f": {exc}" if isinstance(exc, SourceHTTPError) else ""
            log.error("probe %s: FAILED (%s)%s", source.name, type(exc).__name__, msg)
    return 1 if failed else 0


def cmd_init_db(args) -> int:
    with db.connect() as conn:
        db.init_schema(conn)
    print("schema ready")
    return 0


def _queue_rows(conn, day: date | None, open_only: bool, per_record: bool = False):
    view, columns = (("review_queue", RECORD_EXPORT_COLUMNS) if per_record
                     else ("daily_leads", LEAD_COLUMNS))
    where, params = [], []
    if day:
        where.append("queue_date = %s")
        params.append(day)
    if open_only:
        where.append("review_status = 'new'")
    sql = (f"SELECT {', '.join(columns)} FROM {view}"
           + (f" WHERE {' AND '.join(where)}" if where else "")
           + " ORDER BY queue_date DESC, tier, score DESC, metro, legal_name")
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return columns, cur.fetchall()


def cmd_export(args) -> int:
    from . import leadsheet

    day = None if args.all else (args.date or datetime.now(timezone.utc).date())
    xlsx = bool(args.out and args.out.lower().endswith(".xlsx"))
    full = args.full or args.per_record
    if xlsx and full:
        raise SystemExit("--full / --per-record write CSV only; use a .csv file name")
    if not full:
        with db.connect() as conn:
            rows = leadsheet.load_rows(conn, day, args.open)
            open_rows = leadsheet.load_rows(conn, None, True) if xlsx else []
        if xlsx:
            # Tabs: New (the rows asked for), All open, one per state, legend.
            with open(args.out, "wb") as fh:
                fh.write(leadsheet.build_workbook(rows, open_rows))
        else:
            out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
            try:
                leadsheet.write_csv(rows, out)
            finally:
                if args.out:
                    out.close()
        print(f"{len(rows)} leads" + (f" -> {args.out}" if args.out else ""), file=sys.stderr)
        return 0
    with db.connect() as conn:
        columns, rows = _queue_rows(conn, day, args.open, args.per_record)
    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    try:
        w = csv.writer(out)
        w.writerow(columns)
        w.writerows(rows)
    finally:
        if args.out:
            out.close()
    print(f"{len(rows)} {'records' if args.per_record else 'leads'}" + (f" -> {args.out}" if args.out else ""), file=sys.stderr)
    return 0


def cmd_status(args) -> int:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (source) source, started_at, status, is_baseline,
                      records_fetched, records_new, records_changed, records_removed,
                      records_qualified_queued, error_type
               FROM source_runs ORDER BY source, started_at DESC""")
        rows = cur.fetchall()
        cur.execute("""SELECT queue_date, count(*) FROM daily_leads
                       GROUP BY 1 ORDER BY 1 DESC LIMIT 7""")
        days = cur.fetchall()
    print("latest run per source:")
    for r in rows:
        print(f"  {r[0]:<22} {r[1]:%Y-%m-%d %H:%M} {r[2]:<8}"
              f"{' baseline' if r[3] else ''} fetched={r[4]} new={r[5]} "
              f"changed={r[6]} removed={r[7]} queued={r[8]}"
              + (f" error={r[9]}" if r[9] else ""))
    print("queue size by day:")
    for d, n in days:
        print(f"  {d}  {n}")
    return 0


def cmd_email(args) -> int:
    """Email the day's leads to the owner, or write a local preview.

    Logs say only counts and sent/skipped/failed: workflow logs are public.
    """
    from pathlib import Path

    from . import notify

    log = logging.getLogger("licmon")
    day = args.date or datetime.now(timezone.utc).date()
    if not args.preview and not notify.email_configured():
        log.info("email skipped (not configured)")
        return 0
    if args.preview:
        out = Path(args.preview).expanduser().resolve()
        repo = Path(__file__).resolve().parents[2]
        if out == repo or repo in out.parents:
            raise SystemExit("preview folder must be outside the repository (lead data)")
    with db.connect() as conn:
        data = notify.load_daily(conn, day)
    sender, recipients = notify.settings_from_env()
    leads = data["leads"]
    tier_a = sum(1 for lead in leads if lead.get("priority") == "A")
    if args.preview:
        msg = notify.compose(data, day, sender=sender or "sender@example.com",
                             recipients=recipients or ["owner@example.com"])
        paths = notify.write_preview(msg, str(out))
        print(f"preview for {day}: {len(leads)} leads ({tier_a} tier A), "
              f"{len(paths)} files -> {out}", file=sys.stderr)
        return 0
    msg = notify.compose(data, day, sender=sender, recipients=recipients)
    try:
        notify.send(msg)
    except notify.NotifyError as exc:
        log.error("%s", exc)  # NotifyError text is "email failed (<ErrorType>)"
        return 1
    log.info("email sent: %d leads (%d tier A) to %d recipient(s)",
             len(leads), tier_a, len(recipients))
    return 0


def cmd_review(args) -> int:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE records SET review_status=%s,
                   review_notes=COALESCE(%s, review_notes), reviewed_at=now()
               WHERE id = ANY(%s)""",
            (args.status, args.note, args.record_id))
        n = cur.rowcount
        conn.commit()
    print(f"updated {n} record(s)")
    return 0 if n else 1


_REQUALIFY_COLS = ("id", "source", "source_record_id", "source_url", "legal_name", "dba",
                   "license_type", "license_description", "application_type", "status",
                   "application_date", "address", "city", "state", "zip", "county",
                   "category")


def refresh_history(conn, today: date | None = None, http=None) -> int:
    """Look up venue history again for every stored qualified record (needs
    network) and save it. Returns how many records were checked. Logs counts
    only; a failed source leaves its records Unknown."""
    from . import history
    from .http import Http
    from .sources import all_sources

    today = today or datetime.now(timezone.utc).date()
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(_REQUALIFY_COLS)}, raw FROM records "
                    "WHERE qualified")
        rows = cur.fetchall()
    by_source: dict[str, list] = {}
    ids = {}
    for row in rows:
        d = dict(zip(_REQUALIFY_COLS, row[:-1]))
        rid = d.pop("id")
        rec = Record(**d, raw=row[-1] or {})
        by_source.setdefault(rec.source, []).append(rec)
        ids[(rec.source, rec.source_record_id)] = rid
    http = http or Http()
    updates = []
    for source in all_sources():
        recs = by_source.get(source.name) or []
        found = history.lookup(source, http, recs, None, today)
        updates += [(h.label, h.prior_licenses, h.prior_since, ids[(source.name, srid)])
                    for srid, h in found.items()]
    with conn.cursor() as cur:
        cur.executemany("UPDATE records SET venue_history=%s, prior_licenses=%s, "
                        "prior_since=%s WHERE id=%s", updates)
    conn.commit()
    return len(updates)


def requalify(conn, today: date | None = None) -> int:
    """Re-run the rules on every stored record, applying its stored venue
    history. Returns how many records changed."""
    today = today or datetime.now(timezone.utc).date()
    cols = _REQUALIFY_COLS  # raw is not needed to requalify
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(cols)}, qualified, score, tier, qualify_reason, "
                    "metro, venue_key, stage, lead_score, hot, adult, venue_history "
                    "FROM records")
        updates = []
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            rid = d.pop("id")
            rec = Record(**d)
            metro = assign_metro(rec.state, rec.county, rec.city)
            q = qualify(rec, metro, today=today, history=row[-1])
            vk = venue_key(rec)
            new = (q.qualified, q.score, q.tier, q.reason, metro, vk, q.stage,
                   q.lead_score, q.hot, q.adult)
            if new != tuple(row[len(cols):-1]):
                updates.append(new + (rid,))
        cur.executemany("UPDATE records SET qualified=%s, score=%s, tier=%s, "
                        "qualify_reason=%s, metro=%s, venue_key=%s, stage=%s, "
                        "lead_score=%s, hot=%s, adult=%s WHERE id=%s", updates)
    conn.commit()
    return len(updates)


def cmd_requalify(args) -> int:
    """Re-run the rules on stored records after editing qualify.py/metros.py.
    --history also looks up venue history again first (network). Does not
    add anything to past queues."""
    with db.connect() as conn:
        db.init_schema(conn)  # new score columns may not exist yet
    with db.connect() as conn:
        if args.history:
            checked = refresh_history(conn)
            print(f"venue history checked for {checked} qualified record(s)")
        changed = requalify(conn)
    print(f"requalified {changed} record(s)")
    return 0


def cmd_attio_setup(args) -> int:
    """Create the License Leads list on Targets in Attio. Dry run unless
    --write. Prints schema names only."""
    from . import attio

    log = logging.getLogger("licmon")
    if args.write and not attio.configured():
        raise SystemExit("ATTIO_API_KEY (or ATTIO_WRITE_API_KEY) is not set")
    client = attio.Client() if attio.configured() else None
    try:
        steps = attio.setup(client, args.write)
    except attio.AttioError as exc:
        log.error("%s", exc)
        return 1
    for step in steps:
        indented = step.startswith(" ")
        print(step if indented or args.write else f"would {step}")
    if not args.write:
        print("dry run: nothing changed in Attio. Add --write to create it."
              + ("" if client else " (no API key set, so the workspace was not checked)"))
    return 0


def cmd_attio_sync(args) -> int:
    """Add the day's Hot, A and strong B venues (never adult) to the Attio
    License Leads list. Dry run unless --write. Logs counts only: Actions
    logs are public."""
    from . import attio, leadsheet

    log = logging.getLogger("licmon")
    if not attio.configured():
        if args.write:
            log.info("attio sync skipped (not configured)")
            return 0
        client = None
    else:
        client = attio.Client()
    day = args.date or datetime.now(timezone.utc).date()
    with db.connect() as conn:
        rows = leadsheet.load_rows(conn, day)
    try:
        counts = attio.sync(client, rows, write=args.write)
    except attio.AttioError as exc:
        log.error("%s", exc)  # "attio failed (HTTP <status> <code>)": no lead data
        return 1
    if args.counts:
        attio.write_counts(args.counts, counts)
    verb = "" if args.write else "would be "
    log.info("attio sync%s: %d candidates (%d hot); list entries %sadded %d, %supdated %d; "
             "new Targets %d, existing Targets reused %d; skipped over daily cap %d; "
             "B leads sent %d; priority options added %d; B leads held back %d; "
             "adding-a-permit venues left out %d; venue history field added %d; "
             "held back without verified contact %d; contact fields added %d",
             "" if args.write else " (dry run)", counts["candidates"], counts["hot"],
             verb, counts["added"], verb, counts["updated"], counts["created"],
             counts["reused"], counts["skipped"], counts["b"], counts["options_added"],
             counts["b_held"], counts["permits_left_out"], counts["history_field_added"],
             counts["no_contact_held"], counts["contact_fields_added"])
    if counts["b_held"]:
        log.warning("attio: priority option B is missing and this key cannot add it "
                    "(needs list_configuration:read-write); B leads wait until it exists")
    if counts["contact_fields_missing"]:
        log.warning("attio: the contact fields are missing and this key cannot add them "
                    "(needs list_configuration:read-write); synced without them")
    if counts["history_field_missing"]:
        log.warning("attio: the Venue history field is missing and this key cannot add "
                    "it (needs list_configuration:read-write); synced without it")
    return 0


def cmd_enrich(args) -> int:
    """Look up and verify contact details for eligible venues (contact.py).
    Never contacts a business. Skips itself without GOOGLE_PLACES_API_KEY.
    A failed lookup logs "enrich FAILED <lookup> failed (<status>)" and the
    run stays green. Logs counts only: Actions logs are public."""
    from . import contact

    log = logging.getLogger("licmon")
    if not contact.configured():
        log.info("enrich skipped (not configured): 0 venues checked")
        return 0
    with db.connect() as conn:
        db.init_schema(conn)  # the contact_checks table may not exist yet
    instagram = contact.Instagram() if contact.ig_configured() else None
    with db.connect() as conn:
        counts = contact.run(conn, places=contact.Places(), website=contact.Website(),
                             instagram=instagram,
                             cap=args.cap if args.cap is not None else contact.daily_cap())
    log.info("enrich: checked %d (new %d, rechecks %d, backlog %d); reachable %d, newly "
             "reachable %d, waiting %d, gave up %d; over daily cap %d; lookups failed %d; "
             "instagram %s", counts["checked"], counts["new"], counts["recheck"],
             counts["backlog"], counts["reachable"], counts["newly_reachable"],
             counts["waiting"], counts["gave_up"], counts["over_cap"], counts["failed"],
             "on" if counts["instagram"] else "off")
    return 0


def cmd_slack(args) -> int:
    """Ping the team's Slack channel when there are new or stage-advanced
    leads. --preview prints the message on this machine instead of posting."""
    from . import attio, leadsheet, slack

    log = logging.getLogger("licmon")
    if args.preview and os.environ.get("GITHUB_ACTIONS"):
        raise SystemExit("--preview prints lead names; never run it in GitHub Actions")
    if not args.preview and not slack.configured():
        log.info("slack skipped (not configured)")
        return 0
    day = args.date or datetime.now(timezone.utc).date()
    with db.connect() as conn:
        rows = leadsheet.load_rows(conn, day)
    if not slack.is_news(rows):
        log.info("slack skipped (no new or stage-advanced leads)")
        return 0
    counts = attio.read_counts(args.counts) if args.counts else None
    text = slack.compose(rows, counts, os.environ.get("ATTIO_LEADS_URL", "").strip() or None)
    if args.preview:
        print(text)
        return 0
    try:
        slack.post(text)
    except slack.SlackError as exc:
        log.error("%s", exc)
        return 1
    log.info("slack posted: %d leads (%d hot)", len(rows),
             sum(1 for r in rows if r.get("hot")))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # urllib3's retry warnings name the host, which for venue websites is
    # lead data. Workflow logs are public and must stay counts-only.
    logging.getLogger("urllib3").setLevel(logging.CRITICAL + 1)
    p = argparse.ArgumentParser(prog="licmon", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="collect all (or named) sources now")
    r.add_argument("--source", action="append", help="source name; repeatable")
    r.add_argument("--trigger", default="manual")
    r.set_defaults(func=cmd_run)

    pr = sub.add_parser("probe", help="fetch+parse without a database (counts only)")
    pr.add_argument("--source", action="append", help="source name; repeatable")
    pr.set_defaults(func=cmd_probe)

    sub.add_parser("init-db", help="create/upgrade tables").set_defaults(func=cmd_init_db)
    sub.add_parser("status", help="latest run per source (no lead data)").set_defaults(
        func=cmd_status)

    e = sub.add_parser("export", help="write the lead list as .xlsx or .csv (local only)")
    e.add_argument("--date", type=date.fromisoformat, help="queue date, default today UTC")
    e.add_argument("--all", action="store_true", help="every queued lead, all dates")
    e.add_argument("--open", action="store_true", help="only review_status = new")
    e.add_argument("--per-record", action="store_true",
                   help="one row per application, all columns (CSV)")
    e.add_argument("--full", action="store_true",
                   help="every stored column instead of the clean lead sheet (CSV)")
    e.add_argument("--out", help="output file; .xlsx = Excel, else CSV (default stdout)")
    e.set_defaults(func=cmd_export)

    m = sub.add_parser("email", help="email the day's leads to the owner (SMTP env vars)")
    m.add_argument("--date", type=date.fromisoformat, help="queue date, default today UTC")
    m.add_argument("--preview", metavar="DIR",
                   help="write preview files to DIR (outside the repo) instead of sending")
    m.set_defaults(func=cmd_email)

    v = sub.add_parser("review", help="set review status on record id(s)")
    v.add_argument("record_id", type=int, nargs="+")
    v.add_argument("--status", required=True, choices=REVIEW_STATUSES)
    v.add_argument("--note")
    v.set_defaults(func=cmd_review)

    q = sub.add_parser("requalify", help="re-apply qualification rules")
    q.add_argument("--history", action="store_true",
                   help="also look up venue history again for qualified records "
                        "(network, a few minutes)")
    q.set_defaults(func=cmd_requalify)

    a = sub.add_parser("attio-setup", help="create the License Leads list on Targets in "
                                           "Attio (dry run unless --write)")
    a.add_argument("--write", action="store_true", help="really create it")
    a.set_defaults(func=cmd_attio_setup)

    s = sub.add_parser("attio-sync", help="add the day's Hot, A and strong B venues to "
                                          "the Attio License Leads list (dry run unless "
                                          "--write)")
    s.add_argument("--date", type=date.fromisoformat, help="queue date, default today UTC")
    s.add_argument("--write", action="store_true", help="really write to Attio")
    s.add_argument("--counts", metavar="FILE", help="write counts-only JSON here for Slack")
    s.set_defaults(func=cmd_attio_sync)

    n = sub.add_parser("enrich", help="find and verify venue contact details for eligible "
                                      "leads (GOOGLE_PLACES_API_KEY); contacts nobody")
    n.add_argument("--cap", type=int, help="most venues to check now (default "
                                           "ENRICH_DAILY_CAP or 150)")
    n.set_defaults(func=cmd_enrich)

    k = sub.add_parser("slack", help="ping Slack about new leads (SLACK_WEBHOOK_URL)")
    k.add_argument("--date", type=date.fromisoformat, help="queue date, default today UTC")
    k.add_argument("--counts", metavar="FILE", help="Attio counts JSON from attio-sync")
    k.add_argument("--preview", action="store_true",
                   help="print the message here instead of posting (local only)")
    k.set_defaults(func=cmd_slack)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        # Tracebacks can echo record values; print the type only.
        logging.getLogger("licmon").error("fatal: %s (details suppressed; "
                                          "logs are public)", type(exc).__name__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
