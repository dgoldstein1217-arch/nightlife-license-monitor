# PRD: Daily Nightlife Liquor-License Lead Monitor

## Overview

Build a cloud-hosted system that automatically collects public liquor-license application records each day and turns them into a reviewable lead list of likely nightlife and hospitality businesses.

The purpose is to identify businesses that appear to be opening, expanding, changing ownership, or adding alcohol service before they become obvious through ordinary directories or search results. The intended lead types include bars, nightclubs, lounges, restaurants, cocktail venues, rooftops, music/event venues, breweries, taprooms, and similar hospitality businesses.

This is a lead-discovery and review system. It is not a generic directory of all alcohol license holders, and it should not automatically contact anyone without a separate approval step.

## Core objective

Every day, the system should:

1. Collect recent or pending liquor-license application records from official public sources.
2. Focus on major US nightlife metro areas.
3. Detect records that are new or materially changed since prior runs.
4. Filter out businesses that are unlikely to be relevant nightlife/hospitality prospects.
5. Produce a clean daily queue that can be evaluated manually and later passed into a CRM or outreach workflow.

The scraper must run automatically in the cloud. It should not require a laptop, a person, or an AI agent to manually initiate the daily process.

## Scope

### Initial data strategy

Prioritize official sources that expose current or recent application data directly through an API, CSV download, structured report, or stable public page.

The initial implementation should begin with sources that are known to have strong public data coverage, including:

- New York pending liquor-license applications.
- Texas pending original/new liquor-license applications.
- Washington liquor-license application actions.
- California daily new-application reports.
- Chicago liquor-license application records.

The system should be designed so additional states, cities, and sources can be added later through separate source connectors.

### Initial metro focus

Prioritize major nightlife markets covered by the above sources. Examples include:

- New York City and surrounding New York jurisdictions.
- Chicago.
- Dallas–Fort Worth.
- Houston.
- Austin.
- San Antonio.
- Los Angeles / Orange County.
- San Francisco Bay Area.
- San Diego.
- Seattle–Tacoma–Bellevue.

The system should pull a statewide source once when practical, then map individual records into target metro areas internally. It should not create duplicate scrapers for each city when a statewide source covers them.

## Functional requirements

### Automated collection

- Run on a daily schedule in the cloud.
- Support manual runs for testing and troubleshooting.
- Use direct official sources whenever possible.
- Prefer APIs and downloadable files over browser automation.
- Use browser automation only when a source has no reasonable API, export, or structured report option.
- Handle temporary failures with reasonable retries.
- Log every run, including success/failure state, run time, source, record count, and errors.

### Data handling

- Save the original source data before modifying it.
- Normalize records from different sources into a common structure.
- Keep a traceable link back to the official source for every record.
- Deduplicate records across daily runs.
- Track when a record was first seen and last seen.
- Detect meaningful changes, including changes to status, business name, DBA, address, license type, or application date.
- Produce a daily view of newly seen and materially changed records.

### Lead qualification

The system should prioritize likely nightlife/hospitality businesses, including:

- Bars, taverns, nightclubs, lounges, and cocktail bars.
- Restaurants with on-premises alcohol service.
- Rooftops, entertainment venues, music venues, and cabarets.
- Breweries, brewpubs, taprooms, distillery tasting rooms, and winery tasting rooms.
- Event venues or hospitality concepts where alcohol service is central.

The system should normally exclude or deprioritize:

- Grocery stores, supermarkets, convenience stores, pharmacies, and gas stations.
- Package/liquor stores unless retail liquor is later made a target segment.
- Wholesalers, distributors, importers, warehouses, and manufacturers without public-facing hospitality operations.
- Airports, stadiums, universities, hospitals, military facilities, and similar non-target locations.
- Routine renewals with no clear sign of a new venue, expansion, ownership change, or new alcohol-service concept.

Use deterministic rules as the primary qualification method. AI may be used later for ambiguous classification or summarization, but it must not be the scraper, source of truth, or sole decision-maker.

### Daily output

Create a daily review queue containing qualified records that are newly found or materially changed.

Each lead should include, where available:

- Business legal name.
- DBA or public-facing venue name.
- Application or source record ID.
- License type and current application status.
- Application/received/submission date.
- Premises address, city, state, ZIP, county, and assigned metro.
- Source name and direct official source URL.
- Date first seen by the system.
- Reason the record was qualified.
- Lead score or priority tier.
- Review status.

The system should provide an exportable CSV and store the results in a persistent database that can later integrate with the CRM.

## Architecture guidelines

The implementation details are intentionally left to the PI agent. The resulting system should follow these principles:

- Use a public GitHub repository for source code and scheduled workflow execution.
- Keep operational data private. Do not commit collected lead records, enrichment data, database dumps, raw outputs, or secrets to the public repository.
- Store real data in a private database/storage layer.
- Use environment secrets for all credentials.
- Use a scheduled cloud workflow so collection runs without manual intervention.
- Keep source adapters modular: one connector per government dataset/report/source.
- Build for observability: a failed source should be visible rather than silently ignored.
- Preserve raw source snapshots and source provenance for auditability and parser debugging.
- Design the schema so new data sources can be added without rewriting the entire system.

A lightweight free-tier cloud stack is preferred for the MVP. Public GitHub repositories can use GitHub-hosted Actions without Actions-minute charges; however, the implementation must not expose runtime data in Actions logs, artifacts, committed files, or generated public reports. GitHub notes that Actions history and logs are visible when a repository is public. [web:210][web:220]

## Security and operational constraints

- Do not store secrets in source code or committed configuration files.
- Do not publish real lead lists, review decisions, contact information, or proprietary scoring outcomes in the public repository.
- Do not bypass CAPTCHAs, authentication, access restrictions, or published rate limits.
- Collect only publicly accessible source data.
- Keep scraping behavior respectful and efficient.
- Do not automatically initiate outreach from newly collected records. Require a separate human approval or CRM workflow step.
- Maintain enough logging and source history to identify when an upstream public source changes or breaks.

## Success criteria

The first version is successful when:

- It runs automatically each day in the cloud.
- It collects from the initial official sources without manual intervention.
- It produces a daily list of new or updated nightlife/hospitality candidates in target metros.
- It retains source provenance and avoids duplicate leads.
- It gives the operator a straightforward queue to evaluate.
- It can expand to additional sources without changing the overall pipeline design.
- It keeps code public while keeping lead data, secrets, and operational outputs private.

## Out of scope for the first version

- Nationwide coverage of all states and municipalities.
- Perfect categorization of every applicant.
- Automatic outreach of any kind: cold email, SMS, social-media messages, calls or campaigns. The system never contacts a business.
- A polished customer-facing application.
- AI agents controlling the collection pipeline.

Contact enrichment is now in scope (added 2026-10): looking up a venue's public phone, website, email, Instagram and Facebook from its Google listing and its own website, verifying them against the filing's address, scoring how sure we are, and suggesting an outreach method for a person to act on. The lookup also runs one web search per venue for its Instagram account (by the venue's name and city), deciding from the search results alone without opening instagram.com. People are never looked up or searched for, and nothing contacts a business.

## Direction to the PI agent

Build the simplest reliable version first. Treat this as a modular daily data pipeline, not as a chatbot or autonomous browsing agent. Start with direct public datasets and build an auditable collection, normalization, deduplication, metro-filtering, and review workflow. Favor simple deterministic code and clear run logs. Keep implementation choices flexible, but make the system easy to operate, inexpensive to run, and straightforward to extend as more states and cities are added.
