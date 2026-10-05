"""Daily lead email: a short counts-only note plus the full spreadsheet.

The email body never contains lead data: no names, addresses, phones, links
to records, or record ids. All detail lives ONLY in the attached Excel
workbook built by ``licmon.leadsheet`` (New, Existing venues, All open, one
tab per state).

Public Actions logs must never contain lead data, email addresses or SMTP
credentials. This module logs nothing with values in it: senders report only
counts and "email sent" / "email failed (<ErrorType>)".
"""

from __future__ import annotations

import html
import logging
import os
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger("licmon")

FOOTER = ("These leads come from public license filings. "
          "Nothing has contacted these businesses.")


class NotifyError(RuntimeError):
    """Email failure with a value-free message (error type names only)."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_daily(conn, day: date) -> dict:
    """Load one queue day: leads via leadsheet plus the latest source_runs
    row per source started on or before the end of `day`."""
    from . import leadsheet  # lazy: keeps cli -> notify -> leadsheet one-way

    leads = leadsheet.load_rows(conn, day)
    open_leads = leadsheet.load_rows(conn, None, open_only=True)

    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    cutoff = day_start + timedelta(days=1)  # end of `day`, exclusive
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (source) source, status, started_at,
                      records_fetched, records_new, records_changed,
                      records_qualified_queued, error_type
               FROM source_runs WHERE started_at < %s
               ORDER BY source, started_at DESC""",
            (cutoff,))
        srows = cur.fetchall()
        snames = [d.name for d in cur.description]
    sources = [dict(zip(snames, r)) for r in srows]
    return {"leads": leads, "open_leads": open_leads, "sources": sources}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def settings_from_env() -> tuple[str, list[str]]:
    """Return (sender, recipients). Sender defaults to SMTP_USERNAME."""
    sender = (os.environ.get("LEADS_EMAIL_FROM", "").strip()
              or os.environ.get("SMTP_USERNAME", "").strip())
    raw = os.environ.get("LEADS_EMAIL_TO", "")
    recipients = [part.strip() for part in raw.split(",") if part.strip()]
    return sender, recipients


def email_configured() -> bool:
    """True when the required settings are all non-empty."""
    return bool(os.environ.get("SMTP_HOST", "").strip()
                and os.environ.get("SMTP_USERNAME", "").strip()
                and os.environ.get("SMTP_PASSWORD", "")
                and os.environ.get("LEADS_EMAIL_TO", "").strip())


# ---------------------------------------------------------------------------
# Compose helpers (pure)
# ---------------------------------------------------------------------------

def _day_label(day: date) -> str:
    return f"{day:%a, %b} {day.day}"


def _priority_of(lead: dict):
    """Lead priority bucket. Reads "priority", falls back to legacy "tier"."""
    for key in ("priority", "tier"):
        value = lead.get(key)
        if value in ("A", "B", "C"):
            return value
    return None


def _markets_sorted(leads: list[dict]) -> list[tuple[str, int]]:
    """(metro, count) sorted by count desc, then metro name."""
    counts: dict[str, int] = {}
    for lead in leads:
        metro = lead.get("metro") or lead.get("market") or "Unknown"
        counts[metro] = counts.get(metro, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))


def _source_label(name: str) -> str:
    """Human title from the source registry; the machine name if unknown."""
    try:
        from .sources import all_sources

        titles = {s.name: s.title for s in all_sources()}
    except Exception:  # noqa: BLE001 - labels are cosmetic
        titles = {}
    return titles.get(name) or name


def _source_summary(sources: list[dict]) -> str:
    """One aggregate source-health line. Failed source titles only, never
    error text, counts, or any lead data."""
    failed = [_source_label(str(s.get("source") or "unknown"))
              for s in sources if s.get("status") == "failed"]
    if not failed:
        total = len(sources)
        if total == 0:
            return "No source runs were recorded today."
        unit = "source" if total == 1 else "sources"
        return f"All {total} {unit} checked in normally."
    unit = "source" if len(failed) == 1 else "sources"
    return (f"{len(failed)} {unit} had a problem today: "
            f"{'; '.join(failed)}. "
            "The others ran normally. Ask Claude Code to check it.")


def _contact_lines(leads: list[dict], open_leads: list[dict]) -> list[str]:
    """Counts from the contact lookup, once it has run (else nothing, so the
    email reads as before). Counts only, never a contact."""
    from . import leadsheet

    if not leadsheet.contact_checked(leads + open_leads):
        return []
    reachable = sum(1 for lead in leads if lead.get("contact_status") == "reachable")
    newly = sum(1 for lead in leads if lead.get("newly_reachable"))
    waiting = len(leadsheet.waiting_rows(leads, open_leads))
    by_search = sum(1 for lead in leads if lead.get("contact_status") == "reachable"
                    and lead.get("instagram_by_search"))
    lines = [f"Reachable today (verified contact): {reachable}",
             f"Newly reachable (contact found on a recheck): {newly}",
             f"Waiting on contact: {waiting}, on the {leadsheet.WAITING_TAB} tab."]
    if by_search:  # only once the Instagram web search has found one
        lines.insert(2, f"Instagram found by search: {by_search}")
    return lines


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------

def compose(data: dict, day: date, *, sender: str, recipients: list[str]) -> EmailMessage:
    """Build the short daily email. Pure apart from the XLSX builder call:
    no database, no network. The body holds aggregates only."""
    from . import leadsheet  # lazy: keeps cli -> notify -> leadsheet one-way

    leads = list(data.get("leads") or [])
    sources = list(data.get("sources") or [])
    label = _day_label(day)
    total = len(leads)
    count_a = sum(1 for lead in leads if _priority_of(lead) == "A")
    count_b = sum(1 for lead in leads if _priority_of(lead) == "B")
    count_c = sum(1 for lead in leads if _priority_of(lead) == "C")
    count_hot = sum(1 for lead in leads if lead.get("hot"))
    count_existing = sum(1 for lead in leads if leadsheet.is_existing(lead))
    existing_line = (f"Existing venues (new owner or adding a permit): {count_existing}, "
                     "on the Existing venues tab." if count_existing else None)
    contact_lines = _contact_lines(leads, list(data.get("open_leads") or []))
    failed = sum(1 for s in sources if s.get("status") == "failed")

    if total == 0:
        subject = f"Nightlife leads for {label}: no new leads"
    else:
        subject = f"Nightlife leads for {label}: {total} new ({count_a} A"
        subject += f", {count_hot} hot)" if count_hot else ")"
    if failed == 1:
        subject += " (1 source needs attention)"
    elif failed > 1:
        subject += f" ({failed} sources need attention)"

    source_line = _source_summary(sources)

    # ---- plain text ----
    if total == 0:
        text_lines = ["No new leads today.", "", source_line, "", FOOTER]
    else:
        market_text = ", ".join(f"{name} {n}" for name, n in _markets_sorted(leads))
        text_lines = [
            f"{total} new nightlife leads today. They are in the attached spreadsheet.",
            "",
            f"Hot (best fit, call first): {count_hot}",
            f"Nightclubs, lounges and ticketed venues (A): {count_a}",
            f"Bars and event venues (B): {count_b}",
            f"Restaurants (C): {count_c}",
            *([existing_line] if existing_line else []),
            *contact_lines,
            "",
            f"By market: {market_text}",
            "",
            source_line,
            "",
            FOOTER,
        ]
    plain = "\n".join(text_lines)

    # ---- HTML (same content, inline styles, no links or external resources) ----
    e = html.escape
    h = ['<html><body style="font-family:Arial,Helvetica,sans-serif;'
         'font-size:14px;line-height:1.5;color:#222;max-width:640px;">']
    if total == 0:
        h.append(f"<p>{e('No new leads today.')}</p>")
    else:
        h.append(f"<p>{e(text_lines[0])}</p>")
        h.append(f"<p>{e(f'Hot (best fit, call first): {count_hot}')}<br>"
                 f"{e(f'Nightclubs, lounges and ticketed venues (A): {count_a}')}<br>"
                 f"{e(f'Bars and event venues (B): {count_b}')}<br>"
                 f"{e(f'Restaurants (C): {count_c}')}"
                 + (f"<br>{e(existing_line)}" if existing_line else "")
                 + "".join(f"<br>{e(line)}" for line in contact_lines) + "</p>")
        h.append(f"<p>{e(f'By market: {market_text}')}</p>")
    h.append(f"<p>{e(source_line)}</p>")
    h.append(f"<p>{e(FOOTER)}</p>")
    h.append("</body></html>")
    body_html = "".join(h)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(plain)
    msg.add_alternative(body_html, subtype="html")
    if total:
        payload = leadsheet.build_workbook(leads, data.get("open_leads"))
        maintype, _, subtype = leadsheet.XLSX_MIME.partition("/")
        if not maintype or not subtype:
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(payload, maintype=maintype, subtype=subtype,
                           filename=f"nightlife-leads-{day.isoformat()}.xlsx")
    return msg


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def send(msg: EmailMessage) -> None:
    """Send a composed message using SMTP settings from the environment.

    Port 465 uses SMTP_SSL, anything else uses STARTTLS. Raises NotifyError
    whose message carries only the error type name, never values.
    """
    host = os.environ.get("SMTP_HOST", "").strip()
    port_raw = os.environ.get("SMTP_PORT", "").strip() or "587"
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    try:
        if not host:
            raise NotifyError("email not configured (SMTP_HOST)")
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise NotifyError(f"email failed ({type(exc).__name__})") from exc
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=60)
        else:
            server = smtplib.SMTP(host, port, timeout=60)
        with server:
            if port != 465:
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(msg)
    except NotifyError:
        raise
    except Exception as exc:  # noqa: BLE001 - report type only, values stay private
        raise NotifyError(f"email failed ({type(exc).__name__})") from exc


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def write_preview(msg: EmailMessage, out_dir) -> list:
    """Write preview.html, preview.txt, the attachment file (if any) and
    message.eml into out_dir. Returns the list of written paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    plain_part = msg.get_body(preferencelist=("plain",))
    html_part = msg.get_body(preferencelist=("html",))
    attachments = list(msg.iter_attachments())

    preview_txt = out / "preview.txt"
    preview_txt.write_text(plain_part.get_content() if plain_part else "", encoding="utf-8")
    preview_html = out / "preview.html"
    preview_html.write_text(html_part.get_content() if html_part else "", encoding="utf-8")

    written = [preview_html, preview_txt]
    for part in attachments:
        name = part.get_filename() or "attachment.bin"
        target = out / Path(name).name
        payload = part.get_content()
        if isinstance(payload, str):
            target.write_text(payload, encoding="utf-8")
        else:
            target.write_bytes(bytes(payload))
        written.append(target)
    eml = out / "message.eml"
    eml.write_bytes(msg.as_bytes())
    written.append(eml)
    return written
