ALTER TABLE tasks ADD COLUMN heartbeat_at TEXT;
ALTER TABLE tasks ADD COLUMN workflow_version TEXT NOT NULL DEFAULT 'melopet-m1@1.0.0';

ALTER TABLE model_calls ADD COLUMN call_key TEXT NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_model_calls_idempotent
ON model_calls(task_id, node, call_key)
WHERE call_key <> '';

CREATE TABLE IF NOT EXISTS signal_packages (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    track_ref TEXT NOT NULL,
    source_mode TEXT NOT NULL,
    allowed_signals_json TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    consent_decision_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, track_ref),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (track_ref) REFERENCES tracks(track_ref),
    FOREIGN KEY (consent_decision_id) REFERENCES consent_decisions(id)
);

CREATE INDEX IF NOT EXISTS idx_signal_packages_owner_task
ON signal_packages(owner_id, task_id, track_ref);

CREATE TABLE IF NOT EXISTS deletion_requests (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id_hash TEXT NOT NULL,
    task_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    error_code TEXT,
    requested_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_deletion_requests_owner
ON deletion_requests(owner_id, requested_at DESC);

CREATE TABLE IF NOT EXISTS worker_instances (
    worker_id TEXT PRIMARY KEY,
    process_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
