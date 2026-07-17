CREATE TABLE hh_autopilot_notification_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL,
    sink_key TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('challenge','run_failure')),
    account_profile_id TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','sent')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(event_key, sink_key)
);

CREATE INDEX idx_hh_autopilot_notification_due
    ON hh_autopilot_notification_deliveries(status, next_attempt_at, id);
