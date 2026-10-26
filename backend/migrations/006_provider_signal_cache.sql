CREATE TABLE IF NOT EXISTS provider_signal_cache (
    provider TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (provider, resource_id, provider_version)
);

CREATE INDEX IF NOT EXISTS idx_provider_signal_cache_expiry
ON provider_signal_cache(provider, provider_version, expires_at);
