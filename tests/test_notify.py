"""Tests for licmon.notify: short counts-only email plus XLSX attachment.

No network, no real database (except the pg integration test, which uses a
disposable Postgres), no real SMTP. All lead rows are synthetic. The central
rule under test: the email body never contains lead data.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from licmon import leadsheet, notify

DAY = date(2026, 9, 29)


def make_row(i, priority="A", metro="Houston", **over):
    """Synthetic leadsheet row: aggregates plus detail keys that must never
    reach the email body."""
    row = {
        "priority": priority,
        "metro": metro,
        "state": "TX",
        "dba": f"ZEBRA FAKE LOUNGE {i}",
        "legal_name": f"Zebra Fake Holdings {i} LLC",
        "address": f"{i} Zebra Stripe Way",
        "city": "Houston",
        "zip": "77002",
        "phone": f"555-010{i}-0000",
        "source_url": f"https://example.invalid/records/zebra-{i}",
        "record_ids": str(7000 + i),
        "qualify_reason": f"zebra qualify marker {i}",
    }
    row.update(over)
    return row


def ok_source(name="fake_tx"):
    return {"source": name, "status": "success",
            "started_at": "2026-09-29T12:00:00+00:00"}


def failed_source(name="fake_bad"):
    return {"source": name, "status": "failed",
            "started_at": "2026-09-29T12:00:00+00:00",
            "error_type": "SourceHTTPError"}


def sample_data():
    # 7 leads: A=3, B=2, C=2; Houston 3, Chicago 2, Dallas-Fort Worth 2
    leads = [
        make_row(1, "A", "Houston"),
        make_row(2, "A", "Houston"),
        make_row(3, "B", "Houston"),
        make_row(4, "A", "Dallas-Fort Worth"),
        make_row(5, "C", "Dallas-Fort Worth"),
        make_row(6, "B", "Chicago"),
        make_row(7, "C", "Chicago"),
    ]
    return {"leads": leads, "sources": [ok_source("fake_tx"), ok_source("fake_tx2")]}


@pytest.fixture
def fake_xlsx(monkeypatch):
    """Pin the workbook bytes so tests never depend on the real builder."""
    calls = {}

    def _build(rows, open_rows=None, outcomes=None):
        calls["rows"] = list(rows)
        calls["open_rows"] = open_rows
        calls["outcomes"] = outcomes
        return b"PK-fake"

    monkeypatch.setattr(leadsheet, "build_workbook", _build)
    return calls


def bodies(msg):
    plain = msg.get_body(preferencelist=("plain",)).get_content()
    html_body = msg.get_body(preferencelist=("html",)).get_content()
    return plain, html_body


# --- subject variants ---

def test_subject_counts(fake_xlsx):
    msg = notify.compose(sample_data(), DAY, sender="s@example.invalid",
                         recipients=["o@example.invalid"])
    assert str(msg["Subject"]) == "Nightlife leads for Tue, Sep 29: 7 new (3 A)"


def test_subject_failed_suffix_singular_plural(fake_xlsx):
    data = sample_data()
    data["sources"] = [ok_source(), failed_source()]
    msg = notify.compose(data, DAY, sender="s", recipients=["o"])
    assert str(msg["Subject"]).endswith(" (1 source needs attention)")
    data["sources"] = [failed_source("a"), failed_source("b")]
    msg = notify.compose(data, DAY, sender="s", recipients=["o"])
    assert str(msg["Subject"]).endswith(" (2 sources need attention)")


def test_zero_leads_subject(fake_xlsx):
    msg = notify.compose({"leads": [], "sources": [ok_source()]}, DAY,
                         sender="s", recipients=["o"])
    assert str(msg["Subject"]) == "Nightlife leads for Tue, Sep 29: no new leads"


def test_zero_leads_with_failure_suffix(fake_xlsx):
    msg = notify.compose({"leads": [], "sources": [failed_source()]}, DAY,
                         sender="s", recipients=["o"])
    assert str(msg["Subject"]).endswith(" (1 source needs attention)")


# --- body: counts and market line ---

def test_body_counts_and_market_line(fake_xlsx):
    msg = notify.compose(sample_data(), DAY, sender="s", recipients=["o"])
    plain, html_body = bodies(msg)
    assert "7 new nightlife leads today. They are in the attached spreadsheet." in plain
    assert "Nightclubs, lounges and ticketed venues (A): 3" in plain
    assert "Bars and event venues (B): 2" in plain
    assert "Restaurants (C): 2" in plain
    # markets sorted by count desc, then name: Houston 3, then the 2-count tie
    assert "By market: Houston 3, Chicago 2, Dallas-Fort Worth 2" in plain
    assert "All 2 sources checked in normally." in plain
    assert ("These leads come from public license filings. "
            "Nothing has contacted these businesses." in plain)
    for snippet in ("Nightclubs, lounges and ticketed venues (A): 3", "By market: Houston 3",
                    "All 2 sources checked in normally."):
        assert snippet in html_body


def test_body_has_no_lead_data(fake_xlsx):
    msg = notify.compose(sample_data(), DAY, sender="s", recipients=["o"])
    plain, html_body = bodies(msg)
    forbidden = ["ZEBRA FAKE LOUNGE", "Zebra Fake Holdings", "Zebra Stripe Way",
                 "555-010", "example.invalid/records/zebra",
                 "zebra qualify marker", "7001", "77002"]
    for secret in forbidden:
        assert secret not in plain, secret
        assert secret not in html_body, secret
    assert "http" not in plain and "http" not in html_body


def test_zero_lead_body_and_no_attachment(fake_xlsx):
    msg = notify.compose({"leads": [], "sources": [ok_source(), ok_source()]}, DAY,
                         sender="s", recipients=["o"])
    plain, html_body = bodies(msg)
    assert "No new leads today." in plain
    assert "All 2 sources checked in normally." in plain
    assert "Nothing has contacted these businesses." in plain
    assert "By market" not in plain
    assert list(msg.iter_attachments()) == []


def test_failed_source_line_titles_only(fake_xlsx):
    data = sample_data()
    data["sources"] = [ok_source("fake_tx"), failed_source("tx_tabc_pending")]
    msg = notify.compose(data, DAY, sender="s", recipients=["o"])
    plain, html_body = bodies(msg)
    expected = ("1 source had a problem today: "
                "Texas TABC pending original applications. "
                "The others ran normally. Ask Claude Code to check it.")
    assert expected in plain
    assert expected in html_body
    assert "SourceHTTPError" not in plain
    assert "SourceHTTPError" not in html_body


def test_failed_plural_lists_titles(fake_xlsx):
    data = sample_data()
    data["sources"] = [failed_source("tx_tabc_pending"), failed_source("ny_sla_pending")]
    msg = notify.compose(data, DAY, sender="s", recipients=["o"])
    plain, _ = bodies(msg)
    assert plain.startswith("7 new nightlife leads today.")
    assert ("2 sources had a problem today: "
            "Texas TABC pending original applications; "
            "New York SLA pending license applications. "
            "The others ran normally. Ask Claude Code to check it." in plain)
    assert "SourceHTTPError" not in plain


def test_html_escaping_and_no_links(fake_xlsx):
    data = {"leads": [make_row(1, "A", "<b>Zebra & Co</b>")],
            "sources": [ok_source()]}
    msg = notify.compose(data, DAY, sender="s", recipients=["o"])
    plain, html_body = bodies(msg)
    assert "<b>Zebra & Co</b>" in plain  # plain text needs no escaping
    assert "<b>" not in html_body
    assert "&lt;b&gt;Zebra &amp; Co&lt;/b&gt;" in html_body
    assert "href" not in html_body  # no links at all


# --- attachment ---

def test_attachment_is_xlsx(fake_xlsx):
    data = sample_data()
    msg = notify.compose(data, DAY, sender="s", recipients=["o"])
    parts = list(msg.iter_attachments())
    assert len(parts) == 1
    part = parts[0]
    assert part.get_filename() == "nightlife-leads-2026-09-29.xlsx"
    assert part.get_content_type() == leadsheet.XLSX_MIME
    assert part.get_content() == b"PK-fake"
    # the builder got the full lead rows (details live only in the file)
    assert fake_xlsx["rows"] == data["leads"]
    assert fake_xlsx["open_rows"] is None  # sample data carries no open list


def test_attachment_with_real_builder():
    data = sample_data()
    msg = notify.compose(data, DAY, sender="s", recipients=["o"])
    parts = list(msg.iter_attachments())
    assert len(parts) == 1
    assert parts[0].get_filename() == "nightlife-leads-2026-09-29.xlsx"
    payload = parts[0].get_content()
    assert isinstance(payload, bytes) and payload.startswith(b"PK") and len(payload) > 0


def test_write_preview_with_attachment(fake_xlsx, tmp_path):
    msg = notify.compose(sample_data(), DAY, sender="s", recipients=["o"])
    paths = notify.write_preview(msg, tmp_path / "prev")
    names = sorted(p.name for p in paths)
    assert names == ["message.eml", "nightlife-leads-2026-09-29.xlsx",
                     "preview.html", "preview.txt"]
    assert (tmp_path / "prev" / "nightlife-leads-2026-09-29.xlsx").read_bytes() == b"PK-fake"
    txt = (tmp_path / "prev" / "preview.txt").read_text()
    assert "7 new nightlife leads today" in txt
    assert "ZEBRA FAKE LOUNGE" not in txt
    assert "ZEBRA FAKE LOUNGE" not in (tmp_path / "prev" / "preview.html").read_text()


def test_write_preview_zero_leads(fake_xlsx, tmp_path):
    msg = notify.compose({"leads": [], "sources": [ok_source()]}, DAY,
                         sender="s", recipients=["o"])
    paths = notify.write_preview(msg, tmp_path / "prev")
    assert sorted(p.name for p in paths) == ["message.eml", "preview.html", "preview.txt"]


# --- send() with fake SMTP ---

class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.starttls_called = False
        self.login_args = None
        self.sent = None
        FakeSMTP.instances.append(self)

    def starttls(self):
        self.starttls_called = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSSL(FakeSMTP):
    pass


@pytest.fixture
def smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "user@example.invalid")
    monkeypatch.setenv("SMTP_PASSWORD", "s3cret-pw")
    monkeypatch.setenv("LEADS_EMAIL_TO", "owner@example.invalid")
    FakeSMTP.instances.clear()
    FakeSSL.instances.clear()
    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSSL)
    return smtplib


def test_send_starttls(smtp_env, fake_xlsx):
    msg = notify.compose(sample_data(), DAY, sender="s", recipients=["o"])
    notify.send(msg)
    assert len(FakeSMTP.instances) == 1
    conn = FakeSMTP.instances[0]
    assert (conn.host, conn.port, conn.timeout) == ("smtp.example.invalid", 587, 60)
    assert conn.starttls_called
    assert conn.login_args == ("user@example.invalid", "s3cret-pw")
    assert conn.sent is msg


def test_send_ssl_port_465(smtp_env, monkeypatch, fake_xlsx):
    monkeypatch.setenv("SMTP_PORT", "465")
    msg = notify.compose(sample_data(), DAY, sender="s", recipients=["o"])
    notify.send(msg)
    assert len(FakeSSL.instances) == 1
    conn = FakeSSL.instances[0]
    assert conn.port == 465
    assert not conn.starttls_called
    assert conn.login_args == ("user@example.invalid", "s3cret-pw")


def test_send_error_hides_secrets(smtp_env, fake_xlsx):
    class Boom(FakeSMTP):
        def send_message(self, msg):
            raise ConnectionError("boom to owner@example.invalid with s3cret-pw")

    smtp_env.SMTP = Boom
    msg = notify.compose(sample_data(), DAY, sender="s", recipients=["o"])
    with pytest.raises(notify.NotifyError) as ei:
        notify.send(msg)
    text = str(ei.value)
    assert "ConnectionError" in text
    for secret in ("s3cret-pw", "user@example.invalid", "owner@example.invalid",
                   "smtp.example.invalid"):
        assert secret not in text


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("SMTP_USERNAME", "smtp-user@example.invalid")
    monkeypatch.setenv("LEADS_EMAIL_TO", " a@example.invalid ,b@example.invalid,, ")
    monkeypatch.delenv("LEADS_EMAIL_FROM", raising=False)
    sender, recipients = notify.settings_from_env()
    assert sender == "smtp-user@example.invalid"
    assert recipients == ["a@example.invalid", "b@example.invalid"]
    monkeypatch.setenv("LEADS_EMAIL_FROM", "  sender@example.invalid ")
    assert notify.settings_from_env()[0] == "sender@example.invalid"


def test_email_configured(monkeypatch):
    for var in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "LEADS_EMAIL_TO"):
        monkeypatch.delenv(var, raising=False)
    assert not notify.email_configured()
    monkeypatch.setenv("SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("LEADS_EMAIL_TO", "o@example.invalid")
    assert not notify.email_configured()  # no sender account yet
    monkeypatch.setenv("SMTP_USERNAME", "o@example.invalid")
    assert notify.email_configured()


# --- load_daily integration (disposable Postgres only; needs real leadsheet) ---

def test_load_daily_integration(pg):
    import json
    from licmon import pipeline
    from licmon.models import Record, Snapshot
    from licmon.sources.base import Source

    now1 = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
    now2 = now1 + timedelta(days=1)

    class FakeNotifySource(Source):
        name = "fake_notify_tx"
        title = "fake"
        state = "TX"
        min_records = 1

        def __init__(self, rows):
            self.rows = rows

        def fetch(self, http):
            body = json.dumps(self.rows, sort_keys=True).encode()
            return [Snapshot(url="https://example.invalid/notify", body=body,
                             content_type="application/json", fetched_at=now1)]

        def parse(self, snapshots):
            for row in json.loads(snapshots[0].body):
                yield Record(
                    source=self.name, source_record_id=row["id"],
                    source_url=f"https://example.invalid/notify?id={row['id']}",
                    legal_name=row["name"], dba=None, license_type="MB",
                    license_description="Mixed Beverage Permit",
                    application_type="ORIGINAL", status="Received",
                    application_date=date.fromisoformat(row["date"]),
                    address="1 Test St", city="Austin", state="TX", zip="78701",
                    county="Travis", category="on_premise", raw=row)

    src = FakeNotifySource([
        {"id": "n1", "name": "Notify Test Rooftop Bar LLC", "date": "2026-09-28"},
        {"id": "n2", "name": "Notify Test Old Tavern LLC", "date": "2026-01-05"},
    ])
    pipeline.run([src], conn=pg, http=object(), now=now1, trigger="test")
    with pg.cursor() as cur:
        # source_runs.started_at defaults to wall-clock time; pin it so the
        # day filter is deterministic regardless of when tests run.
        cur.execute("UPDATE source_runs SET started_at=%s", (now1,))
    pg.commit()
    src.rows = [
        {"id": "n1", "name": "Notify Test Rooftop Bar LLC", "date": "2026-09-28"},
        {"id": "n2", "name": "Notify Test Old Tavern LLC", "date": "2026-01-05"},
        {"id": "n3", "name": "Notify Test Cocktail Lounge", "date": "2026-09-29"},
    ]
    pipeline.run([src], conn=pg, http=object(), now=now2, trigger="test")
    with pg.cursor() as cur:
        cur.execute("UPDATE source_runs SET started_at=%s WHERE started_at <> %s",
                      (now2, now1))
    pg.commit()

    day1 = notify.load_daily(pg, date(2026, 9, 29))
    assert len(day1["leads"]) >= 1
    for lead in day1["leads"]:
        assert lead["priority"] in ("A", "B", "C")
        assert ("market" in lead or "metro" in lead) and "state" in lead
    assert [(s["source"], s["status"]) for s in day1["sources"]] == [
        ("fake_notify_tx", "success")]
    # day-1 snapshot must not see the day-2 run's counts
    assert day1["sources"][0]["records_new"] == 0

    day2 = notify.load_daily(pg, date(2026, 9, 30))
    assert len(day2["leads"]) >= 1
    assert day2["sources"][0]["records_new"] == 1

    empty = notify.load_daily(pg, date(2026, 9, 27))
    assert empty["leads"] == [] and empty["sources"] == []


def test_market_key_fallback(fake_xlsx):
    """Real leadsheet rows carry the metro under "market", not "metro"."""
    row = make_row(1, "A", metro=None)
    row.pop("metro")
    row["market"] = "Houston"
    msg = notify.compose({"leads": [row], "sources": [ok_source()]}, DAY,
                         sender="s", recipients=["o"])
    plain, _ = bodies(msg)
    assert "By market: Houston 1" in plain


def test_cli_email_skips_when_not_configured(monkeypatch, caplog):
    from licmon import cli

    for var in ("SMTP_HOST", "SMTP_PASSWORD", "LEADS_EMAIL_TO"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)  # must not touch a DB
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["email"]) == 0
    assert "email skipped (not configured)" in caplog.text


def test_cli_email_preview_refuses_repo_folder(monkeypatch):
    from pathlib import Path

    import pytest

    from licmon import cli

    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = Path(cli.__file__).resolve().parents[2]
    with pytest.raises(SystemExit):
        cli.main(["email", "--preview", str(repo / "exports" / "x")])


def test_contact_counts_only_once_the_lookup_has_run(fake_xlsx):
    data = sample_data()
    plain, _ = bodies(notify.compose(data, DAY, sender="s", recipients=["o"]))
    assert "Waiting on contact" not in plain and "reachable" not in plain.lower()

    leads = data["leads"]
    leads[0].update(contact_status="reachable", newly_reachable="Newly reachable",
                    venue_key="k1", contact_url="tel:+15125550142")
    leads[1].update(contact_status="reachable", venue_key="k2")
    leads[2].update(contact_status="waiting", venue_key="k3")
    open_leads = [dict(leads[2]), make_row(9, venue_key="k9", contact_status="gave_up")]
    data["open_leads"] = open_leads
    plain, html_body = bodies(notify.compose(data, DAY, sender="s", recipients=["o"]))
    assert "Reachable today (verified contact): 2" in plain
    assert "Newly reachable (contact found on a recheck): 1" in plain
    assert "Waiting on contact: 2, on the Waiting on contact tab." in plain
    assert "Waiting on contact: 2" in html_body
    for body in (plain, html_body):
        # Only plan venues are named (the newly reachable one). Waiting
        # venues and filing phones never reach the body.
        assert "ZEBRA FAKE LOUNGE 3" not in body.upper()
        assert "ZEBRA FAKE LOUNGE 2" not in body.upper()  # reachable, nothing new
        assert "555-010" not in body and "Zebra Stripe Way" not in body


def test_email_counts_instagram_found_by_search(fake_xlsx):
    data = sample_data()
    leads = data["leads"]
    leads[0].update(contact_status="reachable", venue_key="k1", instagram_by_search=True)
    leads[1].update(contact_status="reachable", venue_key="k2")
    leads[2].update(contact_status="waiting", venue_key="k3", instagram_by_search=False)
    plain, html_body = bodies(notify.compose(data, DAY, sender="s", recipients=["o"]))
    assert "Instagram found by search: 1" in plain and "Instagram found by search: 1" in html_body
    assert plain.index("Newly reachable") < plain.index("Instagram found by search") < \
        plain.index("Waiting on contact")
    leads[0]["instagram_by_search"] = False
    plain, _ = bodies(notify.compose(data, DAY, sender="s", recipients=["o"]))
    assert "found by search" not in plain  # the line shows once the search found one


# --- the outreach plan in the body (owner approved: names and openers go
# only to LEADS_EMAIL_TO; logs stay counts-only) ---

def plan_data():
    from test_outreach import plan_row

    leads = [
        plan_row(1, contact_person="Jane Q Tester", platforms=["SevenRooms"],
                 current_platform="SevenRooms", phone="(512) 555-0199",
                 address="1 Zebra Stripe Way"),
        plan_row(2, business_name="Quokka Fake Lounge", contact_status="waiting",
                 outreach_method="Wait: no verified contact yet", contact_url=None,
                 contact_url_text=None),
        plan_row(3, business_name="Okapi Fake Lounge", review_status="contacted"),
    ]
    return {"leads": leads, "open_leads": [], "sources": [ok_source()],
            "outcomes": [("contacted", "Instagram DM", 2), ("replied", "Instagram DM", 1)]}


def test_email_body_has_the_plan_in_both_parts(fake_xlsx):
    from licmon import outreach

    msg = notify.compose(plan_data(), DAY, sender="s", recipients=["o"])
    plain, html_body = bodies(msg)
    [item] = outreach.build_plan(plan_data()["leads"])
    assert "Today's plan: 1 new venue to reach, best first. Send each one yourself." in plain
    assert "1. Zebra Fake Lounge, Austin | Approved | Hot, tier A" in plain
    assert ("Why reach out:\n- License approved, not open yet: pitch before launch\n"
            "- Lounge: tables, bottle service and the door\n"
            "- Uses SevenRooms: switch pitch") in plain
    assert "How: Instagram DM: @zebrafakelounge (Verified 90)" in plain
    assert "Second best way: Call" in plain
    assert "Contact person on the filing: Jane Q Tester" in plain
    assert "Opener (DM):\n\n" + item["opener"] in plain
    assert item["opener"].startswith("Hey Jane,")
    # counts block first, then the plan, then source health
    assert plain.index("By market") < plain.index("Today's plan") < plain.index("All 1 source")
    assert "Outreach results, last 30 days: contacted 2, replied 1, won 0, wrong contact 0" \
        in plain
    assert "Instagram DM: contacted 2, replied 1" in plain
    assert "<b>1. Zebra Fake Lounge, Austin | Approved | Hot, tier A</b>" in html_body
    assert '<a href="https://www.instagram.com/zebrafakelounge/">@zebrafakelounge</a>' \
        in html_body
    assert "<li>Uses SevenRooms: switch pitch</li>" in html_body
    assert "Hey Jane,\n\nCongrats on the upcoming opening of Zebra Fake Lounge." in html_body
    assert "Outreach results, last 30 days" in html_body
    for body in (plain, html_body):
        assert "Quokka" not in body and "Okapi" not in body  # not in the plan
        assert "555-0199" not in body and "Zebra Stripe Way" not in body  # sheet only
        assert "—" not in body
    plan_part = plain[plain.index("Today's plan"):plain.index("All 1 source")]
    assert ";" not in plan_part


def test_email_empty_plan_says_so(fake_xlsx):
    data = plan_data()
    data["leads"] = data["leads"][1:]
    plain, html_body = bodies(notify.compose(data, DAY, sender="s", recipients=["o"]))
    assert "No new reachable venues today." in plain
    assert "No new reachable venues today." in html_body
    assert "Today's plan:" not in plain


def test_no_plan_section_before_the_contact_lookup_runs(fake_xlsx):
    plain, html_body = bodies(notify.compose(sample_data(), DAY, sender="s",
                                             recipients=["o"]))
    assert "plan" not in plain.lower() and "reachable venues" not in plain
    assert "href" not in html_body


def test_cli_email_logs_counts_only_with_a_plan(monkeypatch, caplog):
    from licmon import cli
    from test_attio_slack import _NullConn

    for var, value in (("SMTP_HOST", "smtp.example.invalid"), ("SMTP_USERNAME", "u"),
                       ("SMTP_PASSWORD", "p"), ("LEADS_EMAIL_TO", "owner@example.invalid")):
        monkeypatch.setenv(var, value)
    monkeypatch.setattr(cli.db, "connect", lambda: _NullConn())
    monkeypatch.setattr(cli.db, "init_schema", lambda conn: None)
    monkeypatch.setattr(notify, "load_daily", lambda conn, day: plan_data())
    sent = []
    monkeypatch.setattr(notify, "send", sent.append)
    with caplog.at_level("INFO", logger="licmon"):
        assert cli.main(["email"]) == 0
    assert "email sent: 3 leads (3 tier A), plan 1 venues, to 1 recipient(s)" in caplog.text
    plain, _ = bodies(sent[0])
    assert "Zebra Fake Lounge" in plain  # the email itself has the plan
    for secret in ("Zebra", "Quokka", "Okapi", "zebrafakelounge", "Jane", "555",
                   "owner@example.invalid"):
        assert secret not in caplog.text
