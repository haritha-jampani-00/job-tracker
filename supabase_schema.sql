-- Run this in your Supabase SQL Editor (https://supabase.com/dashboard)
-- Project > SQL Editor > New Query > Paste & Run

CREATE TABLE IF NOT EXISTS jobs (
    id                  BIGSERIAL PRIMARY KEY,
    source              TEXT NOT NULL,
    company             TEXT NOT NULL,
    title               TEXT NOT NULL,
    location            TEXT DEFAULT '',
    department          TEXT DEFAULT '',
    employment_type     TEXT DEFAULT '',
    url                 TEXT NOT NULL,
    date_posted         TEXT,
    date_found          TEXT NOT NULL,
    description_snippet TEXT DEFAULT '',
    description_full    TEXT DEFAULT '',
    match_score         INTEGER DEFAULT 0,
    recommendation      TEXT DEFAULT '',
    strong_matches      JSONB DEFAULT '[]',
    missing_keywords    JSONB DEFAULT '[]',
    red_flags           JSONB DEFAULT '[]',
    experience_alignment TEXT DEFAULT '',
    resume_improvement_prompt TEXT DEFAULT '',
    sponsorship_status  TEXT DEFAULT 'unknown',
    experience_level    TEXT DEFAULT '',
    status              TEXT DEFAULT 'Not Applied',
    notes               TEXT DEFAULT '',
    roles_matched       JSONB DEFAULT '[]',
    canonical_key       TEXT NOT NULL UNIQUE,
    is_visible          BOOLEAN DEFAULT TRUE,
    filter_reason       TEXT DEFAULT '',
    job_status          TEXT DEFAULT 'Discovered',
    applied_at          TEXT,
    updated_at          TEXT
);

-- Migration: add columns to existing tables (safe to re-run)
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS is_visible BOOLEAN DEFAULT TRUE;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS filter_reason TEXT DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS job_status TEXT DEFAULT 'Discovered';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS applied_at TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS updated_at TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS resume_improvement_prompt TEXT DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS description_full TEXT DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS sponsorship_status TEXT DEFAULT 'unknown';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS experience_level TEXT DEFAULT '';

-- Data migration: map old status values to new job_status
UPDATE jobs SET job_status = CASE
    WHEN status = 'Applied' THEN 'Applied'
    WHEN status IN ('Interview', 'Phone Screen', 'Technical', 'Onsite') THEN 'Interviewing'
    WHEN status = 'Offer' THEN 'Offer'
    WHEN status = 'Rejected' THEN 'Rejected'
    WHEN status = 'Withdrawn' THEN 'Withdrawn'
    ELSE 'Discovered'
END
WHERE job_status IS NULL OR job_status = 'Discovered';

CREATE TABLE IF NOT EXISTS run_log (
    id          BIGSERIAL PRIMARY KEY,
    run_time    TEXT NOT NULL,
    roles       JSONB NOT NULL,
    jobs_found  INTEGER DEFAULT 0,
    jobs_added  INTEGER DEFAULT 0
);

-- ── Discovered slugs (auto-discovery cache) ─────────────
CREATE TABLE IF NOT EXISTS discovered_slugs (
    slug            TEXT NOT NULL,
    ats_type        TEXT NOT NULL,
    company_name    TEXT DEFAULT '',
    discovered_at   TEXT NOT NULL,
    last_verified   TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    PRIMARY KEY (slug, ats_type)
);

-- Index for faster queries
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs (match_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_canonical ON jobs (canonical_key);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_job_status ON jobs (job_status);
