-- Idempotent schema. Applied at the start of every run.

CREATE TABLE IF NOT EXISTS runs (
    id            BIGSERIAL PRIMARY KEY,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    trigger       TEXT NOT NULL DEFAULT 'manual',   -- schedule | manual | test
    status        TEXT NOT NULL DEFAULT 'running',  -- running | success | partial | failed
    git_sha       TEXT
);

CREATE TABLE IF NOT EXISTS raw_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    source        TEXT NOT NULL,
    url           TEXT NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL,       -- first time these exact bytes were fetched
    last_fetched_at TIMESTAMPTZ NOT NULL,     -- most recent fetch of the same bytes
    content_type  TEXT,
    sha256        TEXT NOT NULL,
    byte_size     BIGINT NOT NULL,
    body_gz       BYTEA,           -- NULL once pruned by retention
    pruned_at     TIMESTAMPTZ,
    UNIQUE (source, sha256)
);

CREATE TABLE IF NOT EXISTS source_runs (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES runs(id),
    source          TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running',  -- running | success | failed
    is_baseline     BOOLEAN NOT NULL DEFAULT false,
    snapshot_ids    BIGINT[] NOT NULL DEFAULT '{}',
    records_fetched INTEGER,
    records_new     INTEGER,
    records_changed INTEGER,
    records_removed INTEGER,
    records_qualified_queued INTEGER,
    error_type      TEXT,
    error_detail    TEXT          -- full traceback; private DB only, never logged
);
CREATE INDEX IF NOT EXISTS source_runs_source_idx ON source_runs (source, started_at DESC);

CREATE TABLE IF NOT EXISTS records (
    id                  BIGSERIAL PRIMARY KEY,
    source              TEXT NOT NULL,
    source_record_id    TEXT NOT NULL,
    source_url          TEXT NOT NULL,
    legal_name          TEXT,
    dba                 TEXT,
    license_type        TEXT,
    license_description TEXT,
    application_type    TEXT,
    status              TEXT,
    application_date    DATE,
    address             TEXT,
    city                TEXT,
    state               TEXT,
    zip                 TEXT,
    county              TEXT,
    metro               TEXT,
    venue_key           TEXT NOT NULL,     -- groups applications for one premises
    category            TEXT NOT NULL,
    material_hash       TEXT NOT NULL,
    raw                 JSONB NOT NULL,
    first_seen_at       TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,
    last_changed_at     TIMESTAMPTZ NOT NULL,
    removed_at          TIMESTAMPTZ,       -- left an official pending list
    first_snapshot_id   BIGINT REFERENCES raw_snapshots(id),
    last_snapshot_id    BIGINT REFERENCES raw_snapshots(id),
    qualified           BOOLEAN NOT NULL DEFAULT false,
    score               INTEGER NOT NULL DEFAULT 0,
    tier                TEXT,
    qualify_reason      TEXT,
    review_status       TEXT NOT NULL DEFAULT 'new',  -- new | approved | rejected | contacted | snoozed
    review_notes        TEXT,
    reviewed_at         TIMESTAMPTZ,
    UNIQUE (source, source_record_id)
);
-- Lead ranking (qualify.py). Added after launch; safe to re-run.
ALTER TABLE records ADD COLUMN IF NOT EXISTS stage TEXT;        -- Licensed | Approved | In review | Received
ALTER TABLE records ADD COLUMN IF NOT EXISTS lead_score INTEGER; -- 0 to 100
ALTER TABLE records ADD COLUMN IF NOT EXISTS hot BOOLEAN;        -- tier A, not adult, lead_score >= HOT_MIN_SCORE
ALTER TABLE records ADD COLUMN IF NOT EXISTS adult BOOLEAN;      -- adult entertainment: never Hot, Attio or Slack
-- Venue history (history.py). Only the label and a small summary: never
-- other businesses' names or the downloaded license lists.
ALTER TABLE records ADD COLUMN IF NOT EXISTS venue_history TEXT;  -- New venue | New owner | Adding a permit | Unknown
ALTER TABLE records ADD COLUMN IF NOT EXISTS prior_licenses INTEGER; -- prior licenses found at the premises
ALTER TABLE records ADD COLUMN IF NOT EXISTS prior_since DATE;   -- earliest original issue date among them
CREATE INDEX IF NOT EXISTS records_queue_idx ON records (qualified, review_status, metro);
CREATE INDEX IF NOT EXISTS records_venue_idx ON records (venue_key);

CREATE TABLE IF NOT EXISTS record_events (
    id              BIGSERIAL PRIMARY KEY,
    record_id       BIGINT NOT NULL REFERENCES records(id),
    source_run_id   BIGINT NOT NULL REFERENCES source_runs(id),
    event_type      TEXT NOT NULL,   -- new | changed | removed | baseline
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    changes         JSONB,           -- {field: [old, new]} for 'changed'
    queued          BOOLEAN NOT NULL DEFAULT false  -- included in the daily review queue
);
CREATE INDEX IF NOT EXISTS record_events_day_idx ON record_events (observed_at);
CREATE INDEX IF NOT EXISTS record_events_record_idx ON record_events (record_id);

-- Daily review queue: qualified records that were new or materially changed.
CREATE OR REPLACE VIEW review_queue AS
SELECT
    (e.observed_at AT TIME ZONE 'UTC')::date AS queue_date,
    e.event_type,
    e.changes,
    r.id AS record_id,
    r.tier,
    r.score,
    r.legal_name,
    r.dba,
    r.source_record_id,
    r.license_type,
    r.license_description,
    r.application_type,
    r.status,
    r.application_date,
    r.address,
    r.city,
    r.state,
    r.zip,
    r.county,
    r.metro,
    r.venue_key,
    r.source,
    r.source_url,
    r.first_seen_at,
    r.qualify_reason,
    r.review_status,
    r.review_notes,
    -- New columns go at the end: CREATE OR REPLACE VIEW can only append.
    r.stage,
    r.lead_score,
    r.hot,
    r.adult,
    r.venue_history,
    r.prior_licenses,
    r.prior_since
FROM record_events e
JOIN records r ON r.id = e.record_id
WHERE e.queued;

-- One row per premises per day: several applications for the same venue
-- (e.g. TX mixed-beverage + food-and-beverage + late-hours) become one lead.
CREATE OR REPLACE VIEW daily_leads AS
SELECT
    queue_date,
    min(tier) AS tier,
    max(score) AS score,
    (array_agg(legal_name ORDER BY score DESC, record_id))[1] AS legal_name,
    (array_agg(dba ORDER BY score DESC, record_id))[1] AS dba,
    string_agg(DISTINCT event_type, ', ') AS event_types,
    string_agg(DISTINCT source_record_id, ', ') AS source_record_ids,
    string_agg(DISTINCT license_type, ', ') AS license_types,
    string_agg(DISTINCT license_description, '; ') AS license_descriptions,
    string_agg(DISTINCT application_type, ', ') AS application_types,
    string_agg(DISTINCT status, ', ') AS statuses,
    min(application_date) AS application_date,
    (array_agg(address ORDER BY score DESC, record_id))[1] AS address,
    (array_agg(city ORDER BY score DESC, record_id))[1] AS city,
    (array_agg(state ORDER BY score DESC, record_id))[1] AS state,
    (array_agg(zip ORDER BY score DESC, record_id))[1] AS zip,
    (array_agg(county ORDER BY score DESC, record_id))[1] AS county,
    (array_agg(metro ORDER BY score DESC, record_id))[1] AS metro,
    string_agg(DISTINCT source, ', ') AS source,
    (array_agg(source_url ORDER BY score DESC, record_id))[1] AS source_url,
    min(first_seen_at) AS first_seen_at,
    (array_agg(qualify_reason ORDER BY score DESC, record_id))[1] AS qualify_reason,
    CASE WHEN count(DISTINCT review_status) = 1 THEN min(review_status)
         ELSE 'mixed' END AS review_status,
    string_agg(DISTINCT review_notes, ' | ') AS review_notes,
    string_agg(DISTINCT record_id::text, ' ') AS record_ids,
    venue_key,
    (array_agg(stage ORDER BY CASE stage WHEN 'Licensed' THEN 4 WHEN 'Approved' THEN 3
                                         WHEN 'In review' THEN 2 WHEN 'Received' THEN 1
                                         ELSE 0 END DESC, record_id))[1] AS stage,
    max(lead_score) AS lead_score,
    coalesce(bool_or(hot), false) AS hot,
    coalesce(bool_or(adult), false) AS adult,
    -- Best label wins (history.LABELS order).
    (array_agg(venue_history ORDER BY CASE coalesce(venue_history, 'Unknown')
                                     WHEN 'New venue' THEN 1 WHEN 'Unknown' THEN 2
                                     WHEN 'New owner' THEN 3 WHEN 'Adding a permit' THEN 4
                                     ELSE 2 END, record_id))[1] AS venue_history,
    max(prior_licenses) AS prior_licenses,
    min(prior_since) AS prior_since
FROM review_queue
GROUP BY queue_date, venue_key;

-- Contact lookup (contact.py): can the owner reach this venue? One row per
-- venue_key, only for venues eligible for Attio. Contact details live only
-- here, in the private database; logs carry counts only.
CREATE TABLE IF NOT EXISTS contact_checks (
    venue_key           TEXT PRIMARY KEY,
    status              TEXT NOT NULL,           -- reachable | waiting | gave_up
    channels            JSONB NOT NULL DEFAULT '[]',  -- every channel found, with signals and score
    confidence_score    INTEGER NOT NULL DEFAULT 0,   -- 0 to 100, best channel
    confidence_label    TEXT NOT NULL DEFAULT 'None', -- Verified | Likely | Unverified | None
    confidence_reason   TEXT,
    outreach_method     TEXT,                    -- best way to reach, or "Wait: ..."
    outreach_second     TEXT,
    contact_kind        TEXT,                    -- the channel the method uses
    contact_value       TEXT,
    contact_url         TEXT,
    place_id            TEXT,                    -- Google place id of the matched listing
    maps_url            TEXT,
    business_status     TEXT,                    -- Google: OPERATIONAL, CLOSED_TEMPORARILY ...
    attempts            INTEGER NOT NULL DEFAULT 0,
    first_checked_at    TIMESTAMPTZ NOT NULL,
    last_checked_at     TIMESTAMPTZ NOT NULL,
    next_check_at       TIMESTAMPTZ,             -- waiting venues only
    became_reachable_at TIMESTAMPTZ,
    newly_reachable_on  DATE,                    -- the day it moved from waiting to reachable
    gave_up_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS contact_checks_due_idx ON contact_checks (status, next_check_at);
CREATE INDEX IF NOT EXISTS contact_checks_newly_idx ON contact_checks (newly_reachable_on);
