---
name: contact-lookup
description: Set up, run or tune the contact lookup (licmon enrich) that finds and verifies how to reach each good lead through Google Places, the venue's website, Instagram and a web search for the venue's Instagram (Brave). Use when the owner asks whether he can reach a venue, why a venue is on the Waiting on contact tab, what Best way to reach, Confidence or Possible Instagram mean, wants the Google, Instagram or Brave keys set up, or the "Look up venue contacts" step logs FAILED.
---

# Contact lookup: can we reach this venue?

The daily run calls `licmon enrich` after collecting and before the email.
For the venues Attio would get (Hot, A, and B scoring `ATTIO_MIN_B_SCORE`
or more; never adult, never adding a permit) it:

1. Searches Google Places (Text Search, New) for the venue name and address
   and keeps a listing only if its address is the same premises as the
   filing (same house number, street, ZIP or city; floors ignored). This is
   the venue-history rule in `src/licmon/history.py`, with one difference: a
   suite on one side only still matches, because Google often leaves out
   the suite a filing names. Two different suites never match.
2. Opens the listing's website once (home page only) and reads its
   Instagram, Facebook, email and phone links.
3. Asks Instagram Business Discovery about the Instagram handles that
   website links: does the profile link back to the same website, does its
   bio name the address or city, when was the last post.
4. Only when steps 2 and 3 gave no Verified or Likely Instagram: one web
   search (Brave Search API) for `site:instagram.com "<venue name>" <city>`,
   the same name the Google search uses, without LLC or Inc. It decides
   from the search results alone (profile URL, title, snippet). It never
   opens instagram.com, never logs in, and never searches for a person.
   Waiting venues are searched again on their weekly recheck, which
   catches accounts made after the first check.

It scores each way to reach the venue (the table is in AGENTS.md under
"Contact confidence"), picks the best way to reach out, and saves it in the
`contact_checks` table. **It never contacts a business and never looks up
people.** Logs are counts only.

Venues with nothing Verified or Likely go on the spreadsheet's **Waiting on
contact** tab and are checked again every 7 days. When one becomes
reachable it shows on that day's New tab as **Newly reachable**, goes to
Attio and is named (name and city only) in Slack. After 120 days from the
first check it says **Gave up** and is not checked again.

## Set up (ask first: Google charges per lookup)

Hard rule 5: adding a paid service needs the owner's OK. Google Maps
Platform bills Text Search per request after a monthly free amount, and
asking for the phone and website puts each request in a higher price tier.
Check the current price on Google's Places API pricing page with the owner
before turning it on or raising `ENRICH_DAILY_CAP` (default 150 lookups a
day). Instagram Business Discovery has no charge but is rate limited.

### Google Places key (turns the lookup on)

The owner does this in the browser:

1. console.cloud.google.com, pick or make a project, make sure billing is on.
2. **APIs and Services**, **Library**, search **Places API (New)**, **Enable**.
   (The older "Places API" is a different product and will not work.)
3. **APIs and Services**, **Credentials**, **Create credentials**, **API key**.
4. Click the key, **API restrictions**, **Restrict key**, tick only
   **Places API (New)**, **Save**.
5. Put it on GitHub, not in chat: repo page, **Settings**, **Environments**,
   **production**, **Add environment secret**, name `GOOGLE_PLACES_API_KEY`.

### Brave Search key (optional, finds Instagram by web search)

Meta does not give this app access to Instagram search, so without this
key an Instagram is only found when the venue's own website links it.
Brave costs money per search, so this is hard rule 5: the owner's OK first.

The owner does this in the browser:

1. Go to https://brave.com/search/api/ and sign up.
2. Pick the **Search** plan. At the time of writing it is $5 per 1,000
   requests with $5 of free credit each month (about 1,000 free searches a
   month). Check the current price on that page with the owner.
3. In the Brave dashboard, **API Keys**, **Add API key**, copy it.
4. On his Mac, add it to `~/.env` as `BRAVE_SEARCH_API_KEY=...` (hidden
   prompt or editor, never in chat).
5. On GitHub: repo page, **Settings**, **Environments**, **production**,
   **Add environment secret**, name `BRAVE_SEARCH_API_KEY`. Or from the
   Mac without echoing it: `gh secret set BRAVE_SEARCH_API_KEY --env production`
   (it prompts for the value).

Cost check: at most one search per venue per check, so at most
`ENRICH_DAILY_CAP` (150) a day, and in practice fewer (venues whose
website already links an Instagram are never searched). The client waits
1.1 seconds between searches (the plan's rate limit). Without the key the
log says `instagram search off` and nothing else changes.

### Instagram (optional, makes Instagram checks stronger)

Without these, an Instagram handle linked from the venue's verified website
still counts (Verified), but there is no last-post date, so "Instagram DM"
only appears as "Instagram DM (no recent posts seen)".

Business Discovery needs the owner's own Instagram **professional**
(business or creator) account, connected to a Facebook Page, and a Meta app
with a token that may read it. Meta changes these screens and permission
names often, so check Meta's current Instagram Graph API docs for "Business
Discovery" with the owner. Then:

- `IG_GRAPH_ACCESS_TOKEN`: the token. A normal long-lived user token expires
  after about 60 days; a system user token from Meta Business settings does
  not. When it expires the log says `instagram failed (HTTP 400 code 190)`.
- `IG_BUSINESS_ACCOUNT_ID`: the owner's Instagram account id (a long
  number, not the @handle). Meta's Graph API Explorer shows it as
  `instagram_business_account` on the connected Page.

Add both as environment secrets the same way. Never paste either into chat.

Optional variable: `gh variable set ENRICH_DAILY_CAP --env production --body 100`.

## Run it by hand

Needs skill `connect-database` first. Load the key without printing it
(from `~/.env` if the owner keeps it there, else typed at a hidden prompt):

```bash
read -rs GOOGLE_PLACES_API_KEY && export GOOGLE_PLACES_API_KEY
read -rs BRAVE_SEARCH_API_KEY && export BRAVE_SEARCH_API_KEY   # optional
uv run licmon enrich --cap 5      # a small batch first
uv run licmon enrich              # the normal daily amount
```

If the keys are in `~/.env`, load them without printing:
`set -a; source ~/.env; set +a` (then `uv run licmon enrich --cap 5`).

The log says how many were checked, reachable, newly reachable, waiting and
gave up, `instagram on` or `off`, and `instagram search off` or
`instagram search on: searches N, handles found N, likely N` (counts
only; never a name, handle or query). Then make the spreadsheet (skill
`export-leads`) and look at Best way to reach, Confidence and the Waiting
on contact tab. Do not paste contact details into chat unless the owner
asks for a specific venue.

Counts straight from the table:

```sql
SELECT status, confidence_label, count(*) FROM contact_checks GROUP BY 1, 2 ORDER BY 1, 2;
SELECT count(*) FROM contact_checks WHERE status = 'waiting' AND next_check_at <= now();
```

## Why is a venue waiting?

Open the Waiting on contact tab. "What we found (not verified)" lists each
find with its label and score. Common reasons:

- **Nothing found yet**: no Google listing at that address (new venues
  often have none until they open). It is checked again weekly.
- **Google listing at the same address under another name**: probably the
  old business at that address. Stays Unverified until the new name shows
  on Google or the venue's Instagram bio names the address.
- **A different suite**: the filing says "Ste 5" and Google says "Ste 12".
  Two premises. (A suite on one side only does match. A mall's listing at
  the bare address still needs the venue's name to be more than
  Unverified.)
- **Only the filing phone**: it is often a lawyer or expediter, so it never
  makes a venue reachable on its own.
- **Possible Instagram** (columns on the Waiting tab): the web search found
  an account whose name matches but nothing else (no address or city), or
  two accounts that match equally well. The owner opens the link and
  decides. Name plus address, or name plus city, would have been Likely.

The owner can still use the search links on that tab by hand.

## Tune the rules

All in `src/licmon/contact.py`:

| What | Where |
|---|---|
| Points per signal | `POINTS` |
| Label thresholds (Verified 75, Likely 50) | `LABEL_MIN` |
| Cap without proof of the name (49) and for Facebook (74) | `CAP_UNPROVEN`, `CAP_FACEBOOK` |
| Best way to reach, in order | `METHOD_RULES` |
| Recheck every 7 days, give up after 120, "recent" post 60 days | `RECHECK_DAYS`, `GIVE_UP_DAYS`, `RECENT_POST_DAYS` |
| Words ignored when comparing names (Bar, Lounge ...) | `_GENERIC_NAME_WORDS` |
| Web search points: name 35, address or ZIP 20, city or metro 15 | `POINTS` (`ig_search_*`) |
| Web search tops out at Likely (74); two equal matches stay Unverified | `CAP_SEARCH`, `instagram_from_search` |
| How a profile's name or handle must match the venue | `search_name_match` |
| Short city names counted as a city match (ATX, NYC, CHI ...) | `METRO_ALIASES` in `src/licmon/metros.py` |
| Aliases too common to count outside a handle ("la", "sea" ...) | `_HANDLE_ONLY_ALIASES` |
| The search query | `instagram_query` |
| The search provider (Brave; another could replace it) | `BraveSearch`, `web_search` |

Tuning the Instagram search, in plain terms:

- **Too many wrong accounts become Likely** (same name, other venue): raise
  the bar so name plus city is not enough, for example `ig_search_city` 10
  instead of 15 (name plus city is then 45, Unverified), and only name plus
  address stays Likely. The owner chose "name plus city is enough", so ask
  first.
- **A metro's venues use a short name we miss** (like "HTX" or "305"): add
  it to that metro in `METRO_ALIASES`. Two-letter ones and common words go
  in `_HANDLE_ONLY_ALIASES` too, so they only count in a handle
  ("fakebar.la"), never in a bio ("la mejor").
- **Generic words**: a venue called "Fake Lounge" needs "Fake" in the
  profile name or handle, not "Lounge". A name made only of generic words
  ("The Lounge") must match whole. Edit `_GENERIC_NAME_WORDS` with care: it
  also drives the Google listing name check.

After a change: update the points table and the outreach list in AGENTS.md
(the workbook's How scoring works tab reads the code, so it updates
itself), run the tests, commit. Stored results keep their old scores until
the venue is checked again. To recheck the waiting venues now (owner's OK
first, it costs lookups):

```sql
UPDATE contact_checks SET next_check_at = now() WHERE status = 'waiting';
```

Reachable venues are not rechecked.

## Troubleshooting

| Log says | Meaning / fix |
|---|---|
| `enrich skipped (not configured): 0 venues checked` | `GOOGLE_PLACES_API_KEY` missing. Normal until it is set up. |
| `enrich FAILED places failed (HTTP 403) xN` | Key wrong, not allowed to use Places API (New), or billing off. Fix the key in Google Cloud. The run stays green; venues are tried next run. |
| `enrich FAILED places failed (HTTP 429) xN` | Google quota for the day. Lower `ENRICH_DAILY_CAP`. |
| `enrich FAILED instagram failed (HTTP 400 code 190) xN` | Instagram token expired or revoked. Make a new one. Instagram is off for the rest of that run. |
| `instagram off` | `IG_GRAPH_ACCESS_TOKEN` or `IG_BUSINESS_ACCOUNT_ID` missing. Optional. |
| `instagram search off` | `BRAVE_SEARCH_API_KEY` missing. Optional; nothing else changes. |
| `enrich FAILED instagram search failed (HTTP 401) xN` or `(HTTP 403)` | Brave key wrong or revoked. Make a new one in the Brave dashboard and reset the secret. The search stops for that run; the rest goes on. |
| `enrich FAILED instagram search failed (HTTP 402) xN` or `(HTTP 429)` | Out of plan credit, or over the rate limit after retries. Check the Brave dashboard's usage; lower `ENRICH_DAILY_CAP` if needed. |
| `over daily cap N` | More eligible venues than the cap. They are checked on the next runs (unchecked leads from the last 7 days). |
