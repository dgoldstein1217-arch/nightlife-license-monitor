# Nightlife liquor-license lead monitor

Collects public liquor-license application records every day from official
sources, keeps the ones that look like new or changing bars, clubs, lounges,
ticketed venues (comedy, live music, sports, theaters, event venues),
restaurants and taprooms in major nightlife metros, and puts them in a review
queue. It emails the owner the day's attack plan (every new venue he can
reach, why, how, and a ready opener) and the full list. It never contacts a
business: the owner sends every message himself. See
`nightlife_liquor_license_monitor_prd.md`.

Owner: start with `ONBOARDING.md`. Coding agents: `AGENTS.md` is the operating
guide (`CLAUDE.md` imports it) and `.claude/skills/` holds a step-by-step
skill for each routine job.

**This repository is public. Lead data never goes in it.** Records, raw
snapshots, scores and review decisions live only in a private Postgres
database reached through the `DATABASE_URL` secret. Workflow logs print counts
per source, never names or addresses. The daily email body names the plan's
venues with their contact and opener (owner approved). It goes only to
`LEADS_EMAIL_TO`, so an email preview holds lead data and is always written
outside the repo.

## Sources (one connector each, `src/licmon/sources/`)

| Source name | Official data | Kind |
|---|---|---|
| `ny_sla_pending` | NY SLA "Current SLA Pending Licenses", data.ny.gov `f8i8-k2gm` | full pending list, statewide |
| `tx_tabc_pending` | TABC "Pending Original ... Application(s)", data.texas.gov `mxm5-tdpj` | full pending list, statewide |
| `chicago_bacp_pending` | Chicago BACP "Liquor and Public Place of Amusement Applications" notice lists (liquor + amusement pages) | rolling ~6 weeks, earliest Chicago signal |
| `chicago_bacp_liquor` | Chicago "Business Licenses", data.cityofchicago.org `r5kz-chrr`, liquor/amusement codes, non-renewals | rolling 180 days |
| `wa_lcb_actions` | WSLCB "New License Applications, Approvals and Discontinuances", statewide report | rolling 30 days |
| `ca_abc_applications` | CA ABC Daily Data Export (CSV zip), application rows only | full list, statewide |
| `fl_abt_licenses` | FL DBPR ABT retail alcoholic beverage licensee extract `bd4006lic.csv` | full licensee list, statewide; leads are **newly issued** licenses (Florida publishes no pending list) |

### Venue history, per source

Each qualified lead is labeled **New venue**, **New owner**, **Adding a
permit** or **Unknown** by comparing it with the state's own list of
existing licenses (`src/licmon/history.py`, `Source.venue_history`):

| Source | How it checks |
|---|---|
| `tx_tabc_pending` | TABC License Information, data.texas.gov `7hf9-qc9f`: licenses at the premises, owner id `master_file_id`; subordinate permits whose `primary_license_id` is an existing license |
| `ny_sla_pending` | SLA Active Licenses `9s3h-dpkz` and Inactive Licenses `6dg3-2z7i` on data.ny.gov, by legal name |
| `chicago_bacp_liquor` | expansion and change-of-activity filings are Adding a permit; others are checked against `r5kz-chrr` itself, owner id = account number |
| `chicago_bacp_pending` | `r5kz-chrr` by city and street (the list has no ZIP), owner id = the account in the ownership link |
| `ca_abc_applications` | the export's own `LIC` rows at the premises |
| `fl_abt_licenses` | the extract's own other rows at the premises; a license listed twice under two owners is a transfer (New owner) |
| `wa_lcb_actions` | its Application Type only (ASSUMPTION = New owner, ADDED/CHANGE OF CLASS/IN LIEU = Adding a permit); everything else Unknown, because WA publishes no full list of existing retail licenses |

Rules: a prior license counts if it is current or ended within two years.
Same owner (id, or legal name without LLC/INC/CORP and punctuation; trade
names are never compared) with a current or recent license there is Adding
a permit; another owner is New owner; nothing is New venue. The same owner's
licenses issued after the filing are the venue's own and do not count. The
address must have the same house number, street, ZIP (or city) and suite;
floors are ignored. Only the label, a count of prior licenses and the
earliest prior issue date are stored, never other businesses' names.

The California HTML daily report is behind bot protection, so it is not used.
The official daily export carries the same applications. Per-record CA links
point at ABC's public license lookup page for a human to open.

## How a run works

1. `licmon run` fetches each source (retries with backoff, one polite client).
2. Raw bytes are saved gzip'd in `raw_snapshots` before any parsing, deduped
   by SHA-256. Bodies older than `RAW_RETENTION_DAYS` (default 14) are dropped,
   hashes and metadata kept; the latest successful snapshot per source is
   always kept.
3. Records are normalized to one shape and upserted by
   `(source, source_record_id)`. First seen, last seen and last changed are
   tracked. A change in status, name, DBA, address, license type, application
   type or application date is a *material change* and is recorded with the
   before/after values in `record_events`.
4. Pending-list sources mark records that drop off the list as removed.
   A parse below a source's `min_records` floor fails that source instead of
   marking everything removed.
5. `qualify.py` applies deterministic rules: target metro, license category,
   application type (new / relocation / ownership change beat renewals),
   name keywords, exclusion words. Each lead gets a score, a tier (A/B/C
   venue kind, defined in AGENTS.md) and a plain reason. Every record also
   gets a stage (Licensed, Approved, In review, Received) from its source's
   own status wording, and qualified leads get a 0 to 100 lead score and a
   Hot label (tier A, not adult, score 75 or more). A is nightclubs, lounges
   and ticketed venues (comedy, live music, sports, theaters, event venues);
   adult venues are marked but never Hot. The points table is in AGENTS.md.
6. Venue history is looked up for the qualified new and changed records
   (a few hundred a day, batched queries). New owner loses 10 points and is
   never Hot; Adding a permit loses 15, is never Hot, never goes to Attio
   and is never named in Slack. A failed lookup logs
   `history FAILED <source> (<ErrorType>)`, leaves those leads Unknown and
   keeps the run green.
7. Qualified new or changed records are queued. A change whose stage moves
   up is marked in `record_events.changes` and shows as "Stage advanced".
   A source's very first run is a
   silent baseline except for applications dated in the last 14 days.
8. A failing source is logged, stored with its traceback in `source_runs`, does
   not stop the others, and makes the workflow exit non-zero (red run + email).
9. `licmon enrich` looks up contact details for the venues Attio would get
   (`src/licmon/contact.py`): Google Places Text Search (New) at the same
   premises (the venue-history address rule), the listing's website
   (Instagram, Facebook, email and phone links), Instagram Business
   Discovery for the handles that website links, and, when the website
   gave no Verified or Likely Instagram, one Brave web search per venue
   (`site:instagram.com "<venue name>" <city>`). The search reads only the
   results (profile URL, title, snippet), never instagram.com; a profile
   whose name matches plus the address or city is Likely at most, name
   only is Unverified ("Possible Instagram" on the Waiting tab). Each way to reach the
   venue gets a 0 to 100 confidence (Verified, Likely, Unverified, None)
   and the venue a suggested outreach method; the points and rules are in
   AGENTS.md. Venues with nothing Verified or Likely wait and are checked
   again weekly for 120 days. Today's leads go first, then due rechecks,
   at most `ENRICH_DAILY_CAP` (150) a day. Results stay in
   `contact_checks`; the log is counts only, and a failed lookup logs
   `enrich FAILED <lookup> failed (<status>)` and keeps the run green. It
   skips itself without `GOOGLE_PLACES_API_KEY`, and makes no web search
   without `BRAVE_SEARCH_API_KEY` (`instagram search off` in the log). It
   never contacts anyone and never searches for a person. The same calls
   also record, with no extra lookups: the booking, ticketing or POS
   platforms the home page links or embeds (Speakeasy itself means Already
   on Speakeasy), opening-soon signs ("coming soon" and the like on the
   site, Instagram bio or search snippet, or a same-name Google listing
   with no reviews or not open yet), and Google's venue type, which can
   move the tier shown on the sheet, the plan and Attio (a bar Google calls
   a nightclub shows as A, and a lounge name Google calls a restaurant shows as
   B). A contact marked `wrong_contact` is never offered again and the
   venue is looked up again on the next run.
10. `licmon attio-pull` (read-only in Attio) reads the team's Status of every
   License Leads entry and, for today's plan venues, a same-name Target's
   status. Contacted and Not a fit move leads forward to contacted and
   rejected (never back), and the plan leaves out venues the team already
   works. Skips itself without `ATTIO_API_KEY`. Counts only in the log.
11. `licmon email` sends the owner the day's `daily_leads`: counts by metro and
   priority, contact and outreach-result counts, then today's attack plan
   (`src/licmon/outreach.py`: every reachable venue nobody has reached out to yet, no cap, new ones first, with why
   reach out, how, and a copy-ready DM, email or call script in the
   owner's own wording), source health, and an Excel file of every lead.
   Addresses, filing phones and record links stay in the file. It sends on
   empty days too (heartbeat) and skips itself when the SMTP secrets are
   not set. `licmon email --preview DIR` writes the message to files
   instead of sending (outside the repo: it holds lead data).
12. `licmon attio-sync --write` adds the day's Hot and A venues, and B venues
   scoring `ATTIO_MIN_B_SCORE` (60) or more, never adult ones or ones only
   adding a permit (and, once contacts are looked up, only reachable ones,
   with their verified phone, Instagram and website), to the Attio
   "License Leads" list on the Targets object (reusing a Target with the
   same name, else making a minimal one), and `licmon slack` pings the team's Slack
   channel when there is a new or stage-advanced lead. Both skip themselves
   when their secret is not set and log counts only.

## Review queue

* `daily_leads` view: one row per venue per day (several applications for the
  same premises are merged).
* `review_queue` view: one row per application.
* `records.review_status`: `new`, `approved`, `rejected`, `contacted`,
  `snoozed`, `replied`, `won`, `wrong_contact`. Contacted, replied, won and
  wrong_contact are also logged in `outreach_outcomes` with the best way to
  reach at that moment. The email and How scoring works count them for the
  last 30 days. A `wrong_contact` lead stays open and is looked up again.

On your own machine, with `DATABASE_URL` set:

```bash
uv run licmon status                       # last run per source, queue sizes
uv run licmon export --out today.xlsx      # today's leads (UTC date), clean sheet
uv run licmon export --all --open --out open.xlsx
uv run licmon review 123 456 --status approved --note "call next week"
uv run licmon review 123 --status replied        # or contacted, won, wrong_contact
uv run licmon email --preview ~/Desktop/email-preview   # see the daily email
uv run licmon requalify                    # after editing qualify.py / metros.py
uv run licmon requalify --history          # also look up venue history again (network)
uv run licmon enrich --cap 5               # look up contacts now (GOOGLE_PLACES_API_KEY)
uv run licmon attio-pull                   # read the team's Attio statuses back (ATTIO_API_KEY)
uv run licmon attio-sync                   # dry run: what would go to Attio (counts)
uv run licmon slack --preview              # print today's Slack message here
```

Never run `export` inside GitHub Actions: its logs are public.

## The spreadsheet

`licmon export` builds one clean row per venue per day from the review
queue: several applications for the same premises are merged into one lead,
and the Lead ID cell lists every record id. A file name ending in `.xlsx`
writes the Excel workbook, anything else writes the New sheet as CSV.

Tabs, each sorted by score, highest first:

* **Today's plan** (first, once the contact lookup has run): the same
  venues as the email's plan, in plan order, with Why reach out, Best way
  to reach, Contact, Confidence, Second best way, the Opener, Contact
  person, Current platform, Opening soon and Lead ID. "No new reachable
  venues today." when there are none.
* **New**: the leads asked for (today by default, or `--date` / `--all`)
  whose venue history is New venue or Unknown.
* **Existing venues**: the same day's New owner and Adding a permit leads,
  with a line saying these venues have been open before.
* **All open**: every lead not yet reviewed, from all days.
* **One tab per state** that has open leads (from the data, not a fixed list).
* **Waiting on contact** (once the contact lookup has run): eligible venues
  with no verified contact yet, what was found (unverified), when they were
  last checked and are checked next, and the search links. Gave up venues
  (120 days) are listed after the waiting ones.
* **How scoring works**: the points table and what each label means (and,
  once the lookup has run, the contact confidence points and outreach rules).

Columns: Priority, Hot, Score, Business name, What's new (New filing, Stage
advanced or Details changed), Company / owner, Business type, Filing, Venue
history, Stage,
Filed on, Phone, Owner / applicant names, Contact person, Person on
LinkedIn, Person on Instagram, Person on Facebook, Address, City, State,
ZIP, Market, Mailing address, License applied for, Map, Google, Instagram,
Official record, Lead ID. The All open and state tabs show Queued on in
place of What's new.

Once the contact lookup has run, the lead tabs (New, Existing venues, All
open, state tabs) leave out venues it checked without finding verified
contact, put Newly reachable venues first, and add Newly reachable, Best way
to reach, Contact (the handle, phone or email as a link), Confidence (label
and score), Why we trust it and Second best way right after the name, plus
a Facebook column after Instagram, plus Current platform, Opening soon,
Google says (Google's type, and the tier change it caused) and Speakeasy /
Attio (Already on Speakeasy, In Attio: <status>). Priority shows the tier
after Google's type. Google and Instagram then link to the
verified website and profile; Phone is labeled "Filing phone (may be a
lawyer)". Venues it never checked (C leads, weak B leads) keep the old
layout's search links. Until it has run, the tabs and venues are exactly as
before.

The official records publish some contact details: Washington a phone
number and applicant names, California and Florida a mailing address.
Contact person is the first named person on the filing; its LinkedIn,
Instagram and Facebook columns are plain Google searches the owner opens by
hand. People are never looked up automatically, and a person never counts
toward contact confidence.

## The daily email

After collecting, the workflow runs `licmon email`: the counts (new leads by
priority and market, how many are Hot, contact and outreach-result counts),
then today's attack plan, then whether every source ran, with the workbook
attached. The plan names each venue with why reach out, how and a ready
opener. It goes only to `LEADS_EMAIL_TO`, the owner's own address, and the
log says counts only. Opener wording lives in `src/licmon/outreach.py`. It
sends even on days with no leads (no attachment then), so a missing email
means something is wrong. It skips itself when the email settings are
missing and the run stays green. Settings and troubleshooting live in
AGENTS.md.

## Repo layout

```text
src/licmon/          pipeline, qualification, stage, venue history, contact lookup, outreach plan, outcomes, spreadsheet, email, Attio, Slack, CLI
src/licmon/sources/  one connector per official source
.github/workflows/  daily-collect (daily.yml), tests (ci.yml), probe (probe.yml)
tests/               deterministic tests with synthetic fixtures only
scripts/             package_for_client.sh builds the handover zip
.claude/skills/      step-by-step recipe per routine job (see AGENTS.md)
```

## Setup

1. Create a private Postgres database (any provider; free tiers are enough).
2. In the GitHub repo: Settings → Environments → `production` → add secret
   `DATABASE_URL`. Optional: `SOCRATA_APP_TOKEN` (raises open-data rate limits).
3. Email (optional): secrets `SMTP_USERNAME`, `SMTP_PASSWORD` (a Gmail app
   password), `LEADS_EMAIL_TO`, optional `LEADS_EMAIL_FROM`; variables
   `SMTP_HOST` / `SMTP_PORT` default to `smtp.gmail.com` / `587`.
   Contact lookup (optional): secret `GOOGLE_PLACES_API_KEY`, optional
   `IG_GRAPH_ACCESS_TOKEN`, `IG_BUSINESS_ACCOUNT_ID` and
   `BRAVE_SEARCH_API_KEY` (Instagram by web search); variable
   `ENRICH_DAILY_CAP` (150). See skill `contact-lookup`.
   Attio and Slack (optional): secrets `ATTIO_API_KEY`, `SLACK_WEBHOOK_URL`;
   variables `ATTIO_DAILY_CAP` (50), `ATTIO_MIN_B_SCORE` (60), `ATTIO_LEADS_URL`,
   `HOT_MIN_SCORE` (75). Run
   `licmon attio-setup --write` once first (see AGENTS.md).
   Openers (optional): variables `OUTREACH_PROOF_CLUB_LOUNGE`,
   `OUTREACH_PROOF_TICKETED`, `OUTREACH_PROOF_BAR`,
   `OUTREACH_PROOF_RESTAURANT` add one proof sentence per venue kind.
4. Actions → `daily-collect` → Run workflow, once, to take the baseline.
5. It then runs daily at 15:30 UTC.

GitHub pauses scheduled workflows in public repos after 60 days without a
commit. The workflow's `keepalive` job pushes an empty commit after 45 quiet
days to prevent that. If it happens anyway, re-enable it from the Actions tab.

To hand the project over as a zip: `scripts/package_for_client.sh` (committed
files only, secrets and local state excluded).

## Development

```bash
uv sync
docker run -d --rm --name licmon-test-pg -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=licmon_test -p 127.0.0.1:55432:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/licmon_test uv run pytest -q
uv run licmon probe          # live fetch + parse, no database, counts only
```

Tests use a throwaway Postgres in Docker (Docker Desktop on a Mac) plus
synthetic fixtures only. Never use the real database or commit real records.

### Adding a source

Write `src/licmon/sources/<name>.py` with a `Source` subclass (`fetch` returns
raw snapshots untouched, `parse` yields `Record`s with a `category`, `stage`
maps the source's status wording, `nightlife_license` names its nightlife
license types, `venue_history` checks the state's list of existing licenses
or returns nothing for Unknown), register it in `sources/__init__.py`, add metro counties/cities in `metros.py` if it is a
new state, and add a test with a synthetic fixture. Nothing else changes.
