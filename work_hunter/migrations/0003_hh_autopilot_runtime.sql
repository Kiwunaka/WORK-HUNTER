CREATE TABLE hh_autopilot_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_profile_id TEXT NOT NULL,
    trigger TEXT NOT NULL CHECK (trigger IN ('schedule','manual','shadow','retry','recovery','canary')),
    status TEXT NOT NULL CHECK (status IN ('created','running','stop_requested','completed','failed','interrupted','cancelled')),
    grant_id INTEGER,
    policy_hash TEXT NOT NULL,
    fencing_token INTEGER NOT NULL DEFAULT 0,
    counters_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_hh_autopilot_runs_account_status
    ON hh_autopilot_runs(account_profile_id, status, created_at);

CREATE TABLE hh_autopilot_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin_run_id INTEGER NOT NULL REFERENCES hh_autopilot_runs(id),
    last_run_id INTEGER NOT NULL REFERENCES hh_autopilot_runs(id),
    account_profile_id TEXT NOT NULL,
    vacancy_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    query_key TEXT NOT NULL,
    state TEXT NOT NULL,
    retry_stage TEXT NOT NULL DEFAULT 'eligibility',
    version INTEGER NOT NULL DEFAULT 0,
    filter_json TEXT NOT NULL DEFAULT '{}',
    deterministic_score REAL,
    ai_json TEXT NOT NULL DEFAULT '{}',
    application_attempt_count INTEGER NOT NULL DEFAULT 0,
    reconciliation_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL DEFAULT '',
    last_outcome_code TEXT NOT NULL DEFAULT '',
    active_attempt_id INTEGER REFERENCES hh_application_attempts(id),
    challenge_id INTEGER,
    idempotency_key TEXT NOT NULL,
    published_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(account_profile_id, idempotency_key)
);
CREATE INDEX idx_hh_autopilot_items_due
    ON hh_autopilot_items(account_profile_id, state, next_attempt_at);

CREATE TABLE hh_autopilot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES hh_autopilot_runs(id),
    item_id INTEGER NOT NULL REFERENCES hh_autopilot_items(id),
    previous_state TEXT NOT NULL,
    next_state TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_hh_autopilot_events_item ON hh_autopilot_events(item_id, id);

CREATE TABLE hh_autopilot_leases (
    account_profile_id TEXT PRIMARY KEY,
    owner_token TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE hh_autopilot_quota_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER UNIQUE REFERENCES hh_application_attempts(id),
    run_id INTEGER REFERENCES hh_autopilot_runs(id),
    source TEXT NOT NULL CHECK (source IN ('dispatch','external_sync')),
    remote_negotiation_id TEXT UNIQUE,
    account_profile_id TEXT NOT NULL,
    timezone TEXT NOT NULL,
    local_date TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('reserved','held','consumed','released')),
    fencing_token INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_hh_autopilot_quota_day
    ON hh_autopilot_quota_reservations(account_profile_id, local_date, state);

CREATE TABLE hh_autopilot_challenges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL CHECK (scope IN ('item','account')),
    challenge_type TEXT NOT NULL,
    account_profile_id TEXT NOT NULL,
    item_id INTEGER REFERENCES hh_autopilot_items(id),
    reservation_id INTEGER REFERENCES hh_autopilot_quota_reservations(id),
    sanitized_url TEXT NOT NULL DEFAULT '',
    screenshot_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('open','in_progress','resolved','dismissed','expired')),
    expires_at TEXT NOT NULL DEFAULT '',
    resolution_at TEXT NOT NULL DEFAULT '',
    resolution_actor TEXT NOT NULL DEFAULT '',
    resolution_action TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE hh_autopilot_search_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_profile_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    origin_run_id INTEGER NOT NULL REFERENCES hh_autopilot_runs(id),
    owner_run_id INTEGER NOT NULL REFERENCES hh_autopilot_runs(id),
    claim_version INTEGER NOT NULL DEFAULT 0,
    fencing_token INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','complete','failed','interrupted','superseded')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_hh_autopilot_cycles_recovery
    ON hh_autopilot_search_cycles(account_profile_id, policy_hash, status);

CREATE TABLE hh_autopilot_search_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES hh_autopilot_search_cycles(id) ON DELETE CASCADE,
    resume_id TEXT NOT NULL,
    query_key TEXT NOT NULL,
    next_page INTEGER NOT NULL DEFAULT 0,
    reported_total INTEGER,
    unique_vacancy_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('pending','running','complete','failed')),
    updated_at TEXT NOT NULL,
    UNIQUE(cycle_id, resume_id, query_key)
);

CREATE TABLE hh_autopilot_search_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES hh_autopilot_search_cycles(id) ON DELETE CASCADE,
    checkpoint_id INTEGER NOT NULL REFERENCES hh_autopilot_search_checkpoints(id) ON DELETE CASCADE,
    account_profile_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    query_key TEXT NOT NULL,
    vacancy_id TEXT NOT NULL,
    page INTEGER NOT NULL,
    normalized_json TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    UNIQUE(cycle_id, resume_id, query_key, vacancy_id)
);
CREATE INDEX idx_hh_autopilot_search_results_vacancy
    ON hh_autopilot_search_results(account_profile_id, vacancy_id);

CREATE TABLE hh_autopilot_shadow_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES hh_autopilot_runs(id),
    account_profile_id TEXT NOT NULL,
    vacancy_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    filter_json TEXT NOT NULL,
    deterministic_score REAL,
    ai_json TEXT NOT NULL DEFAULT '{}',
    would_apply INTEGER NOT NULL CHECK (would_apply IN (0,1)),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, vacancy_id, resume_id)
);

CREATE TABLE hh_autopilot_account_state (
    account_profile_id TEXT PRIMARY KEY,
    blocked_until TEXT NOT NULL DEFAULT '',
    block_reason TEXT NOT NULL DEFAULT '',
    hh_reset_json TEXT NOT NULL DEFAULT '{}',
    last_scheduled_at TEXT NOT NULL DEFAULT '',
    next_scheduled_at TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE hh_application_account_guards (
    account_profile_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    owner_attempt_id INTEGER REFERENCES hh_application_attempts(id),
    first_resume_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','applied','external_applied')),
    application_id INTEGER REFERENCES applications(id),
    application_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(account_profile_id, source, source_id)
);

CREATE TABLE hh_autopilot_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_profile_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope = 'applications'),
    policy_hash TEXT NOT NULL,
    generation INTEGER NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0,1)),
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT NOT NULL DEFAULT '',
    UNIQUE(account_profile_id, scope, generation)
);
CREATE UNIQUE INDEX idx_hh_autopilot_active_grant
    ON hh_autopilot_grants(account_profile_id, scope) WHERE active = 1;

CREATE TABLE hh_autopilot_one_shot_authorizations (
    reference_id TEXT PRIMARY KEY,
    authorization_type TEXT NOT NULL CHECK (authorization_type IN ('manual','canary')),
    account_profile_id TEXT NOT NULL,
    max_success INTEGER NOT NULL CHECK (max_success >= 1),
    consumed_success INTEGER NOT NULL DEFAULT 0 CHECK (consumed_success >= 0 AND consumed_success <= max_success),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    CHECK (authorization_type != 'canary' OR max_success = 1)
);

CREATE TABLE hh_autopilot_one_shot_targets (
    authorization_ref TEXT NOT NULL REFERENCES hh_autopilot_one_shot_authorizations(reference_id) ON DELETE CASCADE,
    resume_id TEXT NOT NULL,
    vacancy_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','active','succeeded','closed')),
    active_attempt_id INTEGER REFERENCES hh_application_attempts(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(authorization_ref, resume_id, vacancy_id)
);

CREATE TABLE hh_autopilot_controls (
    scope_type TEXT NOT NULL CHECK (scope_type IN ('global','account')),
    scope_id TEXT NOT NULL,
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0,1)),
    kill_switch INTEGER NOT NULL DEFAULT 0 CHECK (kill_switch IN (0,1)),
    version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(scope_type, scope_id)
);

CREATE INDEX idx_hh_autopilot_items_challenge
    ON hh_autopilot_items(challenge_id);
