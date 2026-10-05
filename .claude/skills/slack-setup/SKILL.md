---
name: slack-setup
description: Set up, preview or test the Slack ping about new leads (incoming webhook). Use when the owner asks about Slack, wants the ping in a different channel, wants to see the message, or the "Ping Slack" step failed.
---

# Slack ping: set up, preview, test

The daily run calls `licmon slack` after the Attio step. It posts one short
message to the team's own Slack channel, and only when the day has at least
one new filing or stage-advanced lead (the email still covers quiet days).
Ask the owner before any real post.

The message looks like this (synthetic example):

```
New license leads: 14 today (3 Hot, 5 A, 6 B)
Hot: Club X, Houston (Approved) · Lounge Y, Miami (Licensed) · ...
Added to Attio: 8 new, 2 updated (3 B) · Full list in today's email.  <link to License Leads in Attio>
```

A venue the contact lookup could not reach before and now can adds a line
"Newly reachable (contact found on a recheck): Name, City", and counts as
news on its own. Never the handle, phone or email.

At most five Hot names, with city and stage only: no addresses, phones or
owners, and never an adult venue or a venue only adding a permit. A venue
changing hands says "New owner" after its stage. "(3 B)" is how many of the Attio
additions are B leads. The GitHub log says `slack posted: N leads (N hot)` or `skipped`,
never the message.

## Make the webhook (owner does this in the browser)

1. Go to api.slack.com/apps and click **Create New App** → **From scratch**.
   Name it `License leads`, pick the workspace, **Create App**.
2. Click **Incoming Webhooks**, switch it **On**, then
   **Add New Webhook to Workspace**, pick the channel, **Allow**.
3. Copy the webhook URL (starts with `https://hooks.slack.com/services/`).
4. Put it on GitHub, not in chat: repository page → **Settings** →
   **Environments** → **production** → **Add environment secret** → Name
   `SLACK_WEBHOOK_URL` → paste → **Add secret**. Get the page URL with
   `gh repo view --json url -q .url` and append `/settings/environments`.

The URL is a password for that channel. Never paste it into chat, a commit
or a command line.

Optional link to the Attio list in the message:
`gh variable set ATTIO_LEADS_URL --env production --body "<License Leads page URL>"`.

## Preview (posts nothing)

Needs skill `connect-database` first. Prints on this Mac only; never run
`--preview` in GitHub Actions (it refuses to).

```bash
uv run licmon slack --preview                    # today (UTC)
uv run licmon slack --preview --date 2026-10-01
```

If it says `slack skipped (no new or stage-advanced leads)`, that day had no
news and nothing would be posted.

## Test post (ask first)

With the owner's OK, run skill `run-now`. The "Ping Slack" step should say
`slack posted` (or `skipped` on a day with no news). Ask the owner to check
the channel.

## Change the channel

Make a new webhook for the other channel (steps above) and replace the
secret. Delete the old webhook on the app's Incoming Webhooks page.

## Troubleshooting

| Log says | Meaning / fix |
|---|---|
| `slack skipped (not configured)` | `SLACK_WEBHOOK_URL` missing. Make the webhook. |
| `slack skipped (no new or stage-advanced leads)` | Quiet day. Normal. |
| `slack failed (HTTP 403)` / `(HTTP 404)` | The webhook or app was removed. Make a new one. |

Stop the ping but keep everything else: `gh secret delete SLACK_WEBHOOK_URL --env production`
(ask first).
