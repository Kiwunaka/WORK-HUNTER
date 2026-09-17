ALTER TABLE resumes ADD COLUMN target_role TEXT NOT NULL DEFAULT '';
ALTER TABLE resumes ADD COLUMN hh_resume_id TEXT NOT NULL DEFAULT '';
ALTER TABLE resumes ADD COLUMN hh_account_profile_id TEXT NOT NULL DEFAULT '';
ALTER TABLE resumes ADD COLUMN facts_hash TEXT NOT NULL DEFAULT '';

CREATE TABLE ai_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL,
    backend TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_usd REAL,
    duration_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE candidate_fit_cache (
    cache_key TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE hh_application_resume_snapshots (
    item_id INTEGER PRIMARY KEY REFERENCES hh_autopilot_items(id),
    resume_hash TEXT NOT NULL,
    resume_json TEXT NOT NULL,
    candidate_hash TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
