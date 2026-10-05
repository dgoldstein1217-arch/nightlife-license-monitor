# Getting started (Mac)

This folder is your nightlife lead finder. Every morning it reads public
liquor license filings from official government websites and picks out the
ones that look like new or changing bars, clubs, lounges, ticketed venues, restaurants and
taprooms in big nightlife cities. Then it emails you the list.

It already runs by itself on GitHub every day at 15:30 UTC (8:30 in Los
Angeles, 10:30 in Chicago). You do not have to leave your Mac on. Your leads
are kept in your own private Neon database.

You never need to type code. Claude Code does the technical steps. This page
tells you what to click and what to paste.

## What you need

1. Your Mac (these steps are written for macOS and zsh, the normal Mac
   terminal shell).
2. A GitHub account (free). GitHub is where the daily job runs.
3. Your Neon account (the one this project already uses). Neon holds the
   leads.
4. Your Google work email. The daily email is sent from your own address to
   yourself, so no one else's account is involved.

## Step 1: Open Terminal

1. Press Cmd + Space (this opens Spotlight search).
2. Type `Terminal` and press Enter.
3. A window with a text prompt appears. That is Terminal. You will paste
   commands into it in the next steps.

## Step 2: Install the helper tools

Paste these lines into Terminal one at a time and press Enter after each.
If a tool is already there, Terminal just tells you, and that is fine.

First, check what you already have:

```sh
command -v brew; command -v git; command -v gh; command -v node; command -v uv
```

If the first line printed nothing, you need Homebrew (a free tool that
installs other tools). Install it with the official installer from brew.sh:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Installing Homebrew also brings in Apple's command line tools, which include
git. Then install the rest:

```sh
brew install gh node uv
```

What these do: `gh` talks to GitHub, `node` runs the Neon sign in helper,
and `uv` runs the lead scraper itself.

## Step 3: Install Claude Code

The current official Mac install (from the Claude Code docs site,
code.claude.com) is:

```sh
curl -fsSL https://claude.ai/install.sh | bash
```

Close Terminal and open it again (Step 1), then check it worked:

```sh
claude --version
```

You should see a version number. If Terminal says it cannot find `claude`,
close Terminal, open it again and try once more. If it still fails, run
`npm install -g @anthropic-ai/claude-code` instead.

## Step 4: Accept the project on GitHub

You will get an email from GitHub about a repository transfer called
`nightlife-license-monitor`. Open it and click **Accept**. The project now
belongs to your GitHub account.

## Step 5: Put the folder on your Mac

Pick one of these:

- If the repo is already yours on GitHub, clone it in Terminal:

  ```sh
  gh repo clone <owner>/nightlife-license-monitor ~/nightlife-license-monitor
  ```

  Replace `<owner>` with your GitHub name. If you are not sure, accept the
  transfer first (Step 4) and Claude Code will figure it out with you.

- If you were sent the project as a zip file, double click it in Finder to
  unzip it, then move the folder somewhere you will remember, like your
  Documents folder.

Then move into the folder in Terminal:

```sh
cd ~/nightlife-license-monitor
```

If you unzipped somewhere else, use that location instead. If your folder
has spaces in its name, put quotes around it, like
`cd ~/Documents/"my folder"`.

## Step 6: Start Claude Code in the folder

In Terminal, inside the project folder, type:

```sh
claude
```

Then paste this first prompt:

```
This folder is my liquor-license lead scraper. Read AGENTS.md and ONBOARDING.md first.
I'm not a developer, so explain things simply and do the technical steps for me.
Do the "handover-checklist" skill one step at a time, including the daily email setup,
and tell me each time I need to click, sign in or paste something.
When it's done, show me today's leads as a spreadsheet on my Desktop.
```

Claude Code will ask you to sign in to GitHub and to Neon in your browser.
Each sign in opens a page with a button like **Authorize**. Click it.

## Step 7: Make a Gmail app password for the daily email

Google does not let programs use your normal password. You make a special
16 letter "app password" that only this project uses. Your sending address
and your receiving address are already set on GitHub. This password is the
only email piece left.

1. Go to myaccount.google.com and click **Security**. Make sure
   **2-Step Verification** is turned on. If it is off, turn it on first
   (Google requires it before app passwords work).
2. Go to myaccount.google.com/apppasswords.
3. In **App name**, type `Nightlife leads` and click **Create**.
4. Google shows a 16 letter code. Copy it.
5. Put it into GitHub (not into chat): open your repo page on github.com,
   click **Settings**, then **Environments**, then **production**, then
   **Add environment secret**. Name it `SMTP_PASSWORD`, paste the code
   (spaces are fine), and click **Add secret**.

Never paste this code into chat or email. Anyone who sees it can send mail
as you until you cancel it.

If Google says app passwords are not available, your work account has them
turned off. The fix lives with your Google Workspace admin (probably you):
go to admin.google.com, then Security, then 2-Step Verification, and allow
it for your account. Then try again.

You can cancel the app password at any time on the same Google page. The
daily email then stops until you make a new one.

## Step 8: Check the first email

Claude Code will run the job once. A few minutes later you get an email with
a subject like "Nightlife leads for ...". Check your spam folder the first
time. If it landed there, mark it "Not spam".

## Step 9 (optional): Turn on the contact lookup

This step lets the daily run check whether you can actually reach each good
lead. It looks at the venue's Google listing, its own website and its
Instagram business page, checks they match the address on the license
filing, and tells you the best way to reach out. With the optional Brave
key it also searches the web for the venue's Instagram when the website
does not link one. It never contacts anyone.
Google charges for these lookups after a free monthly amount, so decide
first; Claude Code can explain the cost (skill `contact-lookup`).

You add up to four secrets the same way as `SMTP_PASSWORD` in Step 7
(repo page on github.com, **Settings**, **Environments**, **production**,
**Add environment secret**):

| Name | What it is | Needed? |
|---|---|---|
| `GOOGLE_PLACES_API_KEY` | A Google Maps Platform key with "Places API (New)" turned on | Yes, to turn the lookup on |
| `IG_GRAPH_ACCESS_TOKEN` | An Instagram Graph API token from a Meta app | Optional, makes Instagram checks stronger |
| `IG_BUSINESS_ACCOUNT_ID` | The id of your own Instagram business account | Optional, goes with the token |
| `BRAVE_SEARCH_API_KEY` | A Brave Search API key (brave.com/search/api, Search plan, about $5 per 1,000 searches with $5 of free credit a month) | Optional, finds Instagram accounts the venue's website does not link |

Ask Claude Code: "Help me set up the contact lookup." It walks you through
making each one. Never paste them into chat. Until the Google key is there,
the lookup step skips itself and everything else works as before.

## What the daily email looks like

The message itself is short: how many new leads came in, how many are Hot,
how many are clubs and ticketed venues, bars or restaurants, which cities they are in, and
whether every source ran clean. It holds no business names. The leads are in
the attached Excel file.

The Excel file has tabs along the bottom:

- **New**: today's leads.
- **Waiting on contact** (once the contact lookup is on): good leads we
  cannot reach yet. Each one is checked again every week. When we find a
  way to reach it, it moves back to the New tab marked **Newly reachable**.
- **All open**: every lead you have not marked reviewed yet, from any day.
- **One tab per state** (like NY, TX, IL): the All open list split by state.
- **How scoring works**: what the score and the labels mean.

On every tab the best leads are at the top. Each row is one venue:

- **Priority**: A, B or C (see below).
- **Hot**: says Hot for the A venues most worth calling first.
- **Score**: 0 to 100. Higher is a better fit for ticketing. It adds up the
  kind of venue, a nightlife license (like a Chicago amusement license or
  Texas late hours), how far along the license is, and whether it is a new
  place.
- **What's new** (New tab): New filing, Stage advanced (a filing we already
  had moved a step forward, like from received to approved), or Details
  changed.
- **Stage**: how far along the license is. Received (just filed), In review,
  Approved, or Licensed.
- **Best way to reach**, **Contact**, **Confidence** and **Why we trust it**
  (once the contact lookup is on): for example "Instagram DM", the handle as
  a link, "Verified 90", and "Google listing at the same address, same
  name; the website links this Instagram". Verified and Likely mean you can
  use it. The phone from the filing is labeled "may be a lawyer", because
  it often is.
- **Contact person**: the person named on the filing, when the state
  publishes one, with LinkedIn, Instagram and Facebook search links you
  open yourself.
- Then the business name, company or owner, business type, what was filed
  (new application, change of owner, new location, and so on), filed on
  date, phone and owner names when the state publishes them (Washington
  does), address, market, mailing address (California and Florida publish
  one), license applied for, clickable Map, Google and Instagram search links
  you open by hand to find a phone number or website, the official record
  link, and the Lead ID numbers you use when you tell Claude Code which leads
  to mark reviewed.

Once the contact lookup is on, the email also has **today's plan**: every
new venue you can reach, best first, each with why it is worth reaching
out, how to reach it, and an opener ready to copy (a DM, an email or a
short call script, in your own words). The workbook's first tab, Today's
plan, has the same list. Nothing is sent for you. After you reach out,
tell Claude Code "I contacted <venue>", "they replied", "we won it" or
"wrong contact", and it records it. A wrong contact is never suggested
again, and the venue is looked up again the next day.

Once they are set up, the same run also puts the Hot and A leads, plus the
best B leads (score 60 or more), into a
"License Leads" list in Attio, on your Targets, and posts a short note in your team's Slack
channel when new leads come in. The Slack note shows the counts and a few
Hot venue names with their city. Addresses and phone numbers stay in the
spreadsheet and Attio.

The email comes even on quiet days (with no file attached then), so if a
morning email is missing, something needs a look. To open the file, double
click it. Excel and Apple Numbers both work.

## Every day after that

The email arrives each morning on its own. To work with leads, open Terminal,
go to the folder (`cd ~/nightlife-license-monitor`), start `claude`, and ask
in plain words. For example:

- "Show me today's leads as a spreadsheet."
- "Show me every lead I haven't reviewed yet."
- "Mark leads 1234 and 1235 as contacted." (The numbers are in the
  Lead ID column.)
- "Is the scraper working?"
- "Only show me Houston tier A leads from this week."
- "Pause the scraper." / "Turn it back on."
- "Send the daily email to my partner too."
- "What would go to Attio today?"
- "Set up the Slack ping."
- "Which venues are waiting on contact?"

Priority: **A** is nightclubs, lounges and ticketed venues: comedy clubs,
live music, sports venues like small stadiums and arenas, theaters, and event
venues. **B** is bars and similar places, like taverns, pubs, taprooms,
breweries, karaoke, pool halls, bowling and cinemas. A restaurant with a
lounge is B too. **C** is restaurants. Coffee shops, bakeries and big chains
are left out.

Adult clubs (gentlemen's clubs and the like) stay in the spreadsheet with
"(adult)" after the business type. They are never Hot, never go to Attio and
are never named in Slack. Start with the Hot rows, then the rest of the A rows. If a lead is in the wrong tier, tell Claude Code.
It can adjust the rules.

## Good to know

- Nothing in this project ever contacts a business. The leads are only for
  you to review by hand.
- Florida publishes no pending list, so Florida leads are newly issued
  licenses and arrive a few weeks later than the other states.
- California data has no filing date, so the date shown is the day the
  scraper first saw the record, at most a day late.
- The GitHub project stays public. Lead data is never stored there, only in
  your private database. Keep it public on the free GitHub plan: in a private
  project on the free plan, GitHub stops giving the daily run its saved
  passwords, so the run breaks. A paid GitHub plan (Pro or Team) lifts that.
- The free Neon plan holds a long time of data. If it ever gets close to
  full, the daily run turns red and Claude Code knows what to do.
