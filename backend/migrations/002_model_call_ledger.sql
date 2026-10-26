CREATE TABLE IF NOT EXISTS model_calls (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    node TEXT NOT NULL,
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    estimated_cost REAL NOT NULL,
    latency_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_model_calls_task
ON model_calls(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_model_calls_owner_date
ON model_calls(owner_id, created_at);
