ALTER TABLE applications RENAME TO applications_legacy_0002;

CREATE TABLE applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_profile_id TEXT NOT NULL DEFAULT 'legacy',
    job_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'applied',
    notes TEXT NOT NULL DEFAULT '',
    applied_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    resume_id TEXT NOT NULL DEFAULT '',
    resume_hash TEXT NOT NULL DEFAULT '',
    plan_id INTEGER,
    transport TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    autopilot_run_id INTEGER,
    autopilot_item_id INTEGER,
    autopilot_attempt_id INTEGER,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    UNIQUE(account_profile_id, job_id, resume_id)
);

INSERT INTO applications (
    id, account_profile_id, job_id, status, notes, applied_at, updated_at,
    source, source_id, resume_id, resume_hash, plan_id, transport, sent_at,
    result_json, error
)
SELECT
    id, 'legacy', job_id, status, notes, applied_at, updated_at,
    source, source_id, resume_id, resume_hash, plan_id, transport, sent_at,
    result_json, error
FROM applications_legacy_0002;

DROP TABLE applications_legacy_0002;
CREATE INDEX idx_applications_account_sent
    ON applications(account_profile_id, sent_at);

ALTER TABLE hh_application_attempts
    ADD COLUMN account_profile_id TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE hh_application_attempts ADD COLUMN autopilot_run_id INTEGER;
ALTER TABLE hh_application_attempts ADD COLUMN autopilot_item_id INTEGER;
ALTER TABLE hh_application_attempts ADD COLUMN autopilot_attempt_id INTEGER;
ALTER TABLE hh_application_attempts
    ADD COLUMN authorization_kind TEXT NOT NULL DEFAULT '';
ALTER TABLE hh_application_attempts
    ADD COLUMN authorization_ref TEXT NOT NULL DEFAULT '';
ALTER TABLE hh_application_attempts
    ADD COLUMN policy_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE hh_application_attempts
    ADD COLUMN delivery_certainty TEXT NOT NULL DEFAULT '';
ALTER TABLE hh_application_attempts
    ADD COLUMN dispatched_at TEXT NOT NULL DEFAULT '';
ALTER TABLE hh_application_attempts
    ADD COLUMN finished_at TEXT NOT NULL DEFAULT '';
