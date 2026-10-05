"""Slack ping: a short note in the team's channel when new leads come in.

Posts through a Slack incoming webhook (SLACK_WEBHOOK_URL; the channel is
picked when the webhook is made). It posts only when the day has at least one
new filing or stage-advanced lead; the email still covers quiet days.

The message holds counts plus up to five Hot venue names with city and stage.
No addresses, phones, owners or record links, and never the name of an
adult venue or of a venue that is only adding a permit (history.py). A
named venue changing hands says "New owner". A venue the contact lookup
found a verified way to reach after waiting (contact.py) is news too, and
is named the same way: name and city only, never the contact itself. It
goes to the owner's own team channel, never to a business.

The message text never goes to public Actions logs: callers log counts and
"posted" / "skipped" / "failed (<ErrorType>)" only.
"""

from __future__ import annotations

import os

from .history import ADDING_PERMIT, NEW_OWNER
from .leadsheet import NEW_FILING, STAGE_ADVANCED

MAX_HOT_NAMES = 5


class SlackError(RuntimeError):
    """Slack failure with a value-free message (HTTP status or error type)."""


def webhook_url() -> str:
    return os.environ.get("SLACK_WEBHOOK_URL", "").strip()


def configured() -> bool:
    return bool(webhook_url())


def is_news(rows: list[dict]) -> bool:
    """At least one new filing, stage-advanced or newly reachable lead."""
    return any(r.get("whats_new") in (NEW_FILING, STAGE_ADVANCED) or r.get("newly_reachable")
               for r in rows)


def _nameable(row: dict) -> bool:
    """Adult venues and venues only adding a permit are never named."""
    return not row.get("adult") and row.get("venue_history") != ADDING_PERMIT


def _names(rows: list[dict], notes) -> str:
    rows = sorted(rows, key=lambda r: -(r.get("lead_score") or 0))
    names = []
    for r in rows[:MAX_HOT_NAMES]:
        where = ", ".join(p for p in (r.get("business_name"), r.get("city")) if p)
        extra = notes(r)
        names.append(_esc(where) + (f" ({_esc(', '.join(extra))})" if extra else ""))
    more = len(rows) - MAX_HOT_NAMES
    return " · ".join(names) + (f" · and {more} more" if more > 0 else "")


def _esc(text: str) -> str:
    """Slack mrkdwn needs &, < and > escaped."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def compose(rows: list[dict], attio_counts: dict | None = None,
            attio_url: str | None = None) -> str:
    """The message text. Hot is counted on its own; A means A and not Hot."""
    hot = [r for r in rows if r.get("hot")]
    # Adult venues and venues only adding a permit are never named.
    named = [r for r in hot if _nameable(r)]
    buckets = [("Hot", len(hot))] + [
        (tier, sum(1 for r in rows if r.get("priority") == tier and not r.get("hot")))
        for tier in ("A", "B", "C")]
    parts = ", ".join(f"{n} {label}" for label, n in buckets if n)
    lines = [f"New license leads: {len(rows)} today" + (f" ({parts})" if parts else "")]

    if named:
        lines.append("Hot: " + _names(named, lambda r: ([r["stage"]] if r.get("stage") else [])
                                      + ([NEW_OWNER] if r.get("venue_history") == NEW_OWNER
                                         else [])))
    newly = [r for r in rows if r.get("newly_reachable") and _nameable(r)]
    if newly:
        lines.append("Newly reachable (contact found on a recheck): "
                     + _names(newly, lambda r: []))

    tail = []
    if attio_counts:
        added = attio_counts.get("added", attio_counts.get("created", 0)) or 0
        b = attio_counts.get("b") or 0
        tail.append(f"Added to Attio: {added} new, {attio_counts.get('updated', 0) or 0} updated"
                    + (f" ({b} B)" if b else ""))
        over = attio_counts.get("skipped", attio_counts.get("over_cap")) or 0
        if over:
            tail.append(f"{over} more over today's Attio limit, in the spreadsheet")
    tail.append("Full list in today's email.")
    last = " · ".join(tail)
    if attio_url:
        last += f"  <{attio_url}|Open License Leads in Attio>"
    lines.append(last)
    return "\n".join(lines)


def post(text: str, url: str | None = None, session=None) -> None:
    """POST the text to the webhook. Raises SlackError (status/type only)."""
    import requests

    url = url or webhook_url()
    if not url:
        raise SlackError("slack not configured (SLACK_WEBHOOK_URL)")
    try:
        resp = (session or requests).post(url, json={"text": text}, timeout=30)
    except Exception as exc:  # noqa: BLE001 - type only; the URL is a secret
        raise SlackError(f"slack failed ({type(exc).__name__})") from exc
    if resp.status_code >= 400:
        raise SlackError(f"slack failed (HTTP {resp.status_code})")
