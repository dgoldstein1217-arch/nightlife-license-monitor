"""Outreach results: what happened after the owner reached out.

``licmon review --status contacted | replied | won | wrong_contact`` records
the outcome with the best way to reach the venue at that moment (the
method the plan suggested), in ``outreach_outcomes``. ``wrong_contact``
also marks that contact as bad in ``contact_checks.bad_channels`` (the
lookup never offers it again) and makes the lookup check the venue again on
its next run (next_check_at = now, the 120-day clock restarts), so another
way to reach it can turn up as Newly reachable.

The Attio pull (``licmon attio-pull``) brings the team's list Status back:
Contacted becomes review status contacted and Not a fit becomes rejected,
only ever moving a lead forward (a replied or won lead is never set back).

Counts of the last RESULT_DAYS days feed the email and the workbook's How
scoring works tab (results_lines). Everything here stays in the private
database; nothing is logged but counts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: Review statuses that are outreach outcomes (logged with the method).
OUTCOMES = ("contacted", "replied", "won", "wrong_contact")
OUTCOME_TEXT = {"contacted": "contacted", "replied": "replied", "won": "won",
                "wrong_contact": "wrong contact"}
RESULT_DAYS = 30
#: Attio list Status -> review status, and the review statuses each may
#: replace (forward only).
ATTIO_TO_REVIEW = {"Contacted": "contacted", "Not a fit": "rejected"}
MAY_REPLACE = {
    "contacted": ("new", "approved", "snoozed", "wrong_contact"),
    "rejected": ("new", "approved", "snoozed", "wrong_contact", "contacted"),
}
ATTIO_NOTE = "Status from Attio"


def _venue_methods(conn, record_ids: list[int]) -> dict[str, str | None]:
    """venue key -> the best way to reach it right now (None when the
    lookup has no verified way to reach it)."""
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT r.venue_key,
                              CASE WHEN c.status = 'reachable' THEN c.outreach_method END
                       FROM records r LEFT JOIN contact_checks c USING (venue_key)
                       WHERE r.id = ANY(%s)""", (record_ids,))
        return {key: method for key, method in cur.fetchall()}


def log_outcome(conn, venue_methods: dict[str, str | None], outcome: str,
                source: str = "review", now: datetime | None = None) -> int:
    rows = [(key, outcome, method, source, now or datetime.now(timezone.utc))
            for key, method in venue_methods.items()]
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO outreach_outcomes
                           (venue_key, outcome, method, source, recorded_at)
                           VALUES (%s, %s, %s, %s, %s)""", rows)
    return len(rows)


def mark_wrong_contact(conn, venue_keys: list[str], now: datetime | None = None) -> int:
    """The venue's current contact is wrong: keep it as a bad channel, take
    the venue off reachable and look it up again on the next run."""
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE contact_checks SET
                   bad_channels = bad_channels || CASE WHEN contact_value IS NULL
                       THEN '[]'::jsonb
                       ELSE jsonb_build_array(jsonb_build_object(
                           'kind', contact_kind, 'value', contact_value)) END,
                   status = 'waiting', next_check_at = %s, first_checked_at = %s,
                   became_reachable_at = NULL, newly_reachable_on = NULL,
                   gave_up_at = NULL
               WHERE venue_key = ANY(%s)""", (now, now, venue_keys))
        return cur.rowcount


def record_review(conn, record_ids: list[int], status: str, note: str | None = None,
                  now: datetime | None = None) -> tuple[int, int]:
    """Set the review status on records. For an outcome status, also log it
    per venue with the current best way to reach (and handle wrong_contact).
    Returns (records updated, venues logged). Commits."""
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("""UPDATE records SET review_status=%s,
                           review_notes=COALESCE(%s, review_notes), reviewed_at=%s
                       WHERE id = ANY(%s)""", (status, note, now, record_ids))
        updated = cur.rowcount
    logged = 0
    if updated and status in OUTCOMES:
        venues = _venue_methods(conn, record_ids)
        logged = log_outcome(conn, venues, status, "review", now)
        if status == "wrong_contact":
            mark_wrong_contact(conn, list(venues), now)
    conn.commit()
    return updated, logged


def pull_attio(conn, list_statuses: dict[str, str | None], now: datetime | None = None
               ) -> dict:
    """Move leads forward from the team's Attio list Status (Contacted ->
    contacted, Not a fit -> rejected). Never sets a lead back. Contacted
    venues are logged as outcomes from Attio. Returns counts. Commits."""
    now = now or datetime.now(timezone.utc)
    counts = {"contacted": 0, "rejected": 0}
    for attio_status, review in ATTIO_TO_REVIEW.items():
        keys = [k for k, s in list_statuses.items() if s == attio_status]
        if not keys:
            continue
        with conn.cursor() as cur:
            cur.execute("""UPDATE records SET review_status=%s, reviewed_at=%s,
                               review_notes=COALESCE(review_notes, %s)
                           WHERE venue_key = ANY(%s) AND review_status = ANY(%s)
                           RETURNING id, venue_key""",
                        (review, now, ATTIO_NOTE, keys, list(MAY_REPLACE[review])))
            moved = cur.fetchall()
        counts[review] = len({key for _, key in moved})
        if review == "contacted" and moved:
            log_outcome(conn, _venue_methods(conn, [rid for rid, _ in moved]), "contacted",
                        "attio", now)
    conn.commit()
    return counts


def save_attio(conn, list_statuses: dict[str, str | None],
               target_statuses: dict[str, str | None], now: datetime | None = None) -> None:
    """Keep what Attio says per venue (attio_status), for the sheet and the
    plan. List statuses and Target statuses are saved separately so one
    read never wipes the other."""
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO attio_status (venue_key, list_status, checked_at)
               VALUES (%s, %s, %s) ON CONFLICT (venue_key) DO UPDATE SET
               list_status = EXCLUDED.list_status, checked_at = EXCLUDED.checked_at""",
            [(k, s, now) for k, s in list_statuses.items()])
        cur.executemany(
            """INSERT INTO attio_status (venue_key, target_status, checked_at)
               VALUES (%s, %s, %s) ON CONFLICT (venue_key) DO UPDATE SET
               target_status = EXCLUDED.target_status, checked_at = EXCLUDED.checked_at""",
            [(k, s, now) for k, s in target_statuses.items()])
    conn.commit()


def results(conn, now: datetime | None = None, days: int = RESULT_DAYS) -> list[tuple]:
    """(outcome, method, count) for the last `days` days. Counts only."""
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('outreach_outcomes') IS NOT NULL")
        if not cur.fetchone()[0]:
            return []
        cur.execute("""SELECT outcome, coalesce(method, ''), count(*)
                       FROM outreach_outcomes WHERE recorded_at >= %s
                       GROUP BY 1, 2 ORDER BY 1, 2""", (now - timedelta(days=days),))
        return [tuple(r) for r in cur.fetchall()]


def _method_name(method: str) -> str:
    return method.split(" (")[0] if method else "Unknown"


def results_lines(rows: list[tuple], days: int = RESULT_DAYS) -> list[str]:
    """Plain count lines: totals, then one line per way of reaching out
    ("Instagram DM: contacted 3, replied 1"). Empty when nothing is logged."""
    if not rows:
        return []
    totals = {o: 0 for o in OUTCOMES}
    by_method: dict[str, dict[str, int]] = {}
    for outcome, method, count in rows:
        if outcome not in totals:
            continue
        totals[outcome] += count
        per = by_method.setdefault(_method_name(method), {o: 0 for o in OUTCOMES})
        per[outcome] += count

    def text(counts):
        return ", ".join(f"{OUTCOME_TEXT[o]} {counts[o]}" for o in OUTCOMES if counts[o])

    lines = [f"Outreach results, last {days} days: "
             + ", ".join(f"{OUTCOME_TEXT[o]} {totals[o]}" for o in OUTCOMES)]
    lines += [f"{method}: {text(counts)}" for method, counts in sorted(by_method.items())
              if text(counts)]
    return lines
