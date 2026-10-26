CREATE TABLE IF NOT EXISTS pet_actions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    action TEXT NOT NULL,
    preview_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, candidate_id, action),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (candidate_id) REFERENCES pet_candidates(id)
);

CREATE INDEX IF NOT EXISTS idx_pet_actions_candidate
ON pet_actions(candidate_id, action);
