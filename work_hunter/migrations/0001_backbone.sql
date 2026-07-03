CREATE TABLE IF NOT EXISTS hh_auth_profiles (
    name TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 0,
    has_access_token INTEGER NOT NULL DEFAULT 0,
    has_refresh_token INTEGER NOT NULL DEFAULT 0,
    access_expires_at TEXT NOT NULL DEFAULT '',
    client_id_set INTEGER NOT NULL DEFAULT 0,
    client_secret_set INTEGER NOT NULL DEFAULT 0,
    cookie_file TEXT NOT NULL DEFAULT '',
    has_xsrf INTEGER NOT NULL DEFAULT 0,
    last_whoami_at TEXT NOT NULL DEFAULT '',
    last_whoami_status TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS source_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL DEFAULT '',
    operation TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    count_found INTEGER NOT NULL DEFAULT 0,
    count_imported INTEGER NOT NULL DEFAULT 0,
    count_updated INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    trace_id TEXT NOT NULL DEFAULT '',
    summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL DEFAULT '',
    operation TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL DEFAULT '',
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agent_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL DEFAULT '',
    decision_type TEXT NOT NULL DEFAULT '',
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    model TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    requires_review INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    risk_flags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT '',
    approved_at TEXT NOT NULL DEFAULT '',
    rejected_at TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS search_presets (
    name TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '{}',
    dry_run_checked_at TEXT NOT NULL DEFAULT '',
    last_live_run_at TEXT NOT NULL DEFAULT '',
    last_result_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 0
);
