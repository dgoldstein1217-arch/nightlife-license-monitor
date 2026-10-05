---
name: email-setup
description: Set up, change, preview or test the daily lead email (Gmail app password, sender, recipients). Use when the owner asks about the daily email, wants to change who gets it, wants to see what it looks like, or the email stopped arriving.
---

# Daily email: set up, preview, test

The daily run calls `licmon email` after collecting. It emails the owner's
own address(es) only, never a business. Ask the owner before any real send
and before changing recipients. Never write the owner's email address into
any repo file (the repo is public). Use placeholders in examples.

What it holds: the counts, then **today's attack plan** (every new venue
the owner can reach: name, city, stage, tier, why reach out, the best way
to reach with the contact and its confidence, and a ready-to-copy opener),
then source health, with the Excel workbook attached. The owner approved
venue names, contacts and openers in the body because it goes only to
`LEADS_EMAIL_TO`. The workflow log stays counts-only (`email sent: N leads
(N tier A), plan N venues`). Opener wording is in
`src/licmon/outreach.py`. Optional `OUTREACH_PROOF_*` variables add one
proof sentence per venue kind (AGENTS.md settings table).

## Current state

The sender (`SMTP_USERNAME`) and the recipient (`LEADS_EMAIL_TO`) are both
the owner's own Google Workspace address and are already set as secrets.
The only piece left is the app password (`SMTP_PASSWORD`), which the owner
adds on GitHub's web page so it never passes through chat or a shell.

## Preview (sends nothing)

```bash
# needs skill connect-database first
uv run licmon email --preview ~/Desktop/email-preview            # today (UTC)
uv run licmon email --preview ~/Desktop/email-preview --date 2026-10-01
open ~/Desktop/email-preview/preview.html
```

This writes `preview.html` (the message body), `preview.txt`, the Excel
attachment and `message.eml` to that folder. The preview holds lead data
(the plan names venues, contacts and openers), so always write it outside
the repo, for example `~/Desktop/email-preview`. The command refuses a
folder inside the repo, and `.gitignore` also ignores `email-preview*/`
folders as a backstop. Delete old previews when done.

## The app password (owner does this in the browser)

The owner makes a 16-letter app password at myaccount.google.com/apppasswords
(needs 2-Step Verification on first; see ONBOARDING.md step 7). If Google
says app passwords are unavailable, the Workspace admin (probably Dylan)
must allow 2-Step Verification in admin.google.com first.

Then the owner adds it on GitHub: repository page → **Settings** →
**Environments** → **production** → **Add environment secret** → Name
`SMTP_PASSWORD` → paste the 16-letter code (spaces are fine) →
**Add secret**. Get the page URL with
`gh repo view --json url -q .url` and append `/settings/environments`.

Check: `gh secret list --env production` shows `DATABASE_URL`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `LEADS_EMAIL_TO` (values are never shown).

Gmail needs no host or port settings (defaults: `smtp.gmail.com`, `587`).
Only for a non-Gmail provider, set
`gh variable set SMTP_HOST --env production --body smtp.example.com` and
`gh variable set SMTP_PORT --env production --body 465` if it only supports
SSL.

## Change who receives it (ask first)

The address is typed at the prompt, never in the command line, so it is not
logged:

```bash
gh secret set LEADS_EMAIL_TO --env production
```

Comma-separated addresses are allowed. To use a different sender address,
do the same with `SMTP_USERNAME` (it must be the account that made the app
password).

## Test send

With the owner's OK, run skill `run-now`. The log's "Email the owner" step
should say `email sent`. Ask the owner to check the inbox and spam folder.

## Troubleshooting

| Log says | Meaning / fix |
|---|---|
| `email skipped (not configured)` | `SMTP_PASSWORD` or `LEADS_EMAIL_TO` missing. Redo the app-password step above. |
| `email failed (SMTPAuthenticationError)` | Wrong or revoked app password, or `SMTP_USERNAME` is not the account that made it. Make a new app password and replace `SMTP_PASSWORD`. |
| `email sent` but nothing arrived | Spam folder; or a typo in `LEADS_EMAIL_TO` (re-set it). |

Stop the email but keep collecting: `gh secret delete LEADS_EMAIL_TO --env production`
(ask first).
