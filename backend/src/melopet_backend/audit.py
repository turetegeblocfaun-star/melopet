from __future__ import annotations

import hashlib
from typing import Any

from .db import Database
from .util import canonical_json, utc_now


def record_audit(
    database: Database,
    *,
    event_type: str,
    status: str,
    owner_id: str | None = None,
    request_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    owner_hash = hashlib.sha256(owner_id.encode()).hexdigest()[:16] if owner_id else None
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO audit_events(request_id, owner_hash, event_type, resource_type, "
            "resource_id, status, detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request_id,
                owner_hash,
                event_type,
                resource_type,
                resource_id,
                status,
                canonical_json(detail or {}),
                utc_now(),
            ),
        )
