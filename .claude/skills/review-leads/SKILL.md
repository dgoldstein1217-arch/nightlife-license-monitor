---
name: review-leads
description: Mark leads as approved, rejected, contacted, replied, won, wrong contact, snoozed or back to new, with an optional note. Use when the owner says "mark these as ...", "I called this one", "I DMed them", "they replied", "we won it", "wrong number", "that Instagram is not them", "not interested", or "reopen".
---

# Record the owner's review decisions

1. Load the database (skill `connect-database`).
2. Get the record ids from the `Lead ID` column of the spreadsheet or of
   the Today's plan tab (space-separated). One venue can have several ids; update all of them.
   If the owner names a venue instead, find its ids:

   ```sql
   SELECT record_ids, legal_name, dba, address, queue_date
   FROM daily_leads
   WHERE dba ILIKE '%name%' OR legal_name ILIKE '%name%'
   ORDER BY queue_date DESC LIMIT 10;
   ```

   If several venues match, ask which one.
3. Update:

   ```bash
   uv run licmon review 1234 1235 --status approved --note "call next week"
   ```

   Statuses: `new` (reopen), `approved`, `rejected`, `contacted`,
   `replied`, `won`, `wrong_contact`, `snoozed`. `--note` is optional.
   Without it the old note stays.

   | Owner says | Status |
   |---|---|
   | "I DMed / called / emailed them" | `contacted` |
   | "they wrote back" | `replied` |
   | "they signed" / "we won it" | `won` |
   | "wrong number", "that Instagram is not them", "email bounced" | `wrong_contact` |
   | "not interested", "not a fit" | `rejected` |
   | "later" | `snoozed` |

4. It prints `updated N record(s)`. N should equal the number of ids. If it
   says 0, the ids were wrong: look them up again. For contacted, replied,
   won and wrong_contact it also says how many venues were logged.

What each outcome does:

- `contacted`, `replied`, `won` are logged with the best way to reach the
  venue at that moment (the method the plan suggested), so the email and
  the How scoring works tab can show "Outreach results, last 30 days" by
  method. These venues (and rejected or snoozed ones) stay off the daily
  plan.
- `wrong_contact` marks the contact the plan showed as bad. It is never
  suggested again, the venue moves to the Waiting on contact tab, and the
  next daily run looks it up again right away (the 120-day clock
  restarts). If another way to reach it turns up, it comes back on the
  plan as Newly reachable. The lead stays open.
- The team's Attio list Status also comes back each day: Contacted there
  becomes `contacted` here and Not a fit becomes `rejected`, never moving a
  lead backwards (a replied or won lead stays as it is).

Marking "contacted" only records what the owner did. This project never
contacts anyone.
