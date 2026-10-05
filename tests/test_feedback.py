"""Outreach results, wrong contacts and the Attio status pull. No network:
Attio is a fake session. All venues are invented."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from licmon import attio, cli, contact, feedback, leadsheet, outreach
from test_attio_slack import FakeResp, FakeSession, _NullConn, fake_client
from test_contact import FakePlaces, FakeWebsite, _seed, place, site_links

DAY1 = datetime(2026, 10, 1, 16, tzinfo=timezone.utc)


# --- results lines (counts only) ---

def test_results_lines_totals_and_by_method():
    rows = [("contacted", "Instagram DM", 3), ("contacted", "Instagram DM (found by search)", 1),
            ("replied", "Instagram DM", 2), ("won", "Instagram DM", 1),
            ("contacted", "Call", 1), ("wrong_contact", "Call (Google does not say it is open)",
                                       1),
            ("contacted", "", 1)]
    assert feedback.results_lines(rows) == [
        "Outreach results, last 30 days: contacted 6, replied 2, won 1, wrong contact 1",
        "Call: contacted 1, wrong contact 1",
        "Instagram DM: contacted 4, replied 2, won 1",
        "Unknown: contacted 1"]
    assert feedback.results_lines([]) == []
    for line in feedback.results_lines(rows):
        assert ";" not in line and "—" not in line


# --- Attio reads (read-only) ---

def entry(key, status=None):
    values = {"venue_key": [{"value": key, "attribute_type": "text"}]}
    if status:
        values["team_status"] = [
            {"active_until": "2026-09-30T00:00:00Z", "option": {"title": "New"},
             "attribute_type": "select"},
            {"active_until": None, "option": {"title": status, "is_archived": False},
             "attribute_type": "select"}]
    return {"id": {"entry_id": f"e-{key}"}, "entry_values": values}


def test_list_statuses_pages_and_reads_select_titles(monkeypatch):
    monkeypatch.setattr(attio, "LIST_PAGE", 2)
    session = FakeSession({("POST", "/lists/license_leads/entries/query"): [
        FakeResp(body={"data": [entry("k1", "Contacted"), entry("k2")]}),
        FakeResp(body={"data": [entry("k3", "Not a fit")]})]})
    assert attio.list_statuses(fake_client(session)) == {
        "k1": "Contacted", "k2": None, "k3": "Not a fit"}
    bodies = [c[2] for c in session.calls]
    assert bodies == [{"limit": 2, "offset": 0}, {"limit": 2, "offset": 2}]
    assert all(c[0] == "POST" for c in session.calls)  # a query, never a write


def test_list_statuses_stops_when_offset_is_ignored(monkeypatch):
    monkeypatch.setattr(attio, "LIST_PAGE", 1)
    session = FakeSession({("POST", "/entries/query"): FakeResp(
        body={"data": [entry("k1", "Contacted")]})})
    assert attio.list_statuses(fake_client(session)) == {"k1": "Contacted"}
    assert len(session.calls) == 2


def test_list_statuses_missing_list():
    session = FakeSession({("POST", "/entries/query"): FakeResp(404, {})})
    with pytest.raises(attio.AttioError, match="run licmon attio-setup"):
        attio.list_statuses(fake_client(session))


def test_find_target_status_exact_name_only():
    target = {"id": {"record_id": "r1"}, "values": {
        "company_1": [{"value": "Zebra Fake Lounge"}],
        "status": [{"status": {"title": "In conversation"}, "attribute_type": "status"}]}}
    near = {"id": {"record_id": "r2"}, "values": {
        "company_1": [{"value": "Zebra Fake Lounge Two"}],
        "status": [{"status": {"title": "Prespecting"}}]}}
    session = FakeSession({("POST", "/objects/target_client/records/query"): FakeResp(
        body={"data": [near, target]})})
    assert attio.find_target_status(fake_client(session), "zebra fake lounge") == \
        "In conversation"
    session = FakeSession({("POST", "/records/query"): FakeResp(body={"data": [near]})})
    assert attio.find_target_status(fake_client(session), "Zebra Fake Lounge") is None
    assert attio.target_worked("In conversation") and attio.target_worked("Follow up sent")
    assert not attio.target_worked("Prespecting") and not attio.target_worked(None)
    assert not attio.target_worked("Haven't found contact")


def test_pipeline_note_from_attio_status():
    row = {"attio_list_status": "Contacted"}
    assert leadsheet.pipeline_note(row) == "In Attio: Contacted"
    assert leadsheet.pipeline_note({"attio_list_status": "New"}) == ""
    assert leadsheet.pipeline_note({"attio_list_status": "New",
                                    "attio_target_status": "In conversation"}) == \
        "In Attio: Target In conversation"
    assert leadsheet.pipeline_note({"on_speakeasy": True, "attio_list_status": "Not a fit"}) \
        == "Already on Speakeasy"


# --- CLI: counts only in the logs ---

def test_cli_attio_pull_skips_without_key(monkeypatch, caplog):
    for var in ("ATTIO_API_KEY", "ATTIO_WRITE_API_KEY", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["attio-pull"]) == 0
    assert "attio pull skipped (not configured)" in caplog.text


def test_cli_attio_pull_logs_counts_only(monkeypatch, caplog):
    from test_outreach import plan_row

    monkeypatch.setenv("ATTIO_API_KEY", "test-key")
    monkeypatch.setattr(cli.db, "connect", lambda: _NullConn())
    monkeypatch.setattr(cli.db, "init_schema", lambda conn: None)
    today = [plan_row(1, business_name="Zebra Fake Lounge"),
             plan_row(2, business_name="Quokka Fake Lounge", attio_list_status="Contacted",
                      pipeline="In Attio: Contacted")]
    older = [plan_row(3, business_name="Okapi Fake Lounge")]  # still on the plan from before
    monkeypatch.setattr(leadsheet, "load_rows", lambda conn, day, open_only=False, **kw:
                        today + older if open_only else today)
    saved = {}
    monkeypatch.setattr(feedback, "save_attio",
                        lambda conn, s, t, now: saved.update(statuses=s, targets=t))
    monkeypatch.setattr(feedback, "pull_attio",
                        lambda conn, s, now: {"contacted": 1, "rejected": 0})
    monkeypatch.setattr(attio, "list_statuses",
                        lambda client: {"TX|78701|2 FAKE ST": "Contacted"})
    monkeypatch.setattr(attio, "find_target_status", lambda client, name: "In conversation")
    monkeypatch.setattr(attio, "Client", lambda: object())
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["attio-pull"]) == 0
    # A stale status never hides a venue from the lookup: both are checked.
    assert set(saved["targets"]) == {"TX|78701|1 FAKE ST", "TX|78701|2 FAKE ST",
                                     "TX|78701|3 FAKE ST"}
    assert ("attio pull: list entries read 1 (worked by the team 1); leads moved to "
            "contacted 1, to rejected 0; targets looked up 3 (outreach under way 3)") \
        in caplog.text
    assert "Zebra" not in caplog.text and "Quokka" not in caplog.text
    assert "FAKE ST" not in caplog.text


def test_cli_review_accepts_outcome_statuses(monkeypatch, capsys):
    monkeypatch.setattr(cli.db, "connect", lambda: _NullConn())
    monkeypatch.setattr(cli.db, "init_schema", lambda conn: None)
    seen = []
    monkeypatch.setattr(feedback, "record_review",
                        lambda conn, ids, status, note: seen.append((ids, status)) or (2, 1))
    for status in ("replied", "won", "wrong_contact", "contacted", "approved"):
        assert cli.main(["review", "7", "8", "--status", status]) == 0
    assert [s for _, s in seen] == ["replied", "won", "wrong_contact", "contacted",
                                    "approved"]
    out = capsys.readouterr().out
    assert "1 venue(s) logged as wrong contact" in out
    with pytest.raises(SystemExit):
        cli.main(["review", "7", "--status", "lost"])


# --- against a disposable Postgres ---

def _keys(pg):
    with pg.cursor() as cur:
        cur.execute("SELECT id, venue_key, dba FROM records ORDER BY id")
        return {dba: (rid, key) for rid, key, dba in cur.fetchall()}


def test_wrong_contact_marks_channel_bad_and_rechecks(pg):
    from licmon import db

    _seed(pg, DAY1)
    db.init_schema(pg)
    zebra = lambda q: [place(name="Zebra Fake Nightclub")] if "Zebra" in q else []  # noqa: E731
    contact.run(pg, places=FakePlaces(zebra), website=FakeWebsite(site_links()), now=DAY1,
                cap=10)
    rid, key = _keys(pg)["Zebra Fake Nightclub"]
    [row] = [r for r in leadsheet.load_rows(pg, DAY1.date()) if r["venue_key"] == key]
    assert row["outreach_method"] == "Call" and row["contact_url_text"] == "(512) 555-0142"
    assert [p["name"] for p in outreach.build_plan([row])] == ["Zebra Fake Nightclub"]

    later = DAY1 + timedelta(days=2)
    assert feedback.record_review(pg, [rid], "wrong_contact", None, later) == (1, 1)
    with pg.cursor() as cur:
        cur.execute("SELECT status, next_check_at, bad_channels FROM contact_checks "
                    "WHERE venue_key = %s", (key,))
        status, due, bad = cur.fetchone()
        cur.execute("SELECT outcome, method FROM outreach_outcomes")
        assert cur.fetchall() == [("wrong_contact", "Call")]
    assert (status, due) == ("waiting", later)
    assert bad == [{"kind": "phone", "value": "(512) 555-0142"}]
    # Waiting now: off the lead tabs and the plan, still open for rechecks.
    [row] = [r for r in leadsheet.load_rows(pg, DAY1.date()) if r["venue_key"] == key]
    assert leadsheet.is_waiting(row) and row["review_status"] == "wrong_contact"

    # The next run checks it again and never offers the bad phone.
    run_at = later + timedelta(hours=1)
    places = FakePlaces(zebra)
    counts = contact.run(pg, places=places, website=FakeWebsite(site_links()), now=run_at,
                         cap=10)
    assert counts["recheck"] == 1 and counts["newly_reachable"] == 1
    [row] = [r for r in leadsheet.load_rows(pg, run_at.date()) if r["venue_key"] == key]
    assert row["outreach_method"] == "Facebook message"
    assert row["verified_phone"] is None
    assert row["newly_reachable"] == "Newly reachable"
    [entry] = outreach.build_plan([row])
    assert entry["method"] == "Facebook message" and "555-0142" not in entry["how"]


def test_review_outcomes_and_results_counts(pg):
    from licmon import db

    _seed(pg, DAY1)
    db.init_schema(pg)
    contact.run(pg, places=FakePlaces(lambda q: [place(name="Zebra Fake Nightclub")]
                                      if "Zebra" in q else []),
                website=FakeWebsite(site_links()), now=DAY1, cap=10)
    ids = _keys(pg)
    zebra, quokka = ids["Zebra Fake Nightclub"][0], ids["Quokka Fake Lounge"][0]
    feedback.record_review(pg, [zebra], "contacted", None, DAY1)
    feedback.record_review(pg, [zebra], "replied", "call back Tuesday", DAY1)
    feedback.record_review(pg, [quokka], "won", None, DAY1)
    feedback.record_review(pg, [quokka], "approved", None, DAY1)  # not an outcome
    lines = feedback.results_lines(feedback.results(pg, DAY1 + timedelta(days=1)))
    assert lines == ["Outreach results, last 30 days: contacted 1, replied 1, won 1, "
                     "wrong contact 0",
                     "Call: contacted 1, replied 1", "Unknown: won 1"]
    assert feedback.results(pg, DAY1 + timedelta(days=31)) == []
    rows = leadsheet.load_rows(pg, DAY1.date())
    assert {r["business_name"]: r["review_status"] for r in rows} == {
        "Zebra Fake Nightclub": "replied", "Quokka Fake Lounge": "approved",
        "Fake Corner Bistro": "new"}
    assert outreach.build_plan(rows) == []  # replied venue is done; the other is waiting


def test_attio_pull_moves_forward_only_and_hides_worked_venues(pg):
    from licmon import db

    _seed(pg, DAY1)
    db.init_schema(pg)
    def both(query):
        if "Zebra" in query:
            return [place(name="Zebra Fake Nightclub")]
        return [place(name="Quokka Fake Lounge", number="200")] if "Quokka" in query else []

    contact.run(pg, places=FakePlaces(both), website=FakeWebsite(site_links()), now=DAY1,
                cap=10)
    ids = _keys(pg)
    zebra_id, zebra = ids["Zebra Fake Nightclub"]
    quokka_id, quokka = ids["Quokka Fake Lounge"]
    bistro_id, bistro = ids["Fake Corner Bistro"]
    plan = outreach.build_plan(leadsheet.load_rows(pg, DAY1.date()))
    assert {p["name"] for p in plan} == {"Zebra Fake Nightclub", "Quokka Fake Lounge"}

    feedback.record_review(pg, [zebra_id], "won", None, DAY1)
    statuses = {zebra: "Contacted", quokka: "Not a fit", bistro: "Contacted"}
    moved = feedback.pull_attio(pg, statuses, DAY1)
    assert moved == {"contacted": 1, "rejected": 1}
    with pg.cursor() as cur:
        cur.execute("SELECT dba, review_status, review_notes FROM records ORDER BY id")
        got = {d: (s, n) for d, s, n in cur.fetchall()}
    assert got["Zebra Fake Nightclub"] == ("won", None)  # never set back
    assert got["Quokka Fake Lounge"] == ("rejected", feedback.ATTIO_NOTE)
    assert got["Fake Corner Bistro"] == ("contacted", feedback.ATTIO_NOTE)
    # Running it again changes nothing (already forward).
    assert feedback.pull_attio(pg, statuses, DAY1) == {"contacted": 0, "rejected": 0}

    # Reopen Quokka by hand; Attio still says Not a fit, and a Target with its
    # name is in conversation: the sheet flags it and the plan leaves it out.
    feedback.record_review(pg, [quokka_id], "new", None, DAY1)
    feedback.save_attio(pg, {quokka: "New"}, {quokka: "In conversation"}, DAY1)
    feedback.save_attio(pg, {zebra: "Contacted"}, {}, DAY1)  # never wipes the Target
    [row] = [r for r in leadsheet.load_rows(pg, DAY1.date()) if r["venue_key"] == quokka]
    assert row["pipeline"] == "In Attio: Target In conversation"
    assert outreach.left_out(row) == "In Attio: Target In conversation"
