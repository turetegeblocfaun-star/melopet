from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings
from .db import Database
from .errors import AppError
from .util import new_id, utc_now


@dataclass(frozen=True, slots=True)
class Principal:
    owner_id: str
    token_hash: str


class CapabilityService:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings

    def _hash_token(self, token: str) -> str:
        value = f"{self.settings.capability_token_pepper}:{token}".encode()
        return hashlib.sha256(value).hexdigest()

    def issue(self) -> tuple[str, str]:
        owner_id = new_id("owner")
        token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO capabilities(token_hash, owner_id, created_at) VALUES (?, ?, ?)",
                (token_hash, owner_id, utc_now()),
            )
        return token, owner_id

    def authenticate(self, token: str) -> Principal:
        candidate_hash = self._hash_token(token)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT token_hash, owner_id, expires_at, revoked_at FROM capabilities WHERE token_hash = ?",
                (candidate_hash,),
            ).fetchone()
        if row is None or not hmac.compare_digest(row["token_hash"], candidate_hash):
            raise AppError("UNAUTHORIZED", "身份凭证无效或已过期。", 401)
        if row["revoked_at"]:
            raise AppError("UNAUTHORIZED", "身份凭证已撤销。", 401)
        if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            raise AppError("UNAUTHORIZED", "身份凭证已过期。", 401)
        return Principal(row["owner_id"], row["token_hash"])


capability_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="MeloPetCapability",
    description="POST /v1/capabilities/anonymous 获取的本地 capability token。",
)


def bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(capability_bearer)],
) -> str:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise AppError("UNAUTHORIZED", "请提供有效的 Bearer capability token。", 401)
    token = credentials.credentials.strip()
    if not token:
        raise AppError("UNAUTHORIZED", "请提供有效的 Bearer capability token。", 401)
    return token
