"""Attio sync and Slack ping. No network: every HTTP call hits a fake session.
All rows are synthetic."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from licmon import attio, cli, leadsheet, slack


class FakeResp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {"data": {}}
        self.headers = headers or {}

    def json(self):
        return self._body


class FakeSession:
    """Records requests; `routes` maps (method, path suffix) -> response(s)."""

    def __init__(self, routes=None):
        self.headers = {}
        self.calls = []
        self.routes = routes or {}

    def request(self, method, url, json=None, params=None, timeout=None):
        self.calls.append((method, url.removeprefix(attio.API), json, params))
        for (m, suffix), resp in self.routes.items():
            if m == method and url.endswith(suffix):
                if isinstance(resp, list):
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                return resp
        return FakeResp()

    def post(self, url, json=None, timeout=None):
        self.calls.append(("POST", url, json, None))
        return self.routes.get("post", FakeResp())


def client(routes=None):
    return attio.Client(key="test-key", session=FakeSession(routes), sleep=lambda s: None)


def row(i, priority="A", hot=False, score=60, **kw):
    base = {
        "venue_key": f"TX|78701|{i} FAKE ST", "priority": priority,
        "hot": "Hot" if hot else "", "lead_score": score,
        "business_name": f"Zebra Fake Lounge {i}", "company": f"Zebra Fake {i} LLC",
        "stage": "In review", "market": "Austin", "state": "TX", "city": "Austin",
        "address": f"{i} Fake St", "zip": "78701", "phone": None,
        "license": "Mixed Beverage Permit", "filing": "New application",
        "filed_on": date(2026, 9, 20),
        "first_seen": datetime(2026, 9, 21, 15, tzinfo=timezone.utc),
        "record_url": "https://example.invalid/r", "map_url": "https://example.invalid/m",
        "google_url": None, "instagram_url": None, "whats_new": leadsheet.NEW_FILING,
    }
    base.update(kw)
    return base


# --- Attio setup ---

def test_setup_dry_run_without_key_lists_schema_only():
    steps = attio.setup(None, write=False)
    assert steps[0].startswith("create list License Leads (license_leads) on Targets "
                               "(target_client)")
    assert "create list field Venue key (text, unique)" in steps
    assert "  add option Moved to Targets" in steps
    assert not any("Zebra" in s for s in steps)


def test_setup_dry_run_with_key_only_reads():
    c = client({("GET", "/lists/license_leads"): FakeResp(404),
                ("GET", "/objects/target_client"): FakeResp(200, {"data": {}})})
    attio.setup(c, write=False)
    assert [(m, p) for m, p, *_ in c.session.calls] == [
        ("GET", "/lists/license_leads"), ("GET", "/objects/target_client")]


def test_setup_write_creates_list_fields_and_options_never_touches_targets():
    c = client({("GET", "/lists/license_leads"): FakeResp(404),
                ("GET", "/objects/target_client"): FakeResp(200, {"data": {}}),
                ("GET", "/lists/license_leads/attributes"): FakeResp(body={"data": []})})
    steps = attio.setup(c, write=True)
    calls = c.session.calls
    assert calls[2] == ("POST", "/lists", {"data": {
        "name": "License Leads", "api_slug": "license_leads",
        "parent_object": "target_client", "workspace_access": "full-access",
        "workspace_member_access": []}}, None)
    attrs = [body["data"] for m, path, body, _ in calls
             if m == "POST" and path == "/lists/license_leads/attributes"]
    assert len(attrs) == len(attio.ATTRIBUTES)
    key = next(a for a in attrs if a["api_slug"] == "venue_key")
    assert key == {"title": "Venue key", "description": None, "api_slug": "venue_key",
                   "type": "text", "is_required": False, "is_unique": True,
                   "is_multiselect": False, "config": {}}
    assert {a["type"] for a in attrs} <= {"text", "number", "select", "date"}
    options = [(path, body["data"]["title"]) for m, path, body, _ in calls
               if path.endswith("/options")]
    assert ("/lists/license_leads/attributes/team_status/options", "New") in options
    assert ("/lists/license_leads/attributes/priority/options", "Hot") in options
    assert len([o for o in options if "/stage/" in o[0]]) == 4
    # Targets is read once to confirm it exists and is never written to
    assert not any(m != "GET" and "/objects/" in p for m, p, *_ in calls)
    assert "create list field Status (select)" in steps


def test_setup_stops_if_list_exists():
    c = client({("GET", "/lists/license_leads"): FakeResp(200, {"data": {}})})
    with pytest.raises(attio.AttioError, match="already exists"):
        attio.setup(c, write=True)
    assert [m for m, *_ in c.session.calls] == ["GET"]  # nothing written


# --- Attio sync ---

class AttioFake(FakeSession):
    """A tiny in-memory Attio: list entries keyed by venue key, Targets by
    name. Answers only the calls the sync makes."""

    def __init__(self, entries=None, targets=None, options=("Venue", "Organizer"),
                 statuses=("Prespecting",), priorities=("Hot", "A", "B"),
                 list_fields=("venue_key", "priority", "venue_history")):
        super().__init__()
        self.list_fields = list(list_fields)  # the list's attribute slugs
        self.entries = dict(entries or {})   # venue key -> entry id
        self.targets = dict(targets or {})   # name -> record id
        self.options, self.statuses = options, statuses
        self.priorities = list(priorities)   # the list's Priority select options

    def request(self, method, url, json=None, params=None, timeout=None):
        path = url.removeprefix(attio.API)
        self.calls.append((method, path, json, params))
        if path == "/lists/license_leads/entries/query":
            keys = json["filter"]["venue_key"]["$in"]
            return FakeResp(body={"data": [
                {"id": {"entry_id": eid}, "entry_values": {"venue_key": [{"value": k}]}}
                for k, eid in self.entries.items() if k in keys]})
        if path == "/objects/target_client/records/query":
            needle = json["filter"]["company_1"]["value"]["$contains"].lower()
            return FakeResp(body={"data": [
                {"id": {"record_id": rid}, "values": {"company_1": [{"value": n}]}}
                for n, rid in self.targets.items() if needle in n.lower()]})
        if path == "/objects/target_client/attributes/company_1":
            return FakeResp(body={"data": {"api_slug": "company_1"}})
        if path == "/objects/target_client/attributes/client_type/options":
            return FakeResp(body={"data": [{"title": t, "is_archived": False}
                                           for t in self.options]})
        if path == "/objects/target_client/attributes/status/statuses":
            return FakeResp(body={"data": [{"title": t, "is_archived": False}
                                           for t in self.statuses]})
        if path == "/lists/license_leads/attributes/priority/options":
            if method == "POST":
                self.priorities.append(json["data"]["title"])
                return FakeResp(body={"data": {"title": json["data"]["title"]}})
            return FakeResp(body={"data": [{"title": t, "is_archived": False}
                                           for t in self.priorities]})
        if path == "/lists/license_leads/attributes":
            if method == "POST":
                self.list_fields.append(json["data"]["api_slug"])
                return FakeResp(body={"data": {"api_slug": json["data"]["api_slug"]}})
            return FakeResp(body={"data": [{"api_slug": a} for a in self.list_fields]})
        if method == "POST" and path == "/objects/target_client/records":
            rid = f"rec-{len(self.targets) + 1}"
            self.targets[json["data"]["values"]["company_1"]] = rid
            return FakeResp(body={"data": {"id": {"record_id": rid}}})
        return FakeResp()

    def made(self, method, path):
        return [body for m, p, body, _ in self.calls if m == method and p == path]


def fake_client(session):
    return attio.Client(key="test-key", session=session, sleep=lambda s: None)


def test_entry_values_and_update_values():
    values = attio.entry_values(row(1, hot=True, score=85))
    assert values["venue_key"] == "TX|78701|1 FAKE ST"
    assert values["priority"] == "Hot" and values["score"] == 85
    assert values["stage"] == "In review" and values["team_status"] == "New"
    assert values["filed_on"] == "2026-09-20" and values["first_seen"] == "2026-09-21"
    assert values["address"] == "1 Fake St, Austin, TX, 78701"
    assert values["owner_company"] == "Zebra Fake 1 LLC"
    assert "phone" not in values and "google_link" not in values  # blanks left out
    assert "name" not in values  # the venue name lives on the parent Target
    assert attio.update_values(row(1)) == {"stage": "In review", "score": 60,
                                           "priority": "A"}
    assert attio.entry_values(row(1, priority="B"))["priority"] == "B"
    assert attio.target_values(row(1)) == {
        "company_1": "Zebra Fake Lounge 1", "client_type": [{"option": "Venue"}],
        "status": [{"status": "Prespecting"}]}


def test_sync_creates_minimal_target_and_list_entry():
    s = AttioFake()
    counts = attio.sync(fake_client(s), [row(1, hot=True, score=90),
                                         row(2, priority="B", score=59)],
                        write=True, cap=25, min_b=60)
    assert counts == {"candidates": 1, "hot": 1, "b": 0, "created": 1, "reused": 0,
                      "added": 1, "updated": 0, "skipped": 0, "options_added": 0,
                      "b_held": 0, "permits_left_out": 0, "history_field_added": 0,
                      "history_field_missing": False, "no_contact_held": 0,
                      "contact_fields_added": 0, "contact_fields_missing": False,
                      "written": True}
    [target] = s.made("POST", "/objects/target_client/records")
    assert target == {"data": {"values": {
        "company_1": "Zebra Fake Lounge 1", "client_type": [{"option": "Venue"}],
        "status": [{"status": "Prespecting"}]}}}
    [entry] = s.made("POST", "/lists/license_leads/entries")
    assert entry["data"]["parent_record_id"] == "rec-1"
    assert entry["data"]["parent_object"] == "target_client"
    assert entry["data"]["entry_values"]["team_status"] == "New"
    assert entry["data"]["entry_values"]["venue_key"] == "TX|78701|1 FAKE ST"
    # nothing structural is ever done to Targets
    assert not any("/attributes" in p for m, p, *_ in s.calls if m != "GET")


def test_sync_reuses_existing_target_by_name_case_insensitively():
    # "Lounge 10" also contains "Lounge 1" and comes back first: exact wins
    s = AttioFake(targets={"Zebra Fake Lounge 10": "rec-other",
                           "ZEBRA FAKE LOUNGE 1 ": "rec-team"})
    counts = attio.sync(fake_client(s), [row(1)], write=True, cap=0)  # cap 0: reuse still ok
    assert counts["reused"] == 1 and counts["created"] == 0 and counts["added"] == 1
    assert s.made("POST", "/objects/target_client/records") == []
    [entry] = s.made("POST", "/lists/license_leads/entries")
    assert entry["data"]["parent_record_id"] == "rec-team"
    # a near miss is not a match: a new Target is made instead
    s = AttioFake(targets={"Zebra Fake Lounge 10": "rec-other"})
    assert attio.sync(fake_client(s), [row(1)], write=True, cap=25)["created"] == 1


def test_sync_updates_existing_entry_without_touching_team_status():
    s = AttioFake(entries={"TX|78701|1 FAKE ST": "ent-1"})
    counts = attio.sync(fake_client(s), [row(1, hot=True, score=91, stage="Approved")],
                        write=True, cap=25)
    assert counts["updated"] == 1 and counts["added"] == 0 and counts["created"] == 0
    [patch] = s.made("PATCH", "/lists/license_leads/entries/ent-1")
    assert patch == {"data": {"entry_values": {"stage": "Approved", "score": 91,
                                               "priority": "Hot"}}}
    # one query, one read of the Priority options, one of the list fields, one update
    assert [m for m, *_ in s.calls] == ["POST", "GET", "GET", "PATCH"]


def test_sync_caps_new_targets_highest_score_first():
    rows = [row(1, hot=True, score=90), row(2, score=70), row(3, score=65),
            row(4, priority="C", score=99), row(5, score=50),
            row(1, hot=True, score=90)]  # same venue twice: one entry
    s = AttioFake(entries={"TX|78701|3 FAKE ST": "ent-3"},
                  targets={"Zebra Fake Lounge 5": "rec-team"})
    counts = attio.sync(fake_client(s), rows + [row(6, score=40)], write=True, cap=1)
    # 1 created (venue 1), venue 2 over the cap, 3 updated, 5 reused, 6 over the cap
    assert counts == {"candidates": 5, "hot": 1, "b": 0, "created": 1, "reused": 1,
                      "added": 2, "updated": 1, "skipped": 2, "options_added": 0,
                      "b_held": 0, "permits_left_out": 0, "history_field_added": 0,
                      "history_field_missing": False, "no_contact_held": 0,
                      "contact_fields_added": 0, "contact_fields_missing": False,
                      "written": True}
    [target] = s.made("POST", "/objects/target_client/records")
    assert target["data"]["values"]["company_1"] == "Zebra Fake Lounge 1"
    parents = [e["data"]["parent_record_id"]
               for e in s.made("POST", "/lists/license_leads/entries")]
    assert sorted(parents) == ["rec-2", "rec-team"]  # rec-2: the new Target


def test_sync_selects_hot_a_and_strong_b_never_adult():
    rows = [row(1, hot=True, score=90), row(2, score=55),
            row(3, priority="B", score=60), row(4, priority="B", score=59),
            row(5, priority="C", score=95),
            row(6, score=88, adult=True), row(7, priority="B", score=80, adult=True)]
    picks = attio.candidates(rows, min_b=60)
    assert [r["venue_key"].split("|")[2] for r in picks] == ["1 FAKE ST", "3 FAKE ST",
                                                             "2 FAKE ST"]
    s = AttioFake()
    counts = attio.sync(fake_client(s), rows, write=True, cap=50, min_b=60)
    assert counts["candidates"] == 3 and counts["b"] == 1 and counts["added"] == 3
    priorities = sorted(e["data"]["entry_values"]["priority"]
                        for e in s.made("POST", "/lists/license_leads/entries"))
    assert priorities == ["A", "B", "Hot"]
    sent = json.dumps(s.calls)
    assert "Lounge 6" not in sent and "Lounge 7" not in sent  # adult: never sent


def test_sync_adds_missing_b_option_before_first_write():
    s = AttioFake(priorities=("Hot", "A"))  # the live list was made with Hot and A
    counts = attio.sync(fake_client(s), [row(3, priority="B", score=70)], write=True,
                        cap=50, min_b=60)
    assert counts["options_added"] == 1 and s.priorities == ["Hot", "A", "B"]
    methods = [(m, p) for m, p, *_ in s.calls]
    option_post = methods.index(("POST", "/lists/license_leads/attributes/priority/options"))
    first_write = next(i for i, (m, p) in enumerate(methods)
                       if (m, p) in (("POST", "/objects/target_client/records"),
                                     ("POST", "/lists/license_leads/entries")))
    assert option_post < first_write
    assert s.made("POST", "/lists/license_leads/attributes/priority/options") == [
        {"data": {"title": "B"}}]
    # next run: B exists, nothing added
    counts = attio.sync(fake_client(s), [row(4, priority="B", score=70)], write=True,
                        cap=50, min_b=60)
    assert counts["options_added"] == 0
    assert len(s.made("POST", "/lists/license_leads/attributes/priority/options")) == 1
    # a key that cannot add the option: Hot and A still go, B waits
    s = AttioFake(priorities=("Hot", "A"))
    real = s.request

    def no_schema_scope(method, url, json=None, params=None, timeout=None):
        if method == "POST" and url.endswith("/attributes/priority/options"):
            s.calls.append((method, url.removeprefix(attio.API), json, params))
            return FakeResp(403, {"code": "missing_scope"})
        return real(method, url, json=json, params=params, timeout=timeout)

    s.request = no_schema_scope
    counts = attio.sync(fake_client(s), [row(1, hot=True, score=90),
                                         row(3, priority="B", score=70)],
                        write=True, cap=50, min_b=60)
    assert (counts["added"], counts["b"], counts["b_held"]) == (1, 0, 1)
    [entry] = s.made("POST", "/lists/license_leads/entries")
    assert entry["data"]["entry_values"]["priority"] == "Hot"
    # no B leads today: a refused option add does not stop the sync
    assert s.priorities == ["Hot", "A"]  # still no B option
    counts = attio.sync(fake_client(s), [row(8, hot=True, score=90)], write=True, cap=50)
    assert counts["added"] == 1 and counts["b_held"] == 0
    # a dry run never adds the option
    s = AttioFake(priorities=("Hot", "A"))
    attio.sync(fake_client(s), [row(3, priority="B", score=70)], write=False, cap=50)
    assert s.priorities == ["Hot", "A"]


def test_sync_default_cap_is_50(monkeypatch):
    monkeypatch.delenv("ATTIO_DAILY_CAP", raising=False)
    counts = attio.sync(None, [row(i, score=70) for i in range(60)], write=False)
    assert counts["added"] == 50 and counts["skipped"] == 10


def test_sync_checks_target_options_before_creating():
    s = AttioFake(statuses=("1st Outreach sent",))  # Prespecting renamed in Attio
    with pytest.raises(attio.AttioError, match="Prespecting missing on target_client.status"):
        attio.sync(fake_client(s), [row(1)], write=True, cap=25)
    assert s.made("POST", "/objects/target_client/records") == []


def test_sync_dry_run_writes_nothing(monkeypatch):
    monkeypatch.setenv("ATTIO_DAILY_CAP", "1")
    counts = attio.sync(None, [row(1, score=80), row(2, score=70)], write=False)
    assert counts["created"] == 1 and counts["skipped"] == 1 and not counts["written"]
    s = AttioFake(entries={"TX|78701|2 FAKE ST": "ent-2"})
    counts = attio.sync(fake_client(s), [row(1), row(2), row(3)], write=False, cap=25)
    assert counts["created"] == 2 and counts["updated"] == 1
    assert {m for m, *_ in s.calls} <= {"GET", "POST"}
    assert all(p.endswith("/query") for m, p, *_ in s.calls if m == "POST")  # reads only


def test_errors_carry_code_but_never_the_body():
    body = {"status_code": 400, "type": "invalid_request_error", "code": "quota_exceeded",
            "message": "Zebra Fake Lounge 1 at 1 Fake St"}
    c = client({("POST", "/entries/query"): FakeResp(400, body)})
    with pytest.raises(attio.AttioError) as ei:
        attio.sync(c, [row(1)], write=True)
    assert str(ei.value) == "attio failed (HTTP 400 quota_exceeded)"
    c = client({("POST", "/entries/query"): FakeResp(403, {"code": "Zebra secret words"})})
    with pytest.raises(attio.AttioError) as ei:
        attio.sync(c, [row(1)], write=True)
    assert str(ei.value) == "attio failed (HTTP 403)"  # not an identifier: dropped
    c = client({("POST", "/entries/query"): FakeResp(404)})
    with pytest.raises(attio.AttioError, match="attio-setup"):
        attio.sync(c, [row(1)], write=True)


def test_client_retries_rate_limit():
    c = client({("POST", "/entries/query"): [
        FakeResp(429, headers={"Retry-After": "Tue, 23 May 2023 14:42:01 GMT"}),
        FakeResp(body={"data": []})]})
    waits = []
    c.sleep = waits.append
    assert attio.sync(c, [], write=False)["candidates"] == 0  # no picks: no calls
    attio.existing_entries(c, ["k"])
    assert len(c.session.calls) == 2 and waits == [0.0]  # date in the past: no wait
    assert attio._retry_wait("3") == 3.0 and attio._retry_wait("999") == 10.0


def test_settings(monkeypatch):
    for var in ("ATTIO_API_KEY", "ATTIO_WRITE_API_KEY", "ATTIO_DAILY_CAP"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("ATTIO_MIN_B_SCORE", raising=False)
    assert not attio.configured() and attio.daily_cap() == 50
    assert attio.min_b_score() == 60
    monkeypatch.setenv("ATTIO_MIN_B_SCORE", "70")
    assert attio.min_b_score() == 70
    monkeypatch.setenv("ATTIO_MIN_B_SCORE", "high")
    assert attio.min_b_score() == 60
    monkeypatch.setenv("ATTIO_API_KEY", "read")
    monkeypatch.setenv("ATTIO_WRITE_API_KEY", "write")
    assert attio.api_key() == "write"
    monkeypatch.setenv("ATTIO_DAILY_CAP", "40")
    assert attio.daily_cap() == 40
    monkeypatch.setenv("ATTIO_DAILY_CAP", "lots")
    assert attio.daily_cap() == 50


def test_counts_file_roundtrip(tmp_path):
    path = tmp_path / "attio-counts.json"
    attio.write_counts(path, {"added": 3, "skipped": 0})
    assert attio.read_counts(path) == {"added": 3, "skipped": 0}
    assert attio.read_counts(tmp_path / "missing.json") is None


def test_cli_attio_sync_skips_when_not_configured(monkeypatch, caplog):
    for var in ("ATTIO_API_KEY", "ATTIO_WRITE_API_KEY", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)  # must not touch a DB
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["attio-sync", "--write"]) == 0
    assert "attio sync skipped (not configured)" in caplog.text


def test_cli_attio_sync_logs_counts_only(monkeypatch, caplog, tmp_path):
    monkeypatch.setenv("ATTIO_API_KEY", "test-key")
    monkeypatch.setattr(cli.db, "connect", lambda: _NullConn())
    monkeypatch.setattr(leadsheet, "load_rows", lambda conn, day, open_only=False: [
        row(1, hot=True, score=90), row(2, priority="B", score=40)])
    monkeypatch.delenv("ATTIO_MIN_B_SCORE", raising=False)
    real_client = attio.Client
    fake = AttioFake()
    monkeypatch.setattr(attio, "Client",
                        lambda: real_client(key="k", session=fake, sleep=lambda s: None))
    counts_file = tmp_path / "c.json"
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["attio-sync", "--write", "--counts", str(counts_file)]) == 0
    assert ("list entries added 1, updated 0; new Targets 1, existing Targets reused 0; "
            "skipped over daily cap 0") in caplog.text
    assert "Zebra" not in caplog.text and "FAKE ST" not in caplog.text
    assert json.loads(counts_file.read_text())["added"] == 1


def test_cli_attio_sync_error_logs_code_only(monkeypatch, caplog):
    monkeypatch.setenv("ATTIO_API_KEY", "test-key")
    monkeypatch.setattr(cli.db, "connect", lambda: _NullConn())
    monkeypatch.setattr(leadsheet, "load_rows", lambda conn, day, open_only=False: [row(1)])
    real_client = attio.Client
    fake = FakeSession({("POST", "/entries/query"): FakeResp(
        400, {"code": "quota_exceeded", "message": "Zebra Fake Lounge 1"})})
    monkeypatch.setattr(attio, "Client",
                        lambda: real_client(key="k", session=fake, sleep=lambda s: None))
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["attio-sync", "--write"]) == 1
    assert "attio failed (HTTP 400 quota_exceeded)" in caplog.text
    assert "Zebra" not in caplog.text


class _NullConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- Slack ---

def test_slack_message_counts_and_top_hot_names():
    rows = [row(i, hot=True, score=80 + i, city="Houston", stage="Approved")
            for i in range(1, 8)]
    rows += [row(10, score=60), row(11, priority="B"), row(12, priority="B")]
    text = slack.compose(rows, {"added": 8, "updated": 2, "skipped": 0},
                         "https://app.attio.example/license-leads")
    lines = text.split("\n")
    assert lines[0] == "New license leads: 10 today (7 Hot, 1 A, 2 B)"
    assert lines[1].startswith("Hot: Zebra Fake Lounge 7, Houston (Approved) · ")
    assert lines[1].count("Zebra") == 5 and lines[1].endswith("· and 2 more")
    assert lines[2] == ("Added to Attio: 8 new, 2 updated · Full list in today's email.  "
                        "<https://app.attio.example/license-leads|Open License Leads in Attio>")
    for private in ("Fake St", "78701", "LLC", "example.invalid", "Mixed Beverage"):
        assert private not in text
    assert "—" not in text


def test_slack_message_without_hot_or_attio_and_escaping():
    text = slack.compose([row(1, priority="B", business_name="Fake <Bar> & Co")])
    assert text == "New license leads: 1 today (1 B)\nFull list in today's email."
    hot = slack.compose([row(1, hot=True, business_name="Fake <Bar> & Co", city="Austin")],
                        {"added": 0, "updated": 0, "skipped": 3})
    assert "Fake &lt;Bar&gt; &amp; Co, Austin (In review)" in hot
    assert "3 more over today's Attio limit, in the spreadsheet" in hot


def test_slack_counts_b_sent_to_attio_and_never_names_adult():
    rows = [row(1, hot=True, score=90, city="Houston"),
            row(2, hot=True, score=95, city="Miami", adult=True,
                business_name="Zebra Adult Fake Club"),
            row(3, priority="B", score=70)]
    text = slack.compose(rows, {"added": 2, "updated": 1, "b": 1, "skipped": 0})
    assert "Zebra Adult Fake Club" not in text and "Miami" not in text
    assert "Hot: Zebra Fake Lounge 1, Houston (In review)" in text
    assert "Added to Attio: 2 new, 1 updated (1 B)" in text
    assert "—" not in text


def test_slack_posts_only_on_news():
    assert slack.is_news([row(1, whats_new=leadsheet.STAGE_ADVANCED)])
    assert slack.is_news([row(1, whats_new=leadsheet.NEW_FILING)])
    assert not slack.is_news([row(1, whats_new=leadsheet.DETAILS_CHANGED)])
    assert not slack.is_news([])


def test_slack_post_and_errors():
    s = FakeSession()
    slack.post("hello", url="https://hooks.example.invalid/T/B/X", session=s)
    assert s.calls == [("POST", "https://hooks.example.invalid/T/B/X", {"text": "hello"}, None)]
    bad = FakeSession({"post": FakeResp(403)})
    with pytest.raises(slack.SlackError) as ei:
        slack.post("hello", url="https://hooks.example.invalid/T/B/SECRET", session=bad)
    assert str(ei.value) == "slack failed (HTTP 403)"
    assert "SECRET" not in str(ei.value)


def test_cli_slack_skips(monkeypatch, caplog):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["slack"]) == 0
    assert "slack skipped (not configured)" in caplog.text

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example.invalid/x")
    monkeypatch.setattr(cli.db, "connect", lambda: _NullConn())
    monkeypatch.setattr(leadsheet, "load_rows", lambda conn, day, open_only=False: [
        row(1, whats_new=leadsheet.DETAILS_CHANGED)])
    posted = []
    monkeypatch.setattr(slack, "post", lambda text: posted.append(text))
    caplog.clear()
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["slack"]) == 0
    assert "slack skipped (no new or stage-advanced leads)" in caplog.text
    assert posted == []

    monkeypatch.setattr(leadsheet, "load_rows", lambda conn, day, open_only=False: [
        row(1, hot=True, score=90)])
    caplog.clear()
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["slack"]) == 0
    assert len(posted) == 1 and "Zebra Fake Lounge 1" in posted[0]
    assert "slack posted: 1 leads (1 hot)" in caplog.text
    assert "Zebra" not in caplog.text


def test_cli_slack_preview_refused_in_actions(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(SystemExit):
        cli.main(["slack", "--preview"])


# --- contact lookup (contact.py) ---

def reachable_row(i, **kw):
    base = dict(contact_status="reachable", outreach_method="Instagram DM",
                outreach_second="Call", confidence="Verified 90",
                confidence_reason="Google listing at the same address, same name",
                verified_phone="(512) 555-0142",
                verified_instagram_url="https://www.instagram.com/zebrafake/",
                verified_website_url="https://zebrafake.test/",
                verified_facebook_url="https://www.facebook.com/zebrafake",
                verified_email="hello@zebrafake.test", phone="(512) 555-0199",
                instagram_url="https://www.google.com/search?q=site%3Ainstagram.com")
    base.update(kw)
    return row(i, **base)


def test_attio_sends_only_reachable_venues_once_contacts_are_checked():
    rows = [reachable_row(1, hot=True, score=90),
            row(2, score=80, contact_status="waiting"),
            row(3, score=75, contact_status="gave_up"),
            row(4, score=70)]  # not checked (over the lookup's cap): waits too
    assert [r["venue_key"] for r in attio.candidates(rows)] == ["TX|78701|1 FAKE ST"]
    assert len(attio.candidates(rows, require_contact=False)) == 4
    # Nothing checked at all (no Places key yet): everything works as before.
    assert len(attio.candidates([row(2, score=80), row(4, score=70)])) == 2
    counts = attio.sync(None, rows, write=False)
    assert counts["candidates"] == 1 and counts["no_contact_held"] == 3


def test_attio_entry_carries_verified_contact_not_search_links():
    values = attio.entry_values(reachable_row(1))
    assert values["phone"] == "(512) 555-0142"  # never the filing phone
    assert values["instagram_link"] == "https://www.instagram.com/zebrafake/"
    assert values["google_link"] == "https://zebrafake.test/"
    assert values["facebook_link"] == "https://www.facebook.com/zebrafake"
    assert values["contact_email"] == "hello@zebrafake.test"
    assert values["best_way_to_reach"] == "Instagram DM (then Call)"
    assert values["contact_confidence"] == ("Verified 90. Google listing at the same "
                                            "address, same name")
    no_phone = attio.entry_values(reachable_row(1, verified_phone=None))
    assert "phone" not in no_phone
    assert set(attio.update_values(reachable_row(1))) >= {
        "phone", "instagram_link", "best_way_to_reach", "contact_confidence"}
    # Unchecked rows keep today's values and refresh only the old fields.
    assert attio.entry_values(row(2, phone="(512) 555-0100"))["phone"] == "(512) 555-0100"
    assert set(attio.update_values(row(2))) <= set(attio.UPDATE_FIELDS)
    assert "—" not in json.dumps([a[1] for a in attio.ATTRIBUTES])


def test_attio_adds_missing_contact_fields_before_first_write():
    s = AttioFake()
    counts = attio.sync(fake_client(s), [reachable_row(1, hot=True, score=90)], write=True)
    assert counts["contact_fields_added"] == 4 and not counts["contact_fields_missing"]
    made = [b["data"]["api_slug"] for b in s.made("POST", "/lists/license_leads/attributes")]
    assert made == ["best_way_to_reach", "contact_confidence", "contact_email",
                    "facebook_link"]
    [entry] = s.made("POST", "/lists/license_leads/entries")
    assert entry["data"]["entry_values"]["best_way_to_reach"] == "Instagram DM (then Call)"

    class Refuses(AttioFake):
        def request(self, method, url, json=None, params=None, timeout=None):
            if method == "POST" and url.endswith("/lists/license_leads/attributes") \
                    and json["data"]["api_slug"] != "venue_history":
                self.calls.append((method, url.removeprefix(attio.API), json, params))
                return FakeResp(403, {"code": "missing_scope"})
            return super().request(method, url, json, params, timeout)

    s = Refuses()
    counts = attio.sync(fake_client(s), [reachable_row(1, hot=True, score=90)], write=True)
    assert counts["contact_fields_missing"] and counts["added"] == 1
    [entry] = s.made("POST", "/lists/license_leads/entries")
    assert "best_way_to_reach" not in entry["data"]["entry_values"]
    assert entry["data"]["entry_values"]["phone"] == "(512) 555-0142"
    # Without checked contacts, no contact field is touched (calls as before).
    s = AttioFake()
    attio.sync(fake_client(s), [row(1, hot=True, score=90)], write=True)
    assert [b["data"]["api_slug"] for b in s.made("POST", "/lists/license_leads/attributes")
            ] == []


def test_slack_names_newly_reachable_venues_without_contact_details():
    rows = [reachable_row(1, hot=False, whats_new=leadsheet.DETAILS_CHANGED,
                          newly_reachable="Newly reachable", business_name="Zebra Fake Club"),
            reachable_row(2, newly_reachable="Newly reachable", adult=True,
                          whats_new=leadsheet.DETAILS_CHANGED)]
    assert slack.is_news(rows)
    text = slack.compose(rows)
    assert "Newly reachable (contact found on a recheck): Zebra Fake Club, Austin" in text
    assert "Zebra Fake Lounge 2" not in text  # adult: never named
    for secret in ("555", "zebrafake", "instagram.com", "@", "Fake St"):
        assert secret not in text
    assert not slack.is_news([row(1, whats_new=leadsheet.DETAILS_CHANGED)])
