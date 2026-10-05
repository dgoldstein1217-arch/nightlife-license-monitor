# Operating guide for the owner's coding agent

You are helping the owner of this project run it. The owner is not a
developer. Talk in plain, short sentences, do the technical work yourself, and
ask before anything risky. The original builder is not maintaining it.

New computer or first day? Walk the owner through `ONBOARDING.md`.
Step-by-step recipes for every routine job are in `.claude/skills/`
(listed at the bottom of this file). Use them.

## What this is

A daily scraper that collects public liquor-license applications from official
government sources and turns them into a lead list of likely new or changing
bars, clubs, lounges, restaurants and taprooms in major nightlife metros.
Read `README.md` for the design and `nightlife_liquor_license_monitor_prd.md`
for the original requirements.

It runs by itself every day at 15:30 UTC on GitHub Actions
(`.github/workflows/daily.yml`, workflow name `daily-collect`). It writes to a
private Neon Postgres database (project `tiny-truth-43995411`, branch
`production`). The same run then looks up and verifies contact details for
the day's Hot, A and strong B venues (`licmon enrich`), reads the team's
statuses back from Attio (`licmon attio-pull`), emails the owner that day's
attack plan (every new venue he can reach, why, how, and a ready opener)
with the spreadsheet attached, sends the venues it can reach to the
"License Leads" list in Attio, and pings the team's Slack channel. Each of
those steps skips itself until its settings exist. Nobody has to start it.

Sources: New York, Texas, Chicago (two lists), Washington and California
publish pending or new applications. Florida publishes no pending list, so its
leads are **newly issued licenses** (`application_type` = `NEW LICENSE`), a few
weeks later than the other states.

## Hard rules

1. **This repo is public, and stays public on GitHub Free** (see handover
   step 5). Never commit,
   print in a workflow, or upload as an artifact any lead data: names,
   addresses, phone numbers, spreadsheet exports, email previews, database dumps,
   `.env*` files or connection strings. Workflow logs must stay counts-only.
   The daily email body may include the attack plan with venue names, why,
   how, contact and opener (the owner approved this). It still goes only to
   `LEADS_EMAIL_TO`, and logs stay counts-only. Because of that, an email
   preview (`licmon email --preview DIR`) holds lead data: always write it
   outside the repo, for example `~/Desktop/email-preview`.
2. **Never contact a business** (email, SMS, calls, social) from this project.
   Leads are for the owner to review by hand. The contact lookup only reads
   Google's listing, the venue's own home page and Instagram's public
   business profile, and runs one web search for the venue's Instagram (by
   the venue's name, never a person's; it reads the search results only and
   never opens instagram.com), and suggests a way to reach out; it never
   sends anything, and it never looks up people. The daily email goes only to
   the owner's own address in `LEADS_EMAIL_TO`. Attio (the owner's CRM) and
   Slack (the team's own channel) are internal too: the sync and the ping
   reach the owner's team only, and nothing in them contacts a business.
3. **Never paste the database password, connection string or email password
   into chat.** Load them into environment variables or GitHub secrets without
   echoing them (see below).
4. Do not bypass CAPTCHAs, logins or bot protection on any source. Do not
   disguise the scraper as a person or a browser. If a source blocks us,
   report it; do not work around it. (California's HTML daily report is
   behind bot protection. The official CSV export is used instead.)
5. Ask the owner before deleting data, changing the database plan, adding paid
   services, changing who receives the leads, or sending a test email.

## Connecting to the database

```bash
# One-time on the owner's Mac: install tools, sign in, link this folder.
brew install gh node uv
npm i -g neon@latest && neon login
neon link --project-id tiny-truth-43995411 --branch production -y
uv sync

# Each session: load the connection string without printing it.
export DATABASE_URL="$(neon cs production --project-id tiny-truth-43995411 --ssl require)"
```

`neon link` also writes `.env.local` (gitignored). Never commit it.

## Everyday tasks

| Owner asks | Do this |
|---|---|
| "Show me today's leads" / "give me a spreadsheet" | `uv run licmon export --out ~/Desktop/leads-$(date -u +%F).xlsx` (Excel, one clean row per venue, today's UTC date; `.csv` also works). Tell the owner where the file is and summarize counts by market and priority. Do not paste the whole list into chat unless asked. |
| "All leads I haven't reviewed" | `uv run licmon export --all --open --out ~/Desktop/open-leads.xlsx` |
| "Leads from a certain day" | `uv run licmon export --date 2026-10-01 --out ...` |
| "Mark these as approved / rejected / contacted / snoozed" | Find the `Lead ID` column in the spreadsheet (the Today's plan tab has it too), then `uv run licmon review <id> <id> --status approved --note "..."`. One venue can have several record ids; update all of them. `--status new` reopens a lead. |
| "I reached out" / "they replied" / "we won it" | `uv run licmon review <id> <id> --status contacted` (or `replied`, `won`). Each is logged with the best way to reach at that moment, for the "Outreach results, last 30 days" counts. Contacted, replied and won venues stay off the plan. Skill `review-leads`. |
| "Mark wrong contact" / "that Instagram is not them" / "wrong number" | `uv run licmon review <id> <id> --status wrong_contact`. That contact is never suggested again, the venue moves to the Waiting tab and the next run looks it up again. If another way to reach it turns up, it comes back as Newly reachable. Skill `review-leads`. |
| "What should I do today?" / "show me today's plan" | The daily email and the workbook's first tab, Today's plan. By hand: preview the email (row below) or export the sheet. |
| "Change what the openers say" | Edit the constants at the top of `src/licmon/outreach.py` (greeting, timing, intro, product lines, ask, sign-off). Keep the `{venue}`, `{first}` and `{platform}` slots, run the tests, commit. No em dashes. An optional proof sentence per venue kind is a variable, not code (settings table under "The daily email"). |
| "One row per application" / "every column" | `--per-record` or `--full` with a `.csv` file name (raw, wide layout for troubleshooting). |
| "Show me the email" / "resend today's email" | Preview: `uv run licmon email --preview ~/Desktop/email-preview` (writes files, sends nothing. The preview holds venue names and openers, so never inside the repo). Send: only after the owner says yes, `uv run licmon email` with the SMTP variables set (skill `email-setup`). Add `--date 2026-10-01` to either for another day. |
| "Nothing came in today?" | An empty list is normal on quiet days. Check `uv run licmon status`: if every source says `success`, it is working. |
| "Is it working?" | `uv run licmon status` (last run per source, queue size by day). Also `gh run list --workflow daily-collect -L 5`. |
| "Run it now" | `gh workflow run daily-collect`, wait ~10 s, then `gh run watch --exit-status $(gh run list --workflow daily-collect -L 1 --json databaseId -q ".[0].databaseId")`. This also sends the email if it is set up. |
| "Pause it" / "turn it back on" | `gh workflow disable daily-collect` / `gh workflow enable daily-collect` |
| "Only show Houston / only tier A" | Export, then filter the file (skill `export-leads`), or query the `daily_leads` view with SQL (`review_queue` is one row per application). |
| "Change who gets the email" | Ask first, then `gh secret set LEADS_EMAIL_TO --env production` (comma-separated addresses, typed at the prompt, not echoed). |
| "Push the leads to Attio" / "what would go to Attio?" | Dry run first: `uv run licmon attio-sync` (counts only, writes nothing). Real write only with the owner's OK: `uv run licmon attio-sync --write`. Add `--date 2026-10-01` for another day. Skill `attio-sync`. |
| "Set up Attio" | Once, skill `attio-sync`: `uv run licmon attio-setup` (dry run), then `--write` with the owner's OK. |
| "Test the Slack ping" / "set up Slack" | Skill `slack-setup`. Preview on this Mac: `uv run licmon slack --preview` (prints, posts nothing). A real post only with the owner's OK. |
| "Can I reach this venue?" / "look up contacts now" | The sheet's Best way to reach, Contact and Confidence columns, and the Waiting on contact tab. By hand: `uv run licmon enrich` with `GOOGLE_PLACES_API_KEY` loaded (skill `contact-lookup`). |
| "Only Hot leads" / "make fewer or more leads Hot" | Hot = tier A (not adult) with a score of 75 or more. Filter the Hot column, or change the threshold without code: `gh variable set HOT_MIN_SCORE --env production --body 80`, then `uv run licmon requalify` with the same `HOT_MIN_SCORE` set locally (skill `tune-tiers`). |

The owner sells event ticketing, table and VIP reservations and POS
(Speakeasy), so the tier is the kind of venue:

- **A** = nightclubs, lounges and ticketed venues (comedy, live music,
  sports, theaters, event venues): club and lounge names, `TICKETED_WORDS`
  (comedy, improv, stadium, arena, ballpark, speedway, amphitheater,
  theater, playhouse, concert, music hall, ballroom, event center, rooftop,
  supper club, cabaret, fairgrounds, convention center and more), or a
  ticketed-venue license (list below). A stadium concessionaire (Levy,
  Aramark, Delaware North, Sodexo Live, Legends, Centerplate, Spectra, Oak
  View Group, ASM Global, Live Nation, AEG) filing for a named stadium,
  arena or venue is A too; they are never dropped as chains.
- **B** = obvious bars and not-quite-ticketed venues: tavern, pub, taproom,
  brewery, karaoke and KTV, billiards, bowling, cinemas. A restaurant name
  never reaches A: "Restaurant & Lounge" or "Sushi & KTV Lounge" is B.
- **C** = restaurants.

Coffee shops, bakeries, dessert shops and national chains are dropped. The
business name decides most of it, because licenses rarely tell a bar from a
restaurant. A Chicago public place of amusement (PPA) license adds points
but does not move a bar to A by itself (bowling alleys and arcades hold
them too). Adult venues (gentlemen's clubs, strip clubs, topless bars) keep
their tier and score but are marked "(adult)" in Business type: never Hot,
never sent to Attio, never named in Slack. Rules live in
`src/licmon/qualify.py`; target metros in `src/licmon/metros.py`.

**Google's venue type can move the tier** (`contact.google_tier`, stored
in `contact_checks.tier_adjusted` at lookup time). It only uses a Google
listing that is at the same address and has the venue's name (another
name may be the old business):

- A **B or C** venue whose Google primary type is a nightclub or ticketed
  venue (`night_club`, `comedy_club`, `live_music_venue`, `concert_hall`,
  `performing_arts_theater`, `event_venue`, `stadium`, `arena`,
  `dance_hall`, but not karaoke, which stays B) shows as **A**, "Google says nightclub".
- An **A** venue that is A only because of a club or lounge word in its
  name (no nightlife license of any kind, no ticketed-venue word, no
  concessionaire: `qualify.a_by_name_only`) shows as **B** when Google's
  primary type is a restaurant, cafe, bakery or other food type, "Google
  says restaurant".

The tier shown is the one used everywhere after the lookup: the
spreadsheet's Priority (with a **Google says** column naming the change
and the filed tier), Hot (a venue moved to B is never Hot, and one moved to A
keeps its score, so it is not made Hot), the plan, Attio eligibility and
Attio's Priority, and Slack. So restaurants stop going to Attio as clubs.
Only venues the lookup checks can move (C and weak B leads are never
looked up). The stored tier in `records` never changes, and `licmon
requalify` does not touch it.

Every lead also gets a **Score** from 0 to 100 (how good a ticketing fit it
is) and a **Stage** (Licensed, Approved, In review, Received):

| Part | Points |
|---|---|
| Venue type | A 45, B 25, C 5 |
| Nightlife license (highest counts) | Chicago PPA 20; late hours (TX `LH`, Chicago Late Hour) 15; CA 48 public premises 15; music venue or concert hall (CA 90, NY) 15; night club or cabaret (NY, WA) 15; theater or performing arts (CA 64/69/71/72, NY legitimate theatre, WA theater or nonprofit arts, FL 11PA) 15; stadium, arena or sports venue (NY athletic/stadium venues, WA sports entertainment facility, FL 12RT pari-mutuel) 15; civic or event center (FL SCX/SCF/EVNT/DEV) 10; FL full-liquor bar (blank-modifier quota license) 10 |
| Stage | Licensed 25, Approved 20, In review 10, Received 5 (Florida: only if issued in the last 60 days) |
| Filing type | new filing or new location 10; change of owner 5 |
| Venue history | New owner -10 (never Hot); Adding a permit -15 (never Hot, never Attio or Slack); New venue and Unknown 0 |

**Hot** = tier A, not adult, with a score of at least `HOT_MIN_SCORE`
(default 75). Hot is a label on top of A/B/C, not a fourth tier. Every
license in the list above except PPA, late hours, public premises and
full-liquor bar also makes an unclear name A (a restaurant, cinema or
bowling name stays B). Each source maps its own
status wording to a stage and names its nightlife licenses
(`Source.stage` and `Source.nightlife_license` in `src/licmon/sources/`);
the points table is shared (`src/licmon/qualify.py`). A filing whose stage
moves up (say Received to Approved) is queued again as "Stage advanced".

**Contact confidence** (`src/licmon/contact.py`, `licmon enrich`): the
venues Attio would get (Hot, A, and B scoring `ATTIO_MIN_B_SCORE` or more;
never adult or adding a permit, the same rule in `attio.eligible`) are
looked up: Google Places Text Search (New) with the name and address, the
listing's website (Instagram, Facebook, email and phone links on its home
page), Instagram Business Discovery for the handles that website links,
and, when the website gave no Verified or Likely Instagram, one web search
(Brave Search API) for `site:instagram.com "<venue name>" <city>`. The
search decides from the results alone (profile URL, title, snippet); it
never opens instagram.com and never searches for a person.
A Google listing counts only when its address is the same premises as the
filing, by the venue-history rule below, except that a suite on one side
only still matches (Google often drops it; two different suites never do). Each way to reach the venue gets
points (a person's name never counts):

| Signal | Points |
|---|---|
| Google listing at the same address as the filing | 35 |
| The listing's name matches the venue name (without it, everything from that listing stays Unverified, at most 49: it may be the old business) | 20 |
| Phone: Google says the place is open | 10 |
| Phone: the venue website shows the same number | 10 |
| Website: the site loads | 10 |
| Email or Instagram linked from the venue website | 20 |
| Facebook page linked from the venue website (cannot be checked by API, so at most 74, Likely) | 10 |
| Instagram profile links back to the same website | 15 |
| Instagram bio names the street address or ZIP (also proves the venue when the listing's name does not match) | 15 |
| Instagram bio names the city or neighborhood | 5 |
| Instagram the API cannot read (a personal account) | -15 |
| Phone from the license filing ("Filing phone (may be a lawyer)"; at most 49, never reachable on its own) | 15 |
| Google says the listing closed for good | everything from it scores 0 |
| Instagram found by web search: the profile's display name or handle has every distinctive word of the venue name (generic words like Bar, Lounge, Club, Kitchen, Grill are skipped while one distinctive word remains; a name of only generic words must match whole). Without this a result never counts | 35 |
| Found by web search: the result's snippet names the street address (house number and street) or ZIP | 20 |
| Found by web search: the city, or a short name for the metro (`METRO_ALIASES` in `metros.py`: ATX, NYC, CHI ...), in the title, snippet or handle | 15 |

An Instagram found by web search is **Likely at most** (74): name and
address, or name and city, is Likely and reachable; name only is
Unverified ("Possible Instagram" on the Waiting tab, for the owner to
check). If two or more different accounts match equally well, all stay
Unverified and up to two are shown. The owner chose "name plus city is
enough": the risk is a different venue with the same name in the same
city. A search find never adds points to the Google or website channels.

Labels: **Verified** 75 to 100, **Likely** 50 to 74, **Unverified** 1 to
49, **None** 0. A venue is **reachable** when one way to reach it is
Verified or Likely. **Best way to reach** is the first rule that fits, and
the next one on a different channel is the second best:

1. Instagram DM: Instagram Verified or Likely, with a post in the last 60 days.
2. Call: the Google phone Verified or Likely, and Google says it is open.
3. Facebook message: a Facebook page linked from the verified website.
4. Email: an email linked from the verified website.
5. Instagram DM (found by search): an Instagram found by web search is
   Likely (name and address, or name and city).
6. Instagram DM (no recent posts seen): Instagram Verified or Likely, no
   recent post seen (or the API could not read it).
7. Website contact form: only the website is Verified or Likely.
8. Call (Google does not say it is open).
9. Otherwise "Wait: no verified contact yet".

Rules 5 and 6 never compete: the search only runs when the website gave no
Verified or Likely Instagram.

The same calls also record, with no extra lookups:

- **Current platform**: booking, ticketing and POS platforms the venue's
  home page links or embeds (links, scripts, iframes): SevenRooms, Tock,
  OpenTable, Resy, Eventbrite, DICE, Posh, Tixr, Ticketmaster, AXS, See
  Tickets, Etix, Tablelist, Discotech, UrVenue, Toast, Square, Clover, Yelp
  Reservations, and Speakeasy itself (`PLATFORMS` in `contact.py`).
  Speakeasy found means **Already on Speakeasy**: flagged on the sheet and
  left out of the plan. "None found on their site" is a greenfield pitch.
- **Opening soon**: the home page text, the Instagram bio or the web
  search snippet says "coming soon", "opening soon", "grand opening",
  "soft open" or "opening (in) <Month>", or a same-name Google listing has
  no reviews yet, or a status other than open or closed for good. "now
  open" means it just opened (`opening_signal`, `OPENING_WORDS`). Only a
  short label is stored, never the page text.
- **Google's venue type** (primary type and types, from the same Places
  request: `userRatingCount`, `primaryType` and `types` are in the field
  mask) and the tier move described under the tiers above.

**Wrong contact**: `licmon review <ids> --status wrong_contact` keeps that
contact in `contact_checks.bad_channels` (never offered again, whatever
the source), sets the venue to waiting with a check due now (the 120-day
clock restarts) and logs it as an outcome. The lead stays open (review
status `wrong_contact` counts as open), so the next run looks for another
way to reach it.

Venues that are not reachable go to the spreadsheet's **Waiting on
contact** tab and are checked again every 7 days. One that becomes
reachable is marked **Newly reachable** on that day's sheet (sorted first),
goes to Attio and is named in Slack. After 120 days from the first check
it says **Gave up** and stays on the Waiting tab, unchecked, until the lead
is reviewed (a reviewed lead is never rechecked). Each run checks today's
eligible leads first, then due rechecks, then the last 7 days' unchecked
leads, at most `ENRICH_DAILY_CAP` (150) a day. Results live only in the
`contact_checks` table. Until `GOOGLE_PLACES_API_KEY` is set the step skips
itself and the sheet, email, Attio and Slack work exactly as before. Until
`BRAVE_SEARCH_API_KEY` is set no web search is made and the lookup works
exactly as before (the log says `instagram search off`). A Likely
search-found Instagram shows as the Contact with "Found by web search:
name and city match" (or address) in Why we trust it, and goes to Attio's
Instagram field; Unverified finds are in the Waiting tab's **Possible
Instagram** columns. The email adds "Instagram found by search: N" once
there is one.

| Name | Kind | Value |
|---|---|---|
| `GOOGLE_PLACES_API_KEY` | secret | Google Places API (New) key; without it `licmon enrich` skips itself |
| `IG_GRAPH_ACCESS_TOKEN` | secret, optional | Instagram Graph API token for Business Discovery; without it Instagram links are scored from the website only |
| `IG_BUSINESS_ACCOUNT_ID` | secret, optional | the owner's Instagram professional account id the lookups go through |
| `BRAVE_SEARCH_API_KEY` | secret, optional | Brave Search API key (Search plan, paid per request: owner's OK first, hard rule 5); without it no web search for Instagram |
| `ENRICH_DAILY_CAP` | variable, optional | most venues looked up per day (API cost), default `150` |

**Venue history** (`src/licmon/history.py`, `Source.venue_history`): many
filings look new but are not. Each qualified lead is compared with the
state's own list of existing licenses at the same premises (same house
number, street, ZIP or city, and suite): **New venue** (nothing there, or
the last license ended over two years ago), **New owner** (a current or
recent license under another company; the legal names are compared, never
trade names), **Adding a permit** (the same company already licensed
there, like a bar adding late hours), **Unknown** (the source cannot
check). Texas uses TABC License Information (`7hf9-qc9f`), New York the SLA
active and inactive lists, Chicago the business-license dataset, California
and Florida their own files; Washington publishes no full license list, so
it uses its own filing type (ASSUMPTION = New owner, change of class =
Adding a permit) and is Unknown otherwise. The spreadsheet's **New** tab
keeps New venue and Unknown; **Existing venues** holds New owner and Adding
a permit. Only the label, a prior-license count and the earliest prior
issue date are stored. After a history rule change, run
`uv run licmon requalify --history` (network, a few minutes).

## The daily email

After collecting, the workflow runs `licmon email`. It sends one message to
`LEADS_EMAIL_TO` (the owner's own address, nobody else): the counts block
(new leads by priority and market, contact counts, the last 30 days'
outreach results), then **today's attack plan**, then whether every source
ran, with the day's leads as an Excel attachment. It sends even on a day
with no leads (no attachment then), so a missing email means something is
wrong. Logs stay counts-only (`email sent: N leads (N tier A), plan N
venues`).

**The plan** (`src/licmon/outreach.py`, once the contact lookup has run)
covers every reachable new venue for the day, no cap: venues on that
day's sheet with a Verified or Likely way to reach them that are a new
filing, a stage advance, or Newly reachable. It leaves out adult venues,
venues only adding a permit, Already on Speakeasy venues, venues the team
already works in Attio (list Status Contacted, Not a fit or Moved to
Targets, or a Target with the same name whose status shows outreach under
way), and venues reviewed as contacted, replied, won, rejected or snoozed.
Order: Newly reachable first, then venues not open yet, then Hot, then
lead score. Each venue shows:

1. Name, city, stage and tier.
2. **Why reach out**: 2 to 4 plain bullets built only from the data:
   timing ("License approved, not open yet: pitch before launch", "Just
   licensed: opening now", "New owner taking over"), the kind of venue and
   what Speakeasy does for it, the platform on their site ("Uses
   SevenRooms: switch pitch" or "No ticketing or reservations found on
   their site: greenfield"), an opening-soon signal, nightlife licenses,
   New venue.
3. **How**: best way to reach, the contact, its confidence, second best way.
4. **Opener**, ready to copy in the format of the best way: a DM
   (Instagram or Facebook), an email with its subject line (email or the
   website's contact form), or a call script. The wording is the owner's
   own, in constants at the top of `outreach.py`: edit there.
   `{first}` is the first name of the contact person when the filing names
   a natural person (never a company: LLC, Inc, Corp, LP, Ltd, Co,
   Company, Group, Holdings, Partners, Trust, Enterprises, digits, or a
   venue word). Otherwise the greeting is "Hey hey," (DM) or "Hey <venue>
   team," (email). The platform line only appears when their site shows a
   competing platform.

With no plan venues the email says "No new reachable venues today." The
workbook's first tab, **Today's plan**, has the same venues with Priority
order, Business name, City, Stage, Why reach out, Best way to reach,
Contact, Confidence, Second best way, Opener, Contact person, Current
platform, Opening soon and Lead ID. Before the contact lookup has ever run
there is no plan section and no plan tab.

The owner's own Google Workspace account sends the email to himself:
`SMTP_USERNAME` and `LEADS_EMAIL_TO` are both his work address (already set as
secrets; never write the address into this public repo), and `SMTP_PASSWORD`
is an app password he makes on his Google account.

The attached workbook has tabs: **Today's plan** (once the contact lookup
has run), **New** (the day's new venues and unknown history), **Existing venues** (new owner or adding a permit), **All open**
(every lead not yet reviewed), one tab per state with open leads, and
**How scoring works**. One row per venue, highest score first; the columns
are listed under "The spreadsheet" in README.md (`src/licmon/leadsheet.py`).
Once the contact lookup has run, the lead tabs show only venues we can
reach (plus any it has not checked, like C leads), with Best way to reach,
Contact, Confidence and Why we trust it after the name; the rest are on a
**Waiting on contact** tab after Existing venues. On the lead tabs Google
and Instagram link to the verified website and profile; the plain search
links stay on the Waiting tab and on unchecked rows. Every tab has a
**Contact person** column: the person named on the filing (Washington
publishes applicants) with LinkedIn, Instagram and Facebook search links the
owner opens by hand. People are never looked up automatically. Texas, New
York, Chicago, California and Florida publish only the legal owner, so the
Contact person fills there only when that owner is a person (a sole
proprietor). Companies are left out. The lead tabs also show **Current
platform**, **Opening soon**, **Google says** and **Speakeasy / Attio**
(Already on Speakeasy, or In Attio: <status>). The email body adds counts:
reachable today, newly reachable, waiting, and once there are any,
"Outreach results, last 30 days" (contacted, replied, won, wrong contact,
then the same by way of reaching out). How scoring works shows those
counts too.

Settings live in the GitHub `production` environment:

| Name | Kind | Value |
|---|---|---|
| `SMTP_USERNAME` | secret | the owner's own sending address (already set; never write it in this repo) |
| `SMTP_PASSWORD` | secret | a Gmail **app password** (16 letters), not the normal password |
| `LEADS_EMAIL_TO` | secret | who receives it (comma-separated) |
| `LEADS_EMAIL_FROM` | secret, optional | defaults to `SMTP_USERNAME` |
| `SMTP_HOST` | variable, optional | default `smtp.gmail.com` |
| `SMTP_PORT` | variable, optional | default `587` (use `465` for SSL-only providers) |
| `OUTREACH_PROOF_CLUB_LOUNGE` | variable, optional | one proof sentence added to openers for clubs and lounges, on its own line after the product line. Unset by default. Real facts only, the owner writes it (never invented) |
| `OUTREACH_PROOF_TICKETED` | variable, optional | the same for ticketed venues (comedy, live music, theaters, sports, event venues) |
| `OUTREACH_PROOF_BAR` | variable, optional | the same for bars, taprooms and breweries |
| `OUTREACH_PROOF_RESTAURANT` | variable, optional | the same for restaurants |

Set one with `gh variable set OUTREACH_PROOF_BAR --env production --body "..."`
(variables are not secret, so no names of clients without their OK).

If any of the settings above is missing, the email step skips itself
and the run stays green. Setup steps are in the `email-setup` skill.

## Attio and Slack

Before the email, the workflow runs `licmon attio-pull`. After it,
`licmon attio-sync --write` and then `licmon slack`. All three skip
themselves (run stays green) until their secret is set, and all log counts
only.

- **Attio pull** (read-only in Attio) reads the team's **Status** of every
  License Leads entry (500 per call) and, for today's plan venues (at most
  `ATTIO_DAILY_CAP`), the status of a Target with the same name (the same
  exact-name match the sync uses). It keeps both in the `attio_status`
  table. Contacted moves the venue's leads to review status contacted and
  Not a fit to rejected, only ever forward (replied, won and rejected leads
  are never set back), and a Contacted pull is logged as an outreach result.
  The plan leaves out Contacted, Not a fit and Moved to Targets entries,
  and Targets whose status shows outreach under way (1st Outreach sent,
  Follow up sent, 3rd Follow up sent, In conversation, Linkedin + email.
  Prespecting and Haven't found contact do not count). Known gap: active
  clients live in Attio's Client object, which this project does not read
  (its name field was never confirmed), so "already a client" comes only
  from Speakeasy on the venue's site and from those statuses. Log line:
  `attio pull: list entries read N (worked by the team N); leads moved to
  contacted N, to rejected N; targets looked up N (outreach under way N)`.

- **Attio** gets the day's Hot and A venues plus B venues with a score of at
  least `ATTIO_MIN_B_SCORE` (default 60). Adult venues never go. Each one is a record in
  the existing **Targets** object, added to a list named "License Leads"
  (api slug `license_leads`) that sits on Targets. All the lead details
  (venue key, priority, score, stage, market, address, owner, phone,
  license, dates, links) live on the list entry, so the Targets object
  itself is never changed. A venue is matched on its Venue key, so it is
  never added twice; one that comes back gets only its stage, score,
  priority and venue history updated. New owner venues go with Venue
  history "New owner"; Adding a permit venues never go. Before its first
  write each day the sync adds the **Venue history** text field if it is
  missing (it came after the list); if the key may not, entries go without
  it and the log says so. If Targets already has a record with the same name
  (ignoring upper and lower case), that record is reused. Otherwise a new
  Target is made with just the name, client type Venue and status
  Prespecting (Attio's own spelling). The team's **Status** on the list
  (New, Moved to Targets, Not a fit, Contacted) starts at New and is never
  overwritten. At most `ATTIO_DAILY_CAP` (default 50) new Targets a day,
  highest score first; the rest stay in the spreadsheet. The list's
  Priority field has Hot, A and B; before its first write each day the sync
  adds any of those that is missing (the live list was made with Hot and A).
  Adding one needs `list_configuration:read-write`. If the daily key lacks it,
  Hot and A still go, B leads wait, and the log says "B leads held back". Fix
  once on the owner's Mac: run `uv run licmon attio-sync --write` with
  `ATTIO_WRITE_API_KEY` loaded (owner's OK first), which adds the B option. The list is
  created once with `licmon attio-setup --write` (skill `attio-sync`). It
  only creates new things and stops if the list exists.
  Once the contact lookup has run, only reachable venues go (the log says
  "held back without verified contact N"; they go the day they become
  reachable), and Phone, Instagram and Google hold the verified phone,
  profile and website instead of the filing phone and search links. The
  sync adds the list fields **Best way to reach**, **Contact confidence**,
  **Email** and **Facebook** the same way as Venue history.
- **Slack** posts only when the day has a new filing or a stage-advanced
  lead: counts (Hot, A, B, C), up to five Hot venue names with city and
  stage (never an adult venue or one only adding a permit; "New owner" is
  added after the stage), how many went to Attio and how many of those
  are B, and a link to the Attio list. No addresses, phones or owners. A
  venue that became reachable after waiting is news too: "Newly reachable"
  lists up to five names with city, never the contact itself. Skill `slack-setup`.

| Name | Kind | Value |
|---|---|---|
| `ATTIO_API_KEY` | secret | Attio API key; needs `list_entry:read-write`, `list_configuration:read`, `record_permission:read-write` and `object_configuration:read` (plus `list_configuration:read-write` for `attio-setup`) |
| `ATTIO_DAILY_CAP` | variable, optional | most new Targets Attio gets per day, default `50` |
| `ATTIO_MIN_B_SCORE` | variable, optional | lowest score a tier B venue needs to go to Attio, default `60` |
| `ATTIO_LEADS_URL` | variable, optional | the License Leads page URL copied from the Attio browser tab, linked from Slack |
| `SLACK_WEBHOOK_URL` | secret | Slack incoming webhook URL (the channel is picked when it is made) |
| `HOT_MIN_SCORE` | variable, optional | score a tier A lead (not adult) needs to be Hot, default `75` |

Changing `HOT_MIN_SCORE` affects records scored from the next run on; run
`uv run licmon requalify` (with the same value set locally) to re-label
stored records.

## If something breaks

- **Red run / failure email from GitHub.** Open the run log
  (`gh run view <id> --log`). Each source logs one result line; a failed one
  says `FAILED` with the error type (no record data; details stay in the DB):
  `SELECT source, started_at, error_type, error_detail FROM source_runs WHERE status='failed' ORDER BY started_at DESC LIMIT 5;`
  One broken source does not stop the others. See skill `fix-broken-source`.
- **The daily email did not arrive.** Check the run's "Email the owner" step.
  `email failed (SMTPAuthenticationError)` means the app password is wrong or
  was revoked: make a new one and reset `SMTP_PASSWORD`. Check spam once.
- **A source changed its format** (error `SourceSanityError` or a parse error).
  Fetch it with `uv run licmon probe --source <name>`, compare to the parser in
  `src/licmon/sources/<file>.py`, fix it, add or update the test fixture
  (synthetic data only, never real rows), run the tests, commit.
- **STORAGE ALARM in the log.** Neon Free stops saving new data at 512 MB. The
  run turns red at 400 MB. First keep fewer days of raw downloads:
  `gh variable set RAW_RETENTION_DAYS --body 7` (default 14;
  old ones are trimmed on the next run). If that is not enough, tell the owner
  the Neon plan needs an upgrade. Check size with
  `SELECT pg_size_pretty(pg_database_size(current_database()));`
- **Daily runs stopped.** GitHub disables schedules in public repos after 60
  days with no commits. The workflow's `keepalive` job prevents this by pushing
  an empty commit after 45 quiet days. If it happened anyway:
  `gh workflow enable daily-collect`.
- **Attio says "B leads held back".** The list has no B priority option and
  the daily key cannot add one. See "Attio and Slack" above.
- **Attio says "the Venue history field is missing".** Same fix as the B
  option: one `uv run licmon attio-sync --write` with `ATTIO_WRITE_API_KEY`
  loaded (owner's OK first) adds it.
- **`history FAILED <source> (<ErrorType>)` in the log.** That state's
  license list could not be read today. The run stays green and those leads
  say Unknown. If it keeps happening, check the dataset with a plain GET
  and see skill `fix-broken-source`; `uv run licmon requalify --history`
  fills them in once it works again.
- **`enrich FAILED places failed (HTTP 403) xN` in the log.** The Google
  key is wrong, restricted to the wrong API, or billing is off; the lookup
  stops for the day and the run stays green (skill `contact-lookup`).
  `instagram failed (HTTP 400 code 190)` means the Instagram token expired:
  make a new one. `instagram search failed (HTTP 401)` (or 402, 403, or 429
  after retries) means the Brave key is wrong, the plan's credit ran out or
  it hit the rate limit: the web search stops for that run only, everything
  else goes on (skill `contact-lookup`). Those venues are tried again on the
  next run.
- **"Read Attio statuses" step red.** Same messages as the Attio step
  below (it uses the same key, read-only). The email still goes, and the plan
  then uses the statuses from the last good pull.
- **Attio step red.** `attio failed (HTTP 401)` or `(HTTP 403)`: the key is
  wrong or lacks a scope (see "Attio and Slack"). The word after the number
  is Attio's reason, for example `quota_exceeded` (the Attio plan's limit).
  `list license_leads not found`: run `licmon attio-setup` once (skill
  `attio-sync`).
- **Slack step red.** `slack failed (HTTP 403)` or `(HTTP 404)`: the webhook
  was removed; make a new one (skill `slack-setup`).
- **After changing rules** in `src/licmon/qualify.py` or `src/licmon/metros.py`,
  run `uv run licmon requalify` so stored records get the new scores. Past
  daily queues are not rewritten; the change shows from the next run on.

## Handover checklist (do once, right after the repo moves to the owner)

Follow skill `handover-checklist`. In short:

1. `gh secret list --env production` must show `DATABASE_URL`. If it is
   missing, recreate it without echoing:
   `neon cs production --project-id tiny-truth-43995411 --ssl require | gh secret set DATABASE_URL --env production`
2. Make the owner the person who gets failure emails: GitHub sends scheduled
   run failures to whoever last enabled the workflow. Run
   `gh workflow disable daily-collect && gh workflow enable daily-collect` while
   signed in as the owner.
3. Set up the daily email (skill `email-setup`).
4. Run it once (see "Run it now") and confirm every source says `ok` and the
   email arrived.
5. Keep the repo public on GitHub Free. The workflows read their secrets
   from the `production` environment, and GitHub Free only allows
   environments in public repos: in a private repo those secrets stop
   working unless the account is on GitHub Pro or Team. The `keepalive` job
   handles the 60-day schedule rule for public repos. Hard rule 1 keeps the
   public logs free of lead data.
6. Optional, each only with the owner's OK: Attio (skill `attio-sync`: one
   `attio-setup --write`, then the `ATTIO_API_KEY` secret) and the Slack ping
   (skill `slack-setup`). Then run `uv run licmon requalify` once so stored
   records get a stage and score.

## Developing

Tests need a disposable Postgres in Docker (Docker Desktop on the owner's
Mac), never the real database:

```bash
docker run -d --rm --name licmon-test-pg -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=licmon_test -p 127.0.0.1:55432:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/licmon_test uv run pytest -q
```

To add a state or city, follow skill `add-source` (also "Adding a source" in
`README.md`): one new file in `src/licmon/sources/`, register it, add metro
counties, add a synthetic test. CI runs the tests on pushes to `main` and on
pull requests. The manual `probe-sources` workflow checks that GitHub can reach
and parse every source without touching the database.

Optional secret `SOCRATA_APP_TOKEN` (free, from data.ny.gov / data.texas.gov
/ data.cityofchicago.org developer settings) raises open-data rate limits. Not
needed at current volumes.

## Skills (`.claude/skills/<name>/SKILL.md`)

| Skill | Use when the owner asks to |
|---|---|
| `connect-database` | do anything that reads or writes leads (load `DATABASE_URL` safely) |
| `export-leads` | see leads, get a spreadsheet, filter by metro/tier/day |
| `review-leads` | mark leads approved, rejected, contacted, replied, won, wrong contact, snoozed or new |
| `check-health` | know if it is working, or why nothing came in |
| `run-now` | run the collection right now |
| `pause-resume` | pause or restart the daily run |
| `email-setup` | set up, change, preview or test the daily email |
| `attio-sync` | set up the Attio License Leads list, or push leads to Attio |
| `slack-setup` | set up, preview or test the Slack ping |
| `contact-lookup` | set up the contact lookup keys, run `licmon enrich` by hand, or tune the confidence and outreach rules |
| `fix-broken-source` | fix a red run or a source that changed format |
| `add-source` | add a new state or city |
| `tune-tiers` | change what counts as a lead, the tiers, the score weights, Hot, or the target metros |
| `storage-alarm` | handle the STORAGE ALARM / database size |
| `handover-checklist` | finish the one-time setup after the repo transfer |
| `package-for-client` | build the zip to hand this project to someone |
| `neon-postgres` | third-party Neon database reference (connections, branching, SQL) |
