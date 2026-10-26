PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS capabilities (
    token_hash TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS consent_decisions (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    provider TEXT NOT NULL,
    region TEXT,
    version TEXT NOT NULL,
    decision TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS playlist_imports (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    object_ref TEXT NOT NULL,
    input_kind TEXT NOT NULL,
    original_name_hash TEXT,
    file_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    mapping_draft_json TEXT NOT NULL,
    headers_json TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    consent_decision_id TEXT,
    failure_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (consent_decision_id) REFERENCES consent_decisions(id)
);

CREATE INDEX IF NOT EXISTS idx_playlist_imports_owner_hash
ON playlist_imports(owner_id, file_hash, status);

CREATE TABLE IF NOT EXISTS request_idempotency (
    owner_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, scope, idempotency_key)
);

CREATE TABLE IF NOT EXISTS playlist_versions (
    id TEXT PRIMARY KEY,
    import_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    normalized_count INTEGER NOT NULL,
    invalid_count INTEGER NOT NULL,
    duplicate_count INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    mapping_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(import_id, version_number),
    FOREIGN KEY (import_id) REFERENCES playlist_imports(id)
);

CREATE INDEX IF NOT EXISTS idx_playlist_versions_owner
ON playlist_versions(owner_id, import_id, created_at);

CREATE TABLE IF NOT EXISTS tracks (
    track_ref TEXT PRIMARY KEY,
    playlist_version_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    row_ref INTEGER NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT,
    duration_ms INTEGER,
    platform_track_id TEXT,
    allowed_signals_json TEXT NOT NULL,
    match_status TEXT NOT NULL,
    excluded INTEGER NOT NULL DEFAULT 0,
    duplicate_of TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(playlist_version_id, row_ref),
    FOREIGN KEY (playlist_version_id) REFERENCES playlist_versions(id),
    FOREIGN KEY (duplicate_of) REFERENCES tracks(track_ref)
);

CREATE INDEX IF NOT EXISTS idx_tracks_playlist
ON tracks(playlist_version_id, row_ref);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    task_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    node TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_until TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    input_json TEXT NOT NULL,
    output_json TEXT NOT NULL,
    journal_json TEXT NOT NULL,
    error_code TEXT,
    safe_message TEXT,
    retry_allowed INTEGER NOT NULL DEFAULT 1,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(owner_id, task_type, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_tasks_claim
ON tasks(status, lease_until, created_at);

CREATE TABLE IF NOT EXISTS mood_profiles (
    revision TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    playlist_version_id TEXT NOT NULL,
    source_task_id TEXT,
    parent_revision TEXT,
    revision_number INTEGER NOT NULL,
    taxonomy_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    counts_json TEXT NOT NULL,
    percentages_json TEXT NOT NULL,
    known_count INTEGER NOT NULL,
    unknown_count INTEGER NOT NULL,
    excluded_count INTEGER NOT NULL,
    normalized_total INTEGER NOT NULL,
    quality_flags_json TEXT NOT NULL,
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(playlist_version_id, revision_number),
    UNIQUE(source_task_id),
    FOREIGN KEY (playlist_version_id) REFERENCES playlist_versions(id),
    FOREIGN KEY (source_task_id) REFERENCES tasks(id),
    FOREIGN KEY (parent_revision) REFERENCES mood_profiles(revision)
);

CREATE INDEX IF NOT EXISTS idx_profiles_owner_playlist
ON mood_profiles(owner_id, playlist_version_id, revision_number DESC);

CREATE TABLE IF NOT EXISTS profile_tracks (
    profile_revision TEXT NOT NULL,
    track_ref TEXT NOT NULL,
    auto_mood_id TEXT NOT NULL,
    final_mood_id TEXT NOT NULL,
    confidence_band TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    source_mode TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    excluded INTEGER NOT NULL DEFAULT 0,
    correction_source TEXT NOT NULL,
    PRIMARY KEY (profile_revision, track_ref),
    FOREIGN KEY (profile_revision) REFERENCES mood_profiles(revision),
    FOREIGN KEY (track_ref) REFERENCES tracks(track_ref)
);

CREATE TABLE IF NOT EXISTS pet_candidates (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    profile_revision TEXT NOT NULL,
    candidate_index INTEGER NOT NULL,
    spec_json TEXT NOT NULL,
    copy_json TEXT NOT NULL,
    preview_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, candidate_index),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (profile_revision) REFERENCES mood_profiles(revision)
);

CREATE INDEX IF NOT EXISTS idx_pet_candidates_owner_task
ON pet_candidates(owner_id, task_id, candidate_index);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    owner_hash TEXT,
    event_type TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    status TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_events_resource
ON audit_events(resource_type, resource_id, created_at);
