CREATE TABLE IF NOT EXISTS pet_selections (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    candidate_record_id TEXT NOT NULL,
    selection_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (candidate_record_id) REFERENCES pet_candidates(id)
);

CREATE INDEX IF NOT EXISTS idx_pet_selections_owner
ON pet_selections(owner_id, updated_at DESC);
