from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import desktop_launch
from .audit import record_audit
from .catalogs import CatalogBundle, load_catalogs
from .config import Settings
from .db import Database
from .dna import build_dna
from .errors import AppError
from .model_gateway import ModelCallResult, ModelGateway, build_model_gateway
from .mood import ClassifiedTrack, classify_tracks, compute_profile
from .netease import (
    NETEASE_SIGNAL_PROVIDER_VERSION,
    extract_netease_playlist_id,
    resolve_netease_playlist,
    tracks_to_playlist_text,
)
from .object_store import LocalObjectStore
from .pet import (
    PET_ACTIONS,
    build_action_prompt,
    create_candidate,
    render_character_card,
    render_preview,
    validate_customization,
)
from .playlist import (
    ALLOWED_CONTENT_TYPES,
    ALLOWED_UPLOAD_SUFFIXES,
    PARSER_VERSION,
    decode_input,
    normalize_tracks,
    parse_table,
    suggest_mapping,
)
from .schemas import ColumnMapping, CorrectionsRequest, PetCustomization
from .util import canonical_json, json_hash, new_id, safe_json_loads, sha256_bytes, utc_now

TERMINAL_TASK_STATES = {
    "PROFILE_READY",
    "CANDIDATES_READY",
    "ACTIONS_READY",
    "DELETION_COMPLETED",
    "SAFE_FALLBACK_READY",
    "FINAL_FAILED",
    "CANCELLED",
}
RUNNING_TASK_STATES = {
    "RESOLVING_SIGNALS",
    "CLASSIFYING",
    "GUARDING",
    "COMPUTING",
    "MAPPING_PET",
    "GENERATING_COPY",
    "GENERATING_ACTIONS",
    "VALIDATING_ASSETS",
    "RENDERING",
    "DELETING_DATA",
}


class MeloPetService:
    def __init__(
        self,
        settings: Settings,
        *,
        database: Database | None = None,
        model_gateway: ModelGateway | None = None,
    ):
        self.settings = settings
        self.database = database or Database(settings)
        self.database.migrate()
        self.object_store = LocalObjectStore(settings.object_path)
        self.preview_store = LocalObjectStore(settings.preview_path)
        self.catalogs: CatalogBundle = load_catalogs(settings)
        self._validate_startup()
        self.model_gateway = model_gateway or build_model_gateway(settings)

    def _validate_startup(self) -> None:
        if self.settings.music_signal_provider != "disabled":
            raise ValueError("MUSIC_SIGNAL_PROVIDER_NOT_APPROVED_OR_IMPLEMENTED")
        if self.settings.model_enabled:
            required_prompts = (
                self.settings.prompts_path / f"{self.settings.prompt_mood_version}.txt",
                self.settings.prompts_path / f"{self.settings.prompt_copy_version}.txt",
            )
            if any(not path.is_file() for path in required_prompts):
                raise ValueError("MODEL_PROMPT_VERSION_NOT_FOUND")
        if self.settings.app_env == "production":
            failures = []
            if self.settings.database_url.startswith("sqlite:///"):
                failures.append("PRODUCTION_DATABASE_ADAPTER_NOT_CONFIGURED")
            if self.settings.capability_token_pepper.startswith(("dev-only", "replace-with")):
                failures.append("INSECURE_CAPABILITY_TOKEN_PEPPER")
            if not self.catalogs.production_eligible:
                failures.append("PRODUCTION_ASSET_CATALOG_NOT_CONFIGURED")
            if failures:
                raise ValueError(",".join(failures))

    @staticmethod
    def _request_hash(payload: Any) -> str:
        return json_hash(payload)

    @staticmethod
    def _require_idempotency_key(value: str | None) -> str:
        key = (value or "").strip()
        if not 8 <= len(key) <= 200:
            raise AppError(
                "IDEMPOTENCY_KEY_REQUIRED",
                "写操作必须提供 8–200 个字符的 Idempotency-Key。",
                400,
            )
        return key

    def _load_netease_signal_cache(self, song_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not song_ids:
            return {}
        result: dict[str, dict[str, Any]] = {}
        now = utc_now()
        with self.database.connect() as connection:
            for start in range(0, len(song_ids), 400):
                chunk = song_ids[start : start + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    "SELECT resource_id, payload_json FROM provider_signal_cache "
                    "WHERE provider = ? AND provider_version = ? AND expires_at > ? "
                    f"AND resource_id IN ({placeholders})",
                    ("netease", NETEASE_SIGNAL_PROVIDER_VERSION, now, *chunk),
                ).fetchall()
                for row in rows:
                    payload = safe_json_loads(row["payload_json"], {})
                    if isinstance(payload, dict):
                        result[row["resource_id"]] = payload
        return result

    def _store_netease_signal_cache(self, entries: dict[str, dict[str, Any]]) -> None:
        if not entries:
            return
        now = utc_now()
        expires_at = (datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="milliseconds")
        with self.database.transaction(immediate=True) as connection:
            connection.executemany(
                "INSERT INTO provider_signal_cache(provider, resource_id, provider_version, payload_json, "
                "expires_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider, resource_id, provider_version) DO UPDATE SET "
                "payload_json = excluded.payload_json, expires_at = excluded.expires_at, "
                "updated_at = excluded.updated_at",
                [
                    (
                        "netease",
                        song_id,
                        NETEASE_SIGNAL_PROVIDER_VERSION,
                        canonical_json(signals),
                        expires_at,
                        now,
                    )
                    for song_id, signals in entries.items()
                ],
            )

    @staticmethod
    def _get_owned(connection: sqlite3.Connection, table: str, resource_id: str, owner_id: str):
        queries = {
            "consent_decisions": "SELECT * FROM consent_decisions WHERE id = ?",
            "deletion_requests": "SELECT * FROM deletion_requests WHERE id = ?",
            "playlist_imports": "SELECT * FROM playlist_imports WHERE id = ?",
            "playlist_versions": "SELECT * FROM playlist_versions WHERE id = ?",
            "tasks": "SELECT * FROM tasks WHERE id = ?",
            "pet_candidates": "SELECT * FROM pet_candidates WHERE id = ?",
            "pet_selections": "SELECT * FROM pet_selections WHERE id = ?",
        }
        if table == "mood_profiles":
            row = connection.execute("SELECT * FROM mood_profiles WHERE revision = ?", (resource_id,)).fetchone()
        else:
            query = queries.get(table)
            if query is None:
                raise ValueError("unsupported resource table")
            row = connection.execute(query, (resource_id,)).fetchone()
        if row is None:
            raise AppError("NOT_FOUND", "请求的资源不存在。", 404)
        if row["owner_id"] != owner_id:
            raise AppError("FORBIDDEN", "你无权访问该资源。", 403)
        return row

    def _record_idempotency(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        scope: str,
        key: str,
        resource_id: str,
        request_hash: str,
    ) -> None:
        connection.execute(
            "INSERT INTO request_idempotency(owner_id, scope, idempotency_key, resource_id, "
            "request_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (owner_id, scope, key, resource_id, request_hash, utc_now()),
        )

    def _idempotent_resource(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        scope: str,
        key: str,
        request_hash: str,
    ) -> str | None:
        row = connection.execute(
            "SELECT resource_id, request_hash FROM request_idempotency WHERE owner_id = ? AND scope = ? AND idempotency_key = ?",
            (owner_id, scope, key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise AppError(
                "IDEMPOTENCY_CONFLICT",
                "同一 Idempotency-Key 已用于不同请求，请更换键后重试。",
                409,
            )
        return row["resource_id"]

    def _resolve_playlist_source(self, data: bytes) -> tuple[bytes, str | None]:
        """Resolve a pasted NetEase playlist URL into CSV text.

        Returns ``(resolved_data, source_label)``. When the pasted text is not
        a NetEase playlist URL, the original bytes are returned unchanged with
        a ``None`` label.
        """
        playlist_id = extract_netease_playlist_id(decode_input(data))
        if playlist_id is None:
            return data, None
        tracks = resolve_netease_playlist(
            playlist_id,
            timeout=self.settings.playlist_url_timeout_seconds,
            max_tracks=self.settings.playlist_max_tracks,
            signal_loader=self._load_netease_signal_cache,
            signal_writer=self._store_netease_signal_cache,
        )
        return tracks_to_playlist_text(tracks).encode("utf-8"), f"netease:playlist:{playlist_id}"

    def create_import(
        self,
        *,
        owner_id: str,
        data: bytes,
        filename: str | None,
        content_type: str | None,
        input_kind: str,
        consent_external_processing: bool,
        idempotency_key: str | None,
        create_new_copy: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        key = self._require_idempotency_key(idempotency_key)
        if len(data) > self.settings.upload_max_bytes:
            raise AppError(
                "FILE_TOO_LARGE",
                f"文件超过 {self.settings.upload_max_bytes} 字节限制。",
                details={"maxBytes": self.settings.upload_max_bytes},
            )
        if input_kind not in {"file", "text"}:
            raise AppError("INVALID_INPUT_KIND", "inputKind 只能是 file 或 text。")
        source_label: str | None = None
        if input_kind == "file":
            suffix = Path(filename or "").suffix.casefold()
            if suffix not in ALLOWED_UPLOAD_SUFFIXES:
                raise AppError("FILE_UNSUPPORTED", "仅支持 .csv 或 .txt 文件。")
            normalized_type = (content_type or "application/octet-stream").split(";", 1)[0]
            if normalized_type not in ALLOWED_CONTENT_TYPES:
                raise AppError("FILE_SPOOFED", "文件类型与允许格式不符，请重新导出。")
        else:
            suffix = ".txt"
            data, source_label = self._resolve_playlist_source(data)
            if len(data) > self.settings.upload_max_bytes:
                raise AppError(
                    "FILE_TOO_LARGE",
                    f"文件超过 {self.settings.upload_max_bytes} 字节限制。",
                    details={"maxBytes": self.settings.upload_max_bytes},
                )

        table = parse_table(data, self.settings.playlist_max_tracks)
        file_hash = sha256_bytes(data)
        request_hash = self._request_hash(
            {
                "fileHash": file_hash,
                "inputKind": input_kind,
                "consent": consent_external_processing,
                "createNewCopy": create_new_copy,
            }
        )
        scope = "playlist-import:create"
        with self.database.transaction(immediate=True) as connection:
            existing_id = self._idempotent_resource(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
            )
            if existing_id:
                row = self._get_owned(connection, "playlist_imports", existing_id, owner_id)
                return self._serialize_import(row)
            if not create_new_copy:
                duplicate = connection.execute(
                    "SELECT pi.* FROM playlist_imports pi "
                    "JOIN consent_decisions cd ON cd.id = pi.consent_decision_id "
                    "WHERE pi.owner_id = ? AND pi.file_hash = ? "
                    "AND pi.status NOT IN ('IMPORT_FAILED', 'CANCELLED', 'RAW_FILE_EXPIRED', 'DELETION_PENDING') "
                    "AND cd.decision = ? ORDER BY pi.created_at DESC LIMIT 1",
                    (owner_id, file_hash, "allow" if consent_external_processing else "deny"),
                ).fetchone()
                if duplicate:
                    self._record_idempotency(
                        connection,
                        owner_id=owner_id,
                        scope=scope,
                        key=key,
                        resource_id=duplicate["id"],
                        request_hash=request_hash,
                    )
                    result = self._serialize_import(duplicate)
                    result["duplicateDetected"] = True
                    return result

        object_ref = self.object_store.put(data, suffix=suffix)
        import_id = new_id("import")
        consent_id = new_id("consent")
        now = utc_now()
        try:
            with self.database.transaction(immediate=True) as connection:
                consent_provider = self.settings.model_provider if consent_external_processing else "none"
                connection.execute(
                    "INSERT INTO consent_decisions(id, owner_id, purpose, fields_json, provider, "
                    "region, version, decision, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        consent_id,
                        owner_id,
                        "playlist_mood_analysis",
                        canonical_json(
                            [
                                "genre",
                                "mood",
                                "tags",
                                "bpm",
                                "audioEnergy",
                                "audioBrightness",
                                "audioDynamics",
                                "audioPulse",
                                "tempoConfidence",
                                "audioFeatureVersion",
                            ]
                        ),
                        consent_provider,
                        None,
                        "consent@1.0.0",
                        "allow" if consent_external_processing else "deny",
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO playlist_imports(id, owner_id, object_ref, input_kind, "
                    "original_name_hash, file_hash, status, mapping_draft_json, headers_json, "
                    "parser_version, consent_decision_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        import_id,
                        owner_id,
                        object_ref,
                        input_kind,
                        sha256_bytes((filename or "pasted-text").encode()),
                        file_hash,
                        "MAPPING_REQUIRED",
                        canonical_json(suggest_mapping(table.headers)),
                        canonical_json(table.headers),
                        PARSER_VERSION,
                        consent_id,
                        now,
                        now,
                    ),
                )
                self._record_idempotency(
                    connection,
                    owner_id=owner_id,
                    scope=scope,
                    key=key,
                    resource_id=import_id,
                    request_hash=request_hash,
                )
        except Exception:
            self.object_store.delete(object_ref)
            raise
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM playlist_imports WHERE id = ?", (import_id,)).fetchone()
        record_audit(
            self.database,
            event_type="upload_completed",
            status="ok",
            owner_id=owner_id,
            request_id=request_id,
            resource_type="playlist_import",
            resource_id=import_id,
            detail={
                "inputKind": input_kind,
                "sizeBand": self._size_band(len(data)),
                **({"source": source_label} if source_label else {}),
            },
        )
        return self._serialize_import(row)

    @staticmethod
    def _size_band(size: int) -> str:
        if size < 100_000:
            return "lt_100kb"
        if size < 1_000_000:
            return "lt_1mb"
        return "gte_1mb"

    @staticmethod
    def _serialize_import(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "importId": row["id"],
            "status": row["status"],
            "headers": safe_json_loads(row["headers_json"], []),
            "mappingDraft": safe_json_loads(row["mapping_draft_json"], {}),
            "parserVersion": row["parser_version"],
            "consentId": row["consent_decision_id"],
            "failureCode": row["failure_code"],
            "createdAt": row["created_at"],
        }

    def get_import(self, owner_id: str, import_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = self._get_owned(connection, "playlist_imports", import_id, owner_id)
            if row["status"] == "DELETION_PENDING":
                raise AppError("DELETION_PENDING", "该歌单正在删除，已停止访问。", 410)
            result = self._serialize_import(row)
            latest = connection.execute(
                "SELECT * FROM playlist_versions WHERE import_id = ? ORDER BY version_number DESC LIMIT 1",
                (import_id,),
            ).fetchone()
            if latest:
                result["latestPlaylistVersion"] = self._serialize_playlist_version(connection, latest, include_preview=True)
            return result

    @staticmethod
    def _serialize_consent(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "consentId": row["id"],
            "purpose": row["purpose"],
            "fields": safe_json_loads(row["fields_json"], []),
            "provider": row["provider"],
            "region": row["region"],
            "version": row["version"],
            "decision": row["decision"],
            "createdAt": row["created_at"],
            "revokedAt": row["revoked_at"],
        }

    def get_consent(self, owner_id: str, consent_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            return self._serialize_consent(self._get_owned(connection, "consent_decisions", consent_id, owner_id))

    def revoke_consent(
        self,
        *,
        owner_id: str,
        consent_id: str,
        idempotency_key: str | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        key = self._require_idempotency_key(idempotency_key)
        request_hash = self._request_hash({"consentId": consent_id, "action": "revoke"})
        scope = f"consent:{consent_id}:revoke"
        with self.database.transaction(immediate=True) as connection:
            existing_id = self._idempotent_resource(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
            )
            if existing_id:
                return self._serialize_consent(
                    self._get_owned(connection, "consent_decisions", existing_id, owner_id)
                )
            row = self._get_owned(connection, "consent_decisions", consent_id, owner_id)
            if not row["revoked_at"]:
                connection.execute(
                    "UPDATE consent_decisions SET decision = 'deny', revoked_at = ? WHERE id = ?",
                    (utc_now(), consent_id),
                )
            self._record_idempotency(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                resource_id=consent_id,
                request_hash=request_hash,
            )
            result = self._serialize_consent(
                connection.execute("SELECT * FROM consent_decisions WHERE id = ?", (consent_id,)).fetchone()
            )
        record_audit(
            self.database,
            event_type="consent_revoked",
            status="ok",
            owner_id=owner_id,
            request_id=request_id,
            resource_type="consent_decision",
            resource_id=consent_id,
            detail={"purpose": result["purpose"]},
        )
        return result

    @staticmethod
    def _serialize_deletion(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        task = connection.execute("SELECT * FROM tasks WHERE id = ?", (row["task_id"],)).fetchone()
        return {
            "deletionId": row["id"],
            "targetType": row["target_type"],
            "status": row["status"],
            "errorCode": row["error_code"],
            "requestedAt": row["requested_at"],
            "completedAt": row["completed_at"],
            "task": MeloPetService._serialize_task(task) if task else None,
        }

    def request_import_deletion(
        self,
        *,
        owner_id: str,
        import_id: str,
        idempotency_key: str | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        key = self._require_idempotency_key(idempotency_key)
        target_hash = sha256_bytes(import_id.encode())
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM deletion_requests WHERE owner_id = ? AND target_type = 'playlist_import' "
                "AND target_id_hash = ? ORDER BY requested_at DESC LIMIT 1",
                (owner_id, target_hash),
            ).fetchone()
            if existing and existing["status"] in {"QUEUED", "DELETING_DATA", "DELETION_COMPLETED"}:
                return self._serialize_deletion(connection, existing)
            import_row = self._get_owned(connection, "playlist_imports", import_id, owner_id)
            consent_id = import_row["consent_decision_id"]
        task = self._create_task(
            owner_id=owner_id,
            task_type="DELETE_PLAYLIST_IMPORT",
            idempotency_key=key,
            input_payload={"importId": import_id, "consentId": consent_id},
            initial_node="N12_DELETE_DATA",
        )
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM deletion_requests WHERE task_id = ?", (task["taskId"],)
            ).fetchone()
            if not existing:
                deletion_id = new_id("deletion")
                connection.execute(
                    "INSERT INTO deletion_requests(id, owner_id, target_type, target_id_hash, task_id, "
                    "status, requested_at) VALUES (?, ?, 'playlist_import', ?, ?, 'QUEUED', ?)",
                    (deletion_id, owner_id, target_hash, task["taskId"], now),
                )
                connection.execute(
                    "UPDATE playlist_imports SET status = 'DELETION_PENDING', updated_at = ? "
                    "WHERE id = ? AND owner_id = ?",
                    (now, import_id, owner_id),
                )
                existing = connection.execute(
                    "SELECT * FROM deletion_requests WHERE id = ?", (deletion_id,)
                ).fetchone()
            result = self._serialize_deletion(connection, existing)
        record_audit(
            self.database,
            event_type="deletion_requested",
            status="ok",
            owner_id=owner_id,
            request_id=request_id,
            resource_type="deletion_request",
            resource_id=result["deletionId"],
            detail={"targetType": "playlist_import"},
        )
        return result

    def get_deletion(self, owner_id: str, deletion_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = self._get_owned(connection, "deletion_requests", deletion_id, owner_id)
            return self._serialize_deletion(connection, row)

    def save_mapping(
        self,
        *,
        owner_id: str,
        import_id: str,
        mapping: ColumnMapping,
        idempotency_key: str | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        key = self._require_idempotency_key(idempotency_key)
        request_hash = self._request_hash(mapping.model_dump(by_alias=True))
        scope = f"playlist-import:{import_id}:mapping"
        with self.database.connect() as connection:
            import_row = self._get_owned(connection, "playlist_imports", import_id, owner_id)
            if import_row["status"] == "DELETION_PENDING":
                raise AppError("DELETION_PENDING", "该歌单数据已停止访问或正在删除。", 410)
            if import_row["object_ref"].startswith("deleted:"):
                raise AppError("RAW_FILE_EXPIRED", "原始内容已到期清理，请重新导入。", 410)
            data = self.object_store.get(import_row["object_ref"])
        # Stale imports created before URL resolution shipped stored the raw
        # NetEase link as their content. Re-resolve here so mapping can recover.
        data, _source = self._resolve_playlist_source(data)
        table = parse_table(data, self.settings.playlist_max_tracks)
        normalized = normalize_tracks(table, mapping)
        if normalized.valid_count < 1:
            raise AppError("MAPPING_INVALID", "映射后没有有效歌曲，请检查歌名和艺人列。")

        with self.database.transaction(immediate=True) as connection:
            existing_id = self._idempotent_resource(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
            )
            if existing_id:
                row = self._get_owned(connection, "playlist_versions", existing_id, owner_id)
                return self._serialize_playlist_version(connection, row, include_preview=True)
            latest_number = connection.execute(
                "SELECT COALESCE(MAX(version_number), 0) AS value FROM playlist_versions WHERE import_id = ?",
                (import_id,),
            ).fetchone()["value"]
            playlist_version_id = new_id("playlist")
            now = utc_now()
            connection.execute(
                "INSERT INTO playlist_versions(id, import_id, owner_id, version_number, "
                "schema_version, normalized_count, invalid_count, duplicate_count, content_hash, "
                "mapping_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    playlist_version_id,
                    import_id,
                    owner_id,
                    latest_number + 1,
                    "playlist-schema@1.0.0",
                    normalized.valid_count,
                    normalized.invalid_count,
                    normalized.duplicate_count,
                    normalized.content_hash,
                    canonical_json(mapping.model_dump(by_alias=True)),
                    now,
                ),
            )
            connection.executemany(
                "INSERT INTO tracks(track_ref, playlist_version_id, owner_id, row_ref, title, artist, "
                "album, duration_ms, platform_track_id, allowed_signals_json, match_status, excluded, "
                "duplicate_of, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        track.track_ref,
                        playlist_version_id,
                        owner_id,
                        track.row_ref,
                        track.title,
                        track.artist,
                        track.album,
                        track.duration_ms,
                        track.platform_track_id,
                        canonical_json(track.allowed_signals),
                        track.match_status,
                        int(track.excluded),
                        track.duplicate_of,
                        now,
                    )
                    for track in normalized.tracks
                ],
            )
            connection.execute(
                "UPDATE playlist_imports SET status = 'READY_FOR_ANALYSIS', mapping_draft_json = ?, updated_at = ? WHERE id = ?",
                (canonical_json(mapping.model_dump(by_alias=True)), now, import_id),
            )
            self._record_idempotency(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                resource_id=playlist_version_id,
                request_hash=request_hash,
            )
            row = connection.execute("SELECT * FROM playlist_versions WHERE id = ?", (playlist_version_id,)).fetchone()
            result = self._serialize_playlist_version(connection, row, include_preview=True)
            result["issues"] = normalized.issues[:100]
        record_audit(
            self.database,
            event_type="mapping_saved",
            status="ok",
            owner_id=owner_id,
            request_id=request_id,
            resource_type="playlist_version",
            resource_id=playlist_version_id,
            detail={
                "validCount": normalized.valid_count,
                "invalidCount": normalized.invalid_count,
                "duplicateCount": normalized.duplicate_count,
            },
        )
        return result

    @staticmethod
    def _serialize_playlist_version(connection: sqlite3.Connection, row: sqlite3.Row, *, include_preview: bool) -> dict[str, Any]:
        result = {
            "playlistVersion": row["id"],
            "versionNumber": row["version_number"],
            "normalizedCount": row["normalized_count"],
            "invalidCount": row["invalid_count"],
            "duplicateCount": row["duplicate_count"],
            "schemaVersion": row["schema_version"],
            "lowSampleTrial": row["normalized_count"] < 10,
        }
        if include_preview:
            preview = connection.execute(
                "SELECT track_ref, row_ref, title, artist, allowed_signals_json, excluded, duplicate_of "
                "FROM tracks WHERE playlist_version_id = ? ORDER BY row_ref LIMIT 20",
                (row["id"],),
            ).fetchall()
            result["preview"] = [
                {
                    "trackRef": item["track_ref"],
                    "row": item["row_ref"],
                    "title": item["title"],
                    "artist": item["artist"],
                    "allowedSignals": safe_json_loads(item["allowed_signals_json"], {}),
                    "excluded": bool(item["excluded"]),
                    "duplicateOf": item["duplicate_of"],
                }
                for item in preview
            ]
        return result

    def _create_task(
        self,
        *,
        owner_id: str,
        task_type: str,
        idempotency_key: str | None,
        input_payload: dict[str, Any],
        initial_node: str,
    ) -> dict[str, Any]:
        key = self._require_idempotency_key(idempotency_key)
        request_hash = self._request_hash(input_payload)
        now = utc_now()
        scope = f"task:{task_type}:create"
        with self.database.transaction(immediate=True) as connection:
            aliased_id = self._idempotent_resource(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
            )
            if aliased_id:
                return self._serialize_task(self._get_owned(connection, "tasks", aliased_id, owner_id))
            existing = connection.execute(
                "SELECT * FROM tasks WHERE owner_id = ? AND task_type = ? AND idempotency_key = ?",
                (owner_id, task_type, key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise AppError(
                        "IDEMPOTENCY_CONFLICT",
                        "同一 Idempotency-Key 已用于不同任务。",
                        409,
                    )
                return self._serialize_task(existing)
            active_duplicate = connection.execute(
                "SELECT * FROM tasks WHERE owner_id = ? AND task_type = ? AND request_hash = ? "
                "AND status NOT IN ('PROFILE_READY','CANDIDATES_READY','DELETION_COMPLETED',"
                "'SAFE_FALLBACK_READY','FINAL_FAILED','CANCELLED') ORDER BY created_at LIMIT 1",
                (owner_id, task_type, request_hash),
            ).fetchone()
            if active_duplicate:
                self._record_idempotency(
                    connection,
                    owner_id=owner_id,
                    scope=scope,
                    key=key,
                    resource_id=active_duplicate["id"],
                    request_hash=request_hash,
                )
                return self._serialize_task(active_duplicate)
            task_id = new_id("task")
            run_id = new_id("run")
            connection.execute(
                "INSERT INTO tasks(id, run_id, task_type, owner_id, idempotency_key, request_hash, "
                "status, node, progress, input_json, output_json, journal_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, 0, ?, '{}', '[]', ?, ?)",
                (
                    task_id,
                    run_id,
                    task_type,
                    owner_id,
                    key,
                    request_hash,
                    initial_node,
                    canonical_json(input_payload),
                    now,
                    now,
                ),
            )
            self._record_idempotency(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                resource_id=task_id,
                request_hash=request_hash,
            )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return self._serialize_task(row)

    def create_analysis_task(
        self,
        *,
        owner_id: str,
        import_id: str,
        idempotency_key: str | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            import_row = self._get_owned(connection, "playlist_imports", import_id, owner_id)
            if import_row["status"] != "READY_FOR_ANALYSIS":
                raise AppError("MAPPING_REQUIRED", "请先完成并确认字段映射。", 409)
            playlist = connection.execute(
                "SELECT * FROM playlist_versions WHERE import_id = ? ORDER BY version_number DESC LIMIT 1",
                (import_id,),
            ).fetchone()
            if not playlist:
                raise AppError("MAPPING_REQUIRED", "请先完成并确认字段映射。", 409)
            consent = connection.execute(
                "SELECT * FROM consent_decisions WHERE id = ?", (import_row["consent_decision_id"],)
            ).fetchone()
        task = self._create_task(
            owner_id=owner_id,
            task_type="ANALYZE_PLAYLIST",
            idempotency_key=idempotency_key,
            input_payload={
                "playlistVersion": playlist["id"],
                "consentDecisionId": consent["id"],
                "externalProcessingAllowed": consent["decision"] == "allow" and not consent["revoked_at"],
            },
            initial_node="N04_RESOLVE_SIGNALS",
        )
        record_audit(
            self.database,
            event_type="analysis_created",
            status="ok",
            owner_id=owner_id,
            request_id=request_id,
            resource_type="task",
            resource_id=task["taskId"],
            detail={"taskType": "ANALYZE_PLAYLIST"},
        )
        return task

    @staticmethod
    def _serialize_task(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "taskId": row["id"],
            "runId": row["run_id"],
            "taskType": row["task_type"],
            "status": row["status"],
            "node": row["node"],
            "progress": row["progress"],
            "attempt": row["attempt"],
            "workflowVersion": row["workflow_version"],
            "heartbeatAt": row["heartbeat_at"],
            "errorCode": row["error_code"],
            "safeMessage": row["safe_message"],
            "retryAllowed": bool(row["retry_allowed"]),
            "output": safe_json_loads(row["output_json"], {}),
            "updatedAt": row["updated_at"],
        }

    def get_task(self, owner_id: str, task_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = self._get_owned(connection, "tasks", task_id, owner_id)
            return self._serialize_task(row)

    def worker_heartbeat(self, worker_id: str, process_id: int, *, status: str = "online") -> None:
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO worker_instances(worker_id, process_id, status, started_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(worker_id) DO UPDATE SET "
                "process_id = excluded.process_id, status = excluded.status, last_seen_at = excluded.last_seen_at",
                (worker_id, process_id, status, now, now),
            )

    def operational_status(self) -> dict[str, Any]:
        cutoff = (datetime.now(UTC) - timedelta(seconds=max(5.0, self.settings.worker_poll_seconds * 4))).isoformat(
            timespec="milliseconds"
        )
        with self.database.connect() as connection:
            connection.execute("SELECT 1").fetchone()
            active_workers = connection.execute(
                "SELECT COUNT(*) AS value FROM worker_instances "
                "WHERE status = 'online' AND last_seen_at >= ?",
                (cutoff,),
            ).fetchone()["value"]
            queued_tasks = connection.execute(
                "SELECT COUNT(*) AS value FROM tasks WHERE status IN ('QUEUED','RETRYABLE_FAILED')"
            ).fetchone()["value"]
        return {
            "database": "ready",
            "objectStore": "ready" if self.settings.object_path.is_dir() else "unavailable",
            "previewStore": "ready" if self.settings.preview_path.is_dir() else "unavailable",
            "activeWorkers": active_workers,
            "queuedTasks": queued_tasks,
        }

    def request_cancel(self, owner_id: str, task_id: str, idempotency_key: str | None) -> dict[str, Any]:
        key = self._require_idempotency_key(idempotency_key)
        request_hash = self._request_hash({"taskId": task_id, "action": "cancel"})
        scope = f"task:{task_id}:cancel"
        with self.database.transaction(immediate=True) as connection:
            existing_id = self._idempotent_resource(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
            )
            if existing_id:
                return self._serialize_task(self._get_owned(connection, "tasks", existing_id, owner_id))
            row = self._get_owned(connection, "tasks", task_id, owner_id)
            if row["task_type"] == "DELETE_PLAYLIST_IMPORT":
                raise AppError("DELETION_NOT_CANCELLABLE", "删除请求已生效，不能取消。", 409)
            if row["status"] not in TERMINAL_TASK_STATES:
                connection.execute(
                    "UPDATE tasks SET cancel_requested = 1, updated_at = ? WHERE id = ?",
                    (utc_now(), task_id),
                )
            self._record_idempotency(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                resource_id=task_id,
                request_hash=request_hash,
            )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return self._serialize_task(row)

    def retry_task(self, owner_id: str, task_id: str, idempotency_key: str | None) -> dict[str, Any]:
        key = self._require_idempotency_key(idempotency_key)
        request_hash = self._request_hash({"taskId": task_id, "action": "retry"})
        scope = f"task:{task_id}:retry"
        with self.database.transaction(immediate=True) as connection:
            existing_id = self._idempotent_resource(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
            )
            if existing_id:
                return self._serialize_task(self._get_owned(connection, "tasks", existing_id, owner_id))
            row = self._get_owned(connection, "tasks", task_id, owner_id)
            if row["status"] != "RETRYABLE_FAILED" or not row["retry_allowed"]:
                raise AppError("TASK_NOT_RETRYABLE", "当前任务不可重试。", 409)
            connection.execute(
                "UPDATE tasks SET status = 'QUEUED', error_code = NULL, safe_message = NULL, "
                "lease_owner = NULL, lease_until = NULL, updated_at = ? WHERE id = ?",
                (utc_now(), task_id),
            )
            self._record_idempotency(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                resource_id=task_id,
                request_hash=request_hash,
            )
            return self._serialize_task(connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())

    def claim_task(self, worker_id: str) -> sqlite3.Row | None:
        now = utc_now()
        lease_until = (datetime.now(UTC) + timedelta(seconds=self.settings.worker_lease_seconds)).isoformat(
            timespec="milliseconds"
        )
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE cancel_requested = 0 AND "
                "((status IN ('QUEUED', 'RETRYABLE_FAILED') "
                "AND (lease_until IS NULL OR lease_until < ?)) OR "
                "(status IN ('RESOLVING_SIGNALS','CLASSIFYING','GUARDING','COMPUTING',"
                "'MAPPING_PET','GENERATING_COPY','GENERATING_ACTIONS','VALIDATING_ASSETS','RENDERING',"
                "'DELETING_DATA') "
                "AND lease_until < ?)) "
                "ORDER BY created_at LIMIT 1",
                (now, now),
            ).fetchone()
            if row is None:
                cancelled = connection.execute(
                    "SELECT id FROM tasks WHERE cancel_requested = 1 AND status NOT IN "
                    "('PROFILE_READY','CANDIDATES_READY','DELETION_COMPLETED','SAFE_FALLBACK_READY',"
                    "'FINAL_FAILED','CANCELLED') "
                    "ORDER BY created_at LIMIT 1"
                ).fetchone()
                if cancelled:
                    connection.execute(
                        "UPDATE tasks SET status = 'CANCELLED', node = 'CANCELLED', progress = 100, "
                        "retry_allowed = 0, lease_owner = NULL, lease_until = NULL, updated_at = ? "
                        "WHERE id = ?",
                        (now, cancelled["id"]),
                    )
                return None
            attempt = int(row["attempt"]) + 1
            if attempt > 3:
                connection.execute(
                    "UPDATE tasks SET status = 'FINAL_FAILED', error_code = 'MAX_ATTEMPTS_EXCEEDED', "
                    "safe_message = '任务多次失败，已安全停止。', retry_allowed = 0, "
                    "lease_owner = NULL, lease_until = NULL, updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                return None
            connection.execute(
                "UPDATE tasks SET attempt = ?, lease_owner = ?, lease_until = ?, heartbeat_at = ?, "
                "updated_at = ? WHERE id = ?",
                (attempt, worker_id, lease_until, now, now, row["id"]),
            )
            return connection.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()

    def _task_transition(
        self,
        task_id: str,
        *,
        status: str,
        node: str,
        progress: int,
        output: dict[str, Any] | None = None,
        error_code: str | None = None,
        safe_message: str | None = None,
        retry_allowed: bool = True,
    ) -> None:
        now = utc_now()
        lease_until = (datetime.now(UTC) + timedelta(seconds=self.settings.worker_lease_seconds)).isoformat(
            timespec="milliseconds"
        )
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                raise RuntimeError("task disappeared")
            if row["status"] in TERMINAL_TASK_STATES and status != row["status"]:
                raise RuntimeError("TERMINAL_TASK_TRANSITION_REJECTED")
            journal = safe_json_loads(row["journal_json"], [])
            journal.append(
                {
                    "node": node,
                    "status": status,
                    "progress": progress,
                    "attempt": row["attempt"],
                    "at": now,
                }
            )
            terminal = status in TERMINAL_TASK_STATES or status == "RETRYABLE_FAILED"
            connection.execute(
                "UPDATE tasks SET status = ?, node = ?, progress = ?, output_json = ?, "
                "journal_json = ?, error_code = ?, safe_message = ?, retry_allowed = ?, "
                "lease_owner = CASE WHEN ? THEN NULL ELSE lease_owner END, "
                "lease_until = CASE WHEN ? THEN NULL ELSE ? END, heartbeat_at = ?, updated_at = ? WHERE id = ?",
                (
                    status,
                    node,
                    progress,
                    canonical_json(output if output is not None else safe_json_loads(row["output_json"], {})),
                    canonical_json(journal[-100:]),
                    error_code,
                    safe_message,
                    int(retry_allowed),
                    int(terminal),
                    int(terminal),
                    lease_until,
                    now,
                    now,
                    task_id,
                ),
            )

    def _stop_if_cancelled(self, task_id: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested, status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row and row["cancel_requested"] and row["status"] not in TERMINAL_TASK_STATES:
            self._task_transition(
                task_id,
                status="CANCELLED",
                node="CANCELLED",
                progress=100,
                retry_allowed=False,
            )
            return True
        return False

    def _external_processing_allowed(self, consent_id: str | None) -> bool:
        if not consent_id or not self.settings.model_enabled:
            return False
        with self.database.connect() as connection:
            consent = connection.execute(
                "SELECT decision, revoked_at FROM consent_decisions WHERE id = ?", (consent_id,)
            ).fetchone()
        return bool(consent and consent["decision"] == "allow" and not consent["revoked_at"])

    def _model_budget_available(self, owner_id: str) -> bool:
        if not self.settings.model_enabled or self.settings.model_daily_budget <= 0:
            return False
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="milliseconds")
        with self.database.connect() as connection:
            spent = connection.execute(
                "SELECT COALESCE(SUM(estimated_cost), 0) AS value FROM model_calls WHERE owner_id = ? AND created_at >= ?",
                (owner_id, day_start),
            ).fetchone()["value"]
        return float(spent) < self.settings.model_daily_budget

    def _record_model_call(
        self,
        *,
        task_id: str,
        owner_id: str,
        node: str,
        result: ModelCallResult | None,
        call_key: str,
    ) -> None:
        if result is None:
            return
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO model_calls(id, task_id, owner_id, node, provider, model_id, "
                "prompt_version, prompt_hash, input_tokens, output_tokens, estimated_cost, "
                "latency_ms, status, created_at, call_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("modelcall"),
                    task_id,
                    owner_id,
                    node,
                    result.provider,
                    result.model,
                    result.prompt_version,
                    result.prompt_hash,
                    result.input_tokens,
                    result.output_tokens,
                    result.estimated_cost,
                    result.latency_ms,
                    result.status,
                    utc_now(),
                    call_key,
                ),
            )

    def process_task(self, row: sqlite3.Row) -> None:
        try:
            if row["task_type"] == "ANALYZE_PLAYLIST":
                self._process_analysis(row)
            elif row["task_type"] == "GENERATE_PET_CANDIDATES":
                self._process_pet_candidates(row)
            elif row["task_type"] == "GENERATE_PET_ACTIONS":
                self._process_pet_actions(row)
            elif row["task_type"] == "DELETE_PLAYLIST_IMPORT":
                self._process_import_deletion(row)
            else:
                raise ValueError("UNSUPPORTED_TASK_TYPE")
        except AppError as exc:
            self._fail_task(row["id"], exc.error_code, exc.safe_message, retryable=exc.status_code >= 500)
        except (OSError, sqlite3.OperationalError, TimeoutError) as exc:
            self._fail_task(
                row["id"],
                type(exc).__name__.upper(),
                "后台任务暂时失败，可稍后重试。",
                retryable=True,
            )
        except Exception as exc:
            code = str(exc) if str(exc).isupper() and len(str(exc)) < 80 else type(exc).__name__.upper()
            self._fail_task(row["id"], code, "后台任务未能完成，已安全停止。", retryable=False)

    def _fail_task(self, task_id: str, code: str, message: str, *, retryable: bool) -> None:
        self._task_transition(
            task_id,
            status="RETRYABLE_FAILED" if retryable else "FINAL_FAILED",
            node="FAILED",
            progress=100 if not retryable else 0,
            error_code=code,
            safe_message=message,
            retry_allowed=retryable,
        )
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE deletion_requests SET status = ?, error_code = ? WHERE task_id = ?",
                ("RETRYABLE_FAILED" if retryable else "FINAL_FAILED", code, task_id),
            )

    def _load_tracks_for_analysis(self, playlist_version: str, owner_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            playlist = self._get_owned(connection, "playlist_versions", playlist_version, owner_id)
            lineage = connection.execute(
                "SELECT pi.status FROM playlist_imports pi WHERE pi.id = ?",
                (playlist["import_id"],),
            ).fetchone()
            if not lineage or lineage["status"] == "DELETION_PENDING":
                raise AppError("DELETION_PENDING", "该歌单数据已停止访问或正在删除。", 410)
            rows = connection.execute(
                "SELECT * FROM tracks WHERE playlist_version_id = ? ORDER BY row_ref",
                (playlist["id"],),
            ).fetchall()
        return [
            {
                "trackRef": item["track_ref"],
                "title": item["title"],
                "artist": item["artist"],
                "allowedSignals": safe_json_loads(item["allowed_signals_json"], {}),
                "excluded": bool(item["excluded"]),
            }
            for item in rows
        ]

    def _persist_signal_packages(
        self,
        *,
        task_id: str,
        owner_id: str,
        consent_id: str,
        tracks: list[dict[str, Any]],
    ) -> None:
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO signal_packages(id, task_id, owner_id, track_ref, source_mode, "
                "allowed_signals_json, source_refs_json, provider, provider_version, consent_decision_id, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        new_id("signal"),
                        task_id,
                        owner_id,
                        track["trackRef"],
                        "uploaded_signals" if track["allowedSignals"] else "manual_only",
                        canonical_json(track["allowedSignals"]),
                        canonical_json(
                            [f"upload:{track['trackRef']}:{field}" for field in track["allowedSignals"]]
                        ),
                        "upload" if track["allowedSignals"] else "none",
                        PARSER_VERSION if track["allowedSignals"] else "none",
                        consent_id,
                        now,
                    )
                    for track in tracks
                ],
            )

    def _process_analysis(self, task: sqlite3.Row) -> None:
        task_id = task["id"]
        input_payload = safe_json_loads(task["input_json"], {})
        playlist_version = input_payload["playlistVersion"]
        owner_id = task["owner_id"]
        with self.database.connect() as connection:
            existing = connection.execute("SELECT revision FROM mood_profiles WHERE source_task_id = ?", (task_id,)).fetchone()
        if existing:
            self._task_transition(
                task_id,
                status="PROFILE_READY",
                node="N07_COMPUTE_PROFILE",
                progress=100,
                output={"profileRevision": existing["revision"], "recovered": True},
                retry_allowed=False,
            )
            return
        self._task_transition(task_id, status="RESOLVING_SIGNALS", node="N04_RESOLVE_SIGNALS", progress=10)
        tracks = self._load_tracks_for_analysis(playlist_version, owner_id)
        if not tracks:
            raise AppError("NO_VALID_TRACKS", "没有可用于分析的歌曲。")
        consent_id = str(input_payload["consentDecisionId"])
        self._persist_signal_packages(
            task_id=task_id,
            owner_id=owner_id,
            consent_id=consent_id,
            tracks=tracks,
        )
        if self._stop_if_cancelled(task_id):
            return
        self._task_transition(task_id, status="CLASSIFYING", node="N05_CLASSIFY_MOOD", progress=35)
        gateway = (
            self.model_gateway
            if self._external_processing_allowed(consent_id) and self._model_budget_available(owner_id)
            else ModelGateway()
        )
        model_call_key = f"{task_id}:mood"
        batch = classify_tracks(
            tracks,
            self.catalogs,
            gateway,
            model_idempotency_key=model_call_key,
        )
        self._record_model_call(
            task_id=task_id,
            owner_id=owner_id,
            node="N05_CLASSIFY_MOOD",
            result=batch.model_call,
            call_key=model_call_key,
        )
        if self._stop_if_cancelled(task_id):
            return
        self._task_transition(task_id, status="GUARDING", node="N06_CLASSIFICATION_GUARD", progress=60)
        if self._stop_if_cancelled(task_id):
            return
        self._task_transition(task_id, status="COMPUTING", node="N07_COMPUTE_PROFILE", progress=80)
        computed = compute_profile(batch.tracks, self.catalogs.taxonomy_order)
        now = utc_now()
        revision = new_id("profile")
        model_version = batch.model_call.model if batch.model_call else "rule-only"
        prompt_version = batch.model_call.prompt_version if batch.model_call else "none"
        with self.database.transaction(immediate=True) as connection:
            revision_number = connection.execute(
                "SELECT COALESCE(MAX(revision_number), 0) + 1 AS value FROM mood_profiles WHERE playlist_version_id = ?",
                (playlist_version,),
            ).fetchone()["value"]
            connection.execute(
                "INSERT INTO mood_profiles(revision, owner_id, playlist_version_id, source_task_id, "
                "revision_number, taxonomy_version, model_version, prompt_version, rule_version, "
                "counts_json, percentages_json, known_count, unknown_count, excluded_count, "
                "normalized_total, quality_flags_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    revision,
                    owner_id,
                    playlist_version,
                    task_id,
                    revision_number,
                    self.catalogs.taxonomy_version,
                    model_version,
                    prompt_version,
                    "mood-guard@1.2.0",
                    canonical_json(computed.counts),
                    canonical_json(computed.percentages),
                    computed.known,
                    computed.unknown,
                    computed.excluded,
                    computed.normalized_total,
                    canonical_json(computed.quality_flags),
                    now,
                ),
            )
            connection.executemany(
                "INSERT INTO profile_tracks(profile_revision, track_ref, auto_mood_id, final_mood_id, "
                "confidence_band, reason_code, source_mode, source_refs_json, evidence_json, excluded, "
                "correction_source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        revision,
                        item.track_ref,
                        item.auto_mood_id,
                        item.final_mood_id,
                        item.confidence_band,
                        item.reason_code,
                        item.source_mode,
                        canonical_json(item.source_refs),
                        canonical_json(item.evidence),
                        int(item.excluded),
                        item.correction_source,
                    )
                    for item in batch.tracks
                ],
            )
        self._task_transition(
            task_id,
            status="PROFILE_READY",
            node="N07_COMPUTE_PROFILE",
            progress=100,
            output={
                "profileRevision": revision,
                "modelStatus": batch.model_call.status if batch.model_call else "rule-only",
                "knownCount": computed.known,
                "unknownCount": computed.unknown,
            },
            retry_allowed=False,
        )

    def _serialize_profile_row(self, row: sqlite3.Row) -> dict[str, Any]:
        active = row["known_count"] + row["unknown_count"]
        return {
            "revision": row["revision"],
            "playlistVersion": row["playlist_version_id"],
            "parentRevision": row["parent_revision"],
            "revisionNumber": row["revision_number"],
            "taxonomyVersion": row["taxonomy_version"],
            "modelVersion": row["model_version"],
            "promptVersion": row["prompt_version"],
            "ruleVersion": row["rule_version"],
            "counts": safe_json_loads(row["counts_json"], {}),
            "percentages": safe_json_loads(row["percentages_json"], []),
            "knownCount": row["known_count"],
            "unknownCount": row["unknown_count"],
            "excludedCount": row["excluded_count"],
            "normalizedTotal": row["normalized_total"],
            "unknownRate": row["unknown_count"] / active if active else 1,
            "qualityFlags": safe_json_loads(row["quality_flags_json"], []),
            "confirmedAt": row["confirmed_at"],
            "createdAt": row["created_at"],
        }

    @staticmethod
    def _assert_profile_available(connection: sqlite3.Connection, profile_row: sqlite3.Row) -> None:
        lineage = connection.execute(
            "SELECT pi.status FROM playlist_imports pi "
            "JOIN playlist_versions pv ON pv.import_id = pi.id WHERE pv.id = ?",
            (profile_row["playlist_version_id"],),
        ).fetchone()
        if not lineage or lineage["status"] == "DELETION_PENDING":
            raise AppError("DELETION_PENDING", "该歌单数据已停止访问或正在删除。", 410)

    def get_profile(self, owner_id: str, revision: str, *, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        page = max(1, page)
        page_size = max(1, min(200, page_size))
        with self.database.connect() as connection:
            row = self._get_owned(connection, "mood_profiles", revision, owner_id)
            self._assert_profile_available(connection, row)
            result = self._serialize_profile_row(row)
            track_rows = connection.execute(
                "SELECT pt.*, t.row_ref, t.title, t.artist FROM profile_tracks pt "
                "JOIN tracks t ON t.track_ref = pt.track_ref WHERE pt.profile_revision = ? "
                "ORDER BY t.row_ref LIMIT ? OFFSET ?",
                (revision, page_size, (page - 1) * page_size),
            ).fetchall()
            result["tracks"] = [
                {
                    "trackRef": item["track_ref"],
                    "row": item["row_ref"],
                    "title": item["title"],
                    "artist": item["artist"],
                    "autoMoodId": item["auto_mood_id"],
                    "finalMoodId": item["final_mood_id"],
                    "confidenceBand": item["confidence_band"],
                    "reasonCode": item["reason_code"],
                    "sourceMode": item["source_mode"],
                    "sourceRefs": safe_json_loads(item["source_refs_json"], []),
                    "evidence": safe_json_loads(item["evidence_json"], []),
                    "excluded": bool(item["excluded"]),
                    "correctionSource": item["correction_source"],
                }
                for item in track_rows
            ]
            representative = connection.execute(
                "SELECT t.title, t.artist FROM tracks t "
                "JOIN profile_tracks pt ON pt.track_ref = t.track_ref "
                "WHERE pt.profile_revision = ? AND pt.excluded = 0 "
                "ORDER BY t.row_ref LIMIT 5",
                (revision,),
            ).fetchall()
            result["dna"] = build_dna(
                percentages=result["percentages"],
                representative_tracks=[
                    {"title": item["title"], "artist": item["artist"]} for item in representative
                ],
                catalogs=self.catalogs,
            )
            result["page"] = page
            result["pageSize"] = page_size
            return result

    def correct_profile(
        self,
        *,
        owner_id: str,
        revision: str,
        request: CorrectionsRequest,
        idempotency_key: str | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        key = self._require_idempotency_key(idempotency_key)
        if request.expected_revision != revision:
            raise AppError("REVISION_CONFLICT", "画像版本已变化，请刷新后比较。", 409)
        request_hash = self._request_hash(request.model_dump(by_alias=True))
        scope = f"profile:{revision}:corrections"
        with self.database.transaction(immediate=True) as connection:
            existing_id = self._idempotent_resource(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
            )
            if existing_id:
                row = self._get_owned(connection, "mood_profiles", existing_id, owner_id)
                return self._serialize_profile_row(row)
            source = self._get_owned(connection, "mood_profiles", revision, owner_id)
            self._assert_profile_available(connection, source)
            latest = connection.execute(
                "SELECT revision FROM mood_profiles WHERE playlist_version_id = ? ORDER BY revision_number DESC LIMIT 1",
                (source["playlist_version_id"],),
            ).fetchone()
            if latest["revision"] != revision:
                raise AppError("REVISION_CONFLICT", "画像已有更新，请刷新后比较。", 409)
            track_rows = connection.execute("SELECT * FROM profile_tracks WHERE profile_revision = ?", (revision,)).fetchall()
            tracks = {
                item["track_ref"]: ClassifiedTrack(
                    item["track_ref"],
                    item["auto_mood_id"],
                    item["final_mood_id"],
                    item["confidence_band"],
                    item["reason_code"],
                    item["source_mode"],
                    safe_json_loads(item["source_refs_json"], []),
                    safe_json_loads(item["evidence_json"], []),
                    bool(item["excluded"]),
                    item["correction_source"],
                )
                for item in track_rows
            }
            for correction in request.corrections:
                track = tracks.get(correction.track_ref)
                if track is None:
                    raise AppError("TRACK_NOT_FOUND", "纠正项包含不属于当前画像的歌曲。", 404)
                if correction.action == "set_mood":
                    if correction.mood_id not in self.catalogs.taxonomy_by_id:
                        raise AppError("MOOD_NOT_SUPPORTED", "选择的氛围标签不受支持。")
                    track.final_mood_id = correction.mood_id or "UNKNOWN"
                    track.excluded = False
                    track.confidence_band = "高"
                    track.reason_code = "USER_CORRECTED"
                    track.source_mode = "user_corrected"
                    track.source_refs = [
                        *[ref for ref in track.source_refs if not ref.startswith("user:")],
                        f"user:{revision}",
                    ]
                    track.evidence = [
                        *[item for item in track.evidence if item != "user:explicit_correction"],
                        "user:explicit_correction",
                    ]
                    track.correction_source = "user"
                elif correction.action == "exclude":
                    track.excluded = True
                    track.reason_code = "USER_EXCLUDED"
                    track.correction_source = "user"
                elif correction.action == "include":
                    track.excluded = False
                    track.correction_source = "user"
                elif correction.action == "restore_auto":
                    track.final_mood_id = track.auto_mood_id
                    track.excluded = False
                    track.reason_code = "RESTORED_AUTO"
                    track.source_refs = [ref for ref in track.source_refs if not ref.startswith("user:")]
                    track.evidence = [item for item in track.evidence if item != "user:explicit_correction"]
                    track.source_mode = (
                        "uploaded_signals" if any(ref.startswith("upload:") for ref in track.source_refs) else "manual_only"
                    )
                    track.correction_source = "automatic"
            ordered = list(tracks.values())
            computed = compute_profile(ordered, self.catalogs.taxonomy_order)
            new_revision = new_id("profile")
            now = utc_now()
            revision_number = source["revision_number"] + 1
            connection.execute(
                "INSERT INTO mood_profiles(revision, owner_id, playlist_version_id, parent_revision, "
                "revision_number, taxonomy_version, model_version, prompt_version, rule_version, "
                "counts_json, percentages_json, known_count, unknown_count, excluded_count, "
                "normalized_total, quality_flags_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_revision,
                    owner_id,
                    source["playlist_version_id"],
                    revision,
                    revision_number,
                    source["taxonomy_version"],
                    source["model_version"],
                    source["prompt_version"],
                    source["rule_version"],
                    canonical_json(computed.counts),
                    canonical_json(computed.percentages),
                    computed.known,
                    computed.unknown,
                    computed.excluded,
                    computed.normalized_total,
                    canonical_json(computed.quality_flags),
                    now,
                ),
            )
            connection.executemany(
                "INSERT INTO profile_tracks(profile_revision, track_ref, auto_mood_id, final_mood_id, "
                "confidence_band, reason_code, source_mode, source_refs_json, evidence_json, excluded, "
                "correction_source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        new_revision,
                        item.track_ref,
                        item.auto_mood_id,
                        item.final_mood_id,
                        item.confidence_band,
                        item.reason_code,
                        item.source_mode,
                        canonical_json(item.source_refs),
                        canonical_json(item.evidence),
                        int(item.excluded),
                        item.correction_source,
                    )
                    for item in ordered
                ],
            )
            self._record_idempotency(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                resource_id=new_revision,
                request_hash=request_hash,
            )
            result = self._serialize_profile_row(
                connection.execute("SELECT * FROM mood_profiles WHERE revision = ?", (new_revision,)).fetchone()
            )
        record_audit(
            self.database,
            event_type="track_labels_corrected",
            status="ok",
            owner_id=owner_id,
            request_id=request_id,
            resource_type="mood_profile",
            resource_id=new_revision,
            detail={"correctionCount": len(request.corrections)},
        )
        return result

    def confirm_profile(
        self,
        *,
        owner_id: str,
        revision: str,
        idempotency_key: str | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        key = self._require_idempotency_key(idempotency_key)
        request_hash = self._request_hash({"revision": revision, "action": "confirm"})
        scope = f"profile:{revision}:confirm"
        with self.database.transaction(immediate=True) as connection:
            existing_id = self._idempotent_resource(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
            )
            if existing_id:
                return self._serialize_profile_row(self._get_owned(connection, "mood_profiles", existing_id, owner_id))
            row = self._get_owned(connection, "mood_profiles", revision, owner_id)
            self._assert_profile_available(connection, row)
            if row["confirmed_at"]:
                self._record_idempotency(
                    connection,
                    owner_id=owner_id,
                    scope=scope,
                    key=key,
                    resource_id=revision,
                    request_hash=request_hash,
                )
                return self._serialize_profile_row(row)
            active = row["known_count"] + row["unknown_count"]
            if active == 0 or row["unknown_count"] / active > 0.5:
                raise AppError(
                    "LOW_SIGNAL_BLOCKED",
                    "超过一半歌曲缺少可靠信号，请先纠正歌曲或补充允许信号。",
                    409,
                )
            if row["known_count"] < 10:
                raise AppError(
                    "LOW_SAMPLE_BLOCKED",
                    "可识别歌曲少于 10 首，当前只能查看试玩画像，不能生成正式候选。",
                    409,
                )
            latest = connection.execute(
                "SELECT revision FROM mood_profiles WHERE playlist_version_id = ? ORDER BY revision_number DESC LIMIT 1",
                (row["playlist_version_id"],),
            ).fetchone()
            if latest["revision"] != revision:
                raise AppError("REVISION_CONFLICT", "画像已有更新，请确认最新版本。", 409)
            confirmed_at = utc_now()
            connection.execute(
                "UPDATE mood_profiles SET confirmed_at = ? WHERE revision = ?",
                (confirmed_at, revision),
            )
            self._record_idempotency(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                resource_id=revision,
                request_hash=request_hash,
            )
            result = self._serialize_profile_row(
                connection.execute("SELECT * FROM mood_profiles WHERE revision = ?", (revision,)).fetchone()
            )
        record_audit(
            self.database,
            event_type="profile_confirmed",
            status="ok",
            owner_id=owner_id,
            request_id=request_id,
            resource_type="mood_profile",
            resource_id=revision,
        )
        return result

    def create_pet_task(
        self,
        *,
        owner_id: str,
        revision: str,
        idempotency_key: str | None,
        request_id: str | None = None,
        customization: PetCustomization | None = None,
    ) -> dict[str, Any]:
        validate_customization(customization, self.catalogs)
        with self.database.connect() as connection:
            row = self._get_owned(connection, "mood_profiles", revision, owner_id)
            self._assert_profile_available(connection, row)
            if not row["confirmed_at"]:
                raise AppError("PROFILE_NOT_CONFIRMED", "请先确认画像。", 409)
            if row["known_count"] < 10:
                raise AppError("LOW_SAMPLE_BLOCKED", "可识别歌曲少于 10 首，不能生成正式候选。", 409)
            active = row["known_count"] + row["unknown_count"]
            if active == 0 or row["unknown_count"] / active > 0.5:
                raise AppError("LOW_SIGNAL_BLOCKED", "超过一半歌曲缺少可靠信号。", 409)
            consent = connection.execute(
                "SELECT cd.* FROM consent_decisions cd "
                "JOIN playlist_imports pi ON pi.consent_decision_id = cd.id "
                "JOIN playlist_versions pv ON pv.import_id = pi.id "
                "WHERE pv.id = ?",
                (row["playlist_version_id"],),
            ).fetchone()
        task = self._create_task(
            owner_id=owner_id,
            task_type="GENERATE_PET_CANDIDATES",
            idempotency_key=idempotency_key,
            input_payload={
                "profileRevision": revision,
                "consentDecisionId": consent["id"] if consent else None,
                "externalProcessingAllowed": bool(consent and consent["decision"] == "allow" and not consent["revoked_at"]),
                "customization": customization.model_dump(by_alias=True, exclude_none=True) if customization else None,
            },
            initial_node="N09_MAP_PET_SPEC",
        )
        record_audit(
            self.database,
            event_type="pet_generation_created",
            status="ok",
            owner_id=owner_id,
            request_id=request_id,
            resource_type="task",
            resource_id=task["taskId"],
        )
        return task

    def _process_pet_candidates(self, task: sqlite3.Row) -> None:
        task_id = task["id"]
        owner_id = task["owner_id"]
        input_payload = safe_json_loads(task["input_json"], {})
        revision = input_payload["profileRevision"]
        customization = None
        if input_payload.get("customization"):
            customization = PetCustomization(**input_payload["customization"])
        with self.database.connect() as connection:
            profile_row = self._get_owned(connection, "mood_profiles", revision, owner_id)
            self._assert_profile_available(connection, profile_row)
            playlist = connection.execute(
                "SELECT * FROM playlist_versions WHERE id = ?",
                (profile_row["playlist_version_id"],),
            ).fetchone()
            existing = connection.execute(
                "SELECT COUNT(*) AS value FROM pet_candidates WHERE task_id = ?", (task_id,)
            ).fetchone()["value"]
        if existing == 3:
            self._task_transition(
                task_id,
                status="CANDIDATES_READY",
                node="N11_RENDER",
                progress=100,
                output={"candidateCount": 3, "recovered": True},
                retry_allowed=False,
            )
            return
        profile = self._serialize_profile_row(profile_row)
        self._task_transition(task_id, status="MAPPING_PET", node="N09_MAP_PET_SPEC", progress=20)
        for index in range(3):
            if self._stop_if_cancelled(task_id):
                return
            with self.database.connect() as connection:
                present = connection.execute(
                    "SELECT id FROM pet_candidates WHERE task_id = ? AND candidate_index = ?",
                    (task_id, index),
                ).fetchone()
            if present:
                continue
            self._task_transition(
                task_id,
                status="GENERATING_COPY",
                node="N10_GENERATE_COPY",
                progress=35 + index * 15,
            )
            model_call_key = f"{task_id}:copy:{index}"
            result, _model_call = create_candidate(
                profile=profile,
                playlist_hash=playlist["content_hash"],
                candidate_index=index,
                catalogs=self.catalogs,
                gateway=(
                    self.model_gateway
                    if self._external_processing_allowed(input_payload.get("consentDecisionId"))
                    and self._model_budget_available(owner_id)
                    else ModelGateway()
                ),
                settings=self.settings,
                model_idempotency_key=model_call_key,
                customization=customization,
            )
            self._record_model_call(
                task_id=task_id,
                owner_id=owner_id,
                node="N10_GENERATE_COPY",
                result=_model_call,
                call_key=model_call_key,
            )
            if self._stop_if_cancelled(task_id):
                self.preview_store.delete(result.preview_ref)
                return
            self._task_transition(
                task_id,
                status="VALIDATING_ASSETS",
                node="N11_VALIDATE_ASSETS",
                progress=45 + index * 15,
            )
            self._task_transition(
                task_id,
                status="RENDERING",
                node="N11_RENDER",
                progress=50 + index * 15,
            )
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO pet_candidates(id, task_id, owner_id, profile_revision, "
                    "candidate_index, spec_json, copy_json, preview_ref, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id("candidate"),
                        task_id,
                        owner_id,
                        revision,
                        index,
                        canonical_json(result.spec),
                        canonical_json(result.copy),
                        result.preview_ref,
                        utc_now(),
                    ),
                )
        self._task_transition(
            task_id,
            status="CANDIDATES_READY",
            node="N11_RENDER",
            progress=100,
            output={
                "candidateCount": 3,
                "assetCatalogVersion": self.catalogs.asset_catalog_version,
                "developmentAssets": not self.catalogs.production_eligible,
            },
            retry_allowed=False,
        )

    def _process_import_deletion(self, task: sqlite3.Row) -> None:
        task_id = task["id"]
        owner_id = task["owner_id"]
        input_payload = safe_json_loads(task["input_json"], {})
        import_id = str(input_payload.get("importId", ""))
        deletion = None
        with self.database.connect() as connection:
            deletion = connection.execute(
                "SELECT * FROM deletion_requests WHERE task_id = ? AND owner_id = ?",
                (task_id, owner_id),
            ).fetchone()
            import_row = connection.execute(
                "SELECT * FROM playlist_imports WHERE id = ? AND owner_id = ?",
                (import_id, owner_id),
            ).fetchone()
        if not deletion:
            raise AppError("DELETION_REQUEST_MISSING", "删除任务缺少持久化记录。", 500)
        self._task_transition(
            task_id,
            status="DELETING_DATA",
            node="N12_DELETE_DATA",
            progress=10,
        )
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE deletion_requests SET status = 'DELETING_DATA', error_code = NULL WHERE id = ?",
                (deletion["id"],),
            )
        if import_row is None:
            completed_at = utc_now()
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE deletion_requests SET status = 'DELETION_COMPLETED', completed_at = ? WHERE id = ?",
                    (completed_at, deletion["id"]),
                )
            self._task_transition(
                task_id,
                status="DELETION_COMPLETED",
                node="N13_DELETE_COMPLETE",
                progress=100,
                output={"deletionId": deletion["id"], "recovered": True},
                retry_allowed=False,
            )
            return

        with self.database.connect() as connection:
            playlist_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM playlist_versions WHERE import_id = ?", (import_id,)
                )
            ]
            revision_rows = []
            for playlist_id in playlist_ids:
                revision_rows.extend(
                    connection.execute(
                        "SELECT revision, source_task_id, revision_number FROM mood_profiles "
                        "WHERE playlist_version_id = ? ORDER BY revision_number DESC",
                        (playlist_id,),
                    ).fetchall()
                )
            revisions = [row["revision"] for row in revision_rows]
            related_task_ids = {row["source_task_id"] for row in revision_rows if row["source_task_id"]}
            preview_refs: list[str] = []
            candidate_record_ids: list[str] = []
            candidate_rows = []
            for revision in revisions:
                candidate_rows.extend(
                    connection.execute(
                        "SELECT id, task_id, preview_ref FROM pet_candidates WHERE profile_revision = ?",
                        (revision,),
                    ).fetchall()
                )
            if candidate_rows:
                related_task_ids.update(row["task_id"] for row in candidate_rows)
                preview_refs = [row["preview_ref"] for row in candidate_rows]
                candidate_record_ids = [row["id"] for row in candidate_rows]
                action_rows = []
                for candidate_record_id in candidate_record_ids:
                    action_rows.extend(
                        connection.execute(
                            "SELECT task_id, preview_ref FROM pet_actions WHERE candidate_id = ?",
                            (candidate_record_id,),
                        ).fetchall()
                    )
                related_task_ids.update(row["task_id"] for row in action_rows)
                preview_refs.extend(row["preview_ref"] for row in action_rows)
                preview_refs = list(dict.fromkeys(preview_refs))
            for row in connection.execute("SELECT id, input_json FROM tasks WHERE owner_id = ?", (owner_id,)):
                payload = safe_json_loads(row["input_json"], {})
                if (
                    payload.get("playlistVersion") in playlist_ids
                    or payload.get("profileRevision") in revisions
                    or payload.get("candidateId") in candidate_record_ids
                ):
                    related_task_ids.add(row["id"])
            selection_ids = []
            for related_task_id in related_task_ids:
                selection_ids.extend(
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM pet_selections WHERE task_id = ?",
                        (related_task_id,),
                    ).fetchall()
                )

        raw_ref = import_row["object_ref"]
        if raw_ref and not raw_ref.startswith("deleted:"):
            self.object_store.delete(raw_ref)
        for preview_ref in preview_refs:
            self.preview_store.delete(preview_ref)

        with self.database.transaction(immediate=True) as connection:
            if related_task_ids:
                task_parameters = [(related_task_id,) for related_task_id in related_task_ids]
                connection.executemany(
                    "DELETE FROM model_calls WHERE task_id = ?",
                    task_parameters,
                )
                connection.executemany(
                    "DELETE FROM signal_packages WHERE task_id = ?",
                    task_parameters,
                )
            if revisions:
                revision_parameters = [(revision,) for revision in revisions]
                connection.executemany(
                    "DELETE FROM pet_actions WHERE candidate_id IN "
                    "(SELECT id FROM pet_candidates WHERE profile_revision = ?)",
                    revision_parameters,
                )
                connection.executemany(
                    "DELETE FROM pet_selections WHERE task_id IN "
                    "(SELECT task_id FROM pet_candidates WHERE profile_revision = ?)",
                    revision_parameters,
                )
                connection.executemany(
                    "DELETE FROM pet_candidates WHERE profile_revision = ?",
                    revision_parameters,
                )
                connection.executemany(
                    "DELETE FROM profile_tracks WHERE profile_revision = ?",
                    revision_parameters,
                )
                connection.executemany(
                    "DELETE FROM mood_profiles WHERE revision = ?",
                    revision_parameters,
                )
            if playlist_ids:
                playlist_parameters = [(playlist_id,) for playlist_id in playlist_ids]
                connection.executemany(
                    "DELETE FROM tracks WHERE playlist_version_id = ?",
                    playlist_parameters,
                )
                connection.executemany(
                    "DELETE FROM playlist_versions WHERE id = ?",
                    playlist_parameters,
                )
            removable_tasks = sorted(task for task in related_task_ids if task != task_id)
            if removable_tasks:
                connection.executemany(
                    "DELETE FROM tasks WHERE id = ?",
                    [(removable_task,) for removable_task in removable_tasks],
                )
            consent_id = input_payload.get("consentId")
            resource_ids = [
                import_id,
                *playlist_ids,
                *revisions,
                *related_task_ids,
                *candidate_record_ids,
                *selection_ids,
                *([consent_id] if consent_id else []),
            ]
            if resource_ids:
                resource_parameters = [(resource_id,) for resource_id in resource_ids]
                connection.executemany(
                    "DELETE FROM request_idempotency WHERE resource_id = ?",
                    resource_parameters,
                )
                connection.executemany(
                    "DELETE FROM audit_events WHERE resource_id = ?",
                    resource_parameters,
                )
            connection.execute("DELETE FROM playlist_imports WHERE id = ?", (import_id,))
            if consent_id:
                connection.execute(
                    "DELETE FROM consent_decisions WHERE id = ? AND NOT EXISTS "
                    "(SELECT 1 FROM playlist_imports WHERE consent_decision_id = ?)",
                    (consent_id, consent_id),
                )
            completed_at = utc_now()
            connection.execute(
                "UPDATE deletion_requests SET status = 'DELETION_COMPLETED', completed_at = ? WHERE id = ?",
                (completed_at, deletion["id"]),
            )
            connection.execute(
                "UPDATE tasks SET input_json = ? WHERE id = ?",
                (canonical_json({"targetIdHash": sha256_bytes(import_id.encode())}), task_id),
            )
        self._task_transition(
            task_id,
            status="DELETION_COMPLETED",
            node="N13_DELETE_COMPLETE",
            progress=100,
            output={"deletionId": deletion["id"], "deleted": True},
            retry_allowed=False,
        )
        record_audit(
            self.database,
            event_type="deletion_completed",
            status="ok",
            owner_id=owner_id,
            resource_type="deletion_request",
            resource_id=deletion["id"],
            detail={"targetType": "playlist_import"},
        )

    def get_pet_candidates(self, owner_id: str, task_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            task = self._get_owned(connection, "tasks", task_id, owner_id)
            if task["task_type"] != "GENERATE_PET_CANDIDATES":
                raise AppError("TASK_TYPE_MISMATCH", "该任务不是桌宠候选任务。", 409)
            rows = connection.execute(
                "SELECT * FROM pet_candidates WHERE task_id = ? ORDER BY candidate_index",
                (task_id,),
            ).fetchall()
            selection = connection.execute(
                "SELECT * FROM pet_selections WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if rows:
                profile_row = self._get_owned(
                    connection,
                    "mood_profiles",
                    rows[0]["profile_revision"],
                    owner_id,
                )
                self._assert_profile_available(connection, profile_row)
        return {
            "task": self._serialize_task(task),
            "selection": self._serialize_pet_selection(selection) if selection else None,
            "candidates": [
                {
                    "candidateId": safe_json_loads(row["spec_json"], {})["candidateId"],
                    "candidateRecordId": row["id"],
                    "candidateIndex": row["candidate_index"],
                    "petSpec": safe_json_loads(row["spec_json"], {}),
                    "copy": safe_json_loads(row["copy_json"], {}),
                    "previewUrl": f"/v1/pet-candidates/{task_id}/previews/{row['id']}",
                }
                for row in rows
            ],
        }

    @staticmethod
    def _serialize_pet_selection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "selectionId": row["id"],
            "taskId": row["task_id"],
            "candidateRecordId": row["candidate_record_id"],
            "selectionVersion": row["selection_version"],
            "selectedAt": row["updated_at"],
        }

    def select_pet_candidate(
        self,
        *,
        owner_id: str,
        task_id: str,
        candidate_record_id: str,
        expected_selection_version: int | None,
        idempotency_key: str | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        key = self._require_idempotency_key(idempotency_key)
        request_hash = self._request_hash(
            {
                "taskId": task_id,
                "candidateRecordId": candidate_record_id,
                "expectedSelectionVersion": expected_selection_version,
            }
        )
        scope = f"pet-candidates:{task_id}:select"
        changed = False
        with self.database.transaction(immediate=True) as connection:
            existing_id = self._idempotent_resource(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
            )
            if existing_id:
                row = self._get_owned(connection, "pet_selections", existing_id, owner_id)
                return self._serialize_pet_selection(row)

            task = self._get_owned(connection, "tasks", task_id, owner_id)
            if task["task_type"] != "GENERATE_PET_CANDIDATES":
                raise AppError("TASK_TYPE_MISMATCH", "该任务不是桌宠候选任务。", 409)
            if task["status"] != "CANDIDATES_READY":
                raise AppError("CANDIDATES_NOT_READY", "桌宠候选尚未生成完成。", 409)
            candidate = connection.execute(
                "SELECT * FROM pet_candidates WHERE id = ? AND task_id = ? AND owner_id = ?",
                (candidate_record_id, task_id, owner_id),
            ).fetchone()
            if candidate is None:
                raise AppError("CANDIDATE_NOT_FOUND", "选择的桌宠候选不存在。", 404)
            profile = self._get_owned(
                connection,
                "mood_profiles",
                candidate["profile_revision"],
                owner_id,
            )
            self._assert_profile_available(connection, profile)

            selection = connection.execute(
                "SELECT * FROM pet_selections WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            current_version = int(selection["selection_version"]) if selection else 0
            if expected_selection_version is not None and expected_selection_version != current_version:
                raise AppError("SELECTION_CONFLICT", "候选选择已更新，请刷新后重试。", 409)
            now = utc_now()
            if selection is None:
                selection_id = new_id("selection")
                connection.execute(
                    "INSERT INTO pet_selections(id, task_id, owner_id, candidate_record_id, "
                    "selection_version, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (selection_id, task_id, owner_id, candidate_record_id, now, now),
                )
                changed = True
            else:
                selection_id = selection["id"]
                if selection["candidate_record_id"] != candidate_record_id:
                    connection.execute(
                        "UPDATE pet_selections SET candidate_record_id = ?, selection_version = ?, "
                        "updated_at = ? WHERE id = ?",
                        (candidate_record_id, current_version + 1, now, selection_id),
                    )
                    changed = True
            self._record_idempotency(
                connection,
                owner_id=owner_id,
                scope=scope,
                key=key,
                resource_id=selection_id,
                request_hash=request_hash,
            )
            row = connection.execute("SELECT * FROM pet_selections WHERE id = ?", (selection_id,)).fetchone()
            result = self._serialize_pet_selection(row)
        record_audit(
            self.database,
            event_type="pet_candidate_selected",
            status="ok",
            owner_id=owner_id,
            request_id=request_id,
            resource_type="pet_selection",
            resource_id=result["selectionId"],
            detail={
                "candidateRecordId": candidate_record_id,
                "selectionVersion": result["selectionVersion"],
                "changed": changed,
            },
        )
        self._ensure_pet_actions_task(owner_id, task_id, candidate_record_id)
        return result

    def activate_desktop_pet(self, *, owner_id: str, task_id: str, request_id: str | None = None) -> dict[str, Any]:
        """把已选候选写为桌宠配置并启动/重启本地桌面窗口进程。"""
        with self.database.connect() as connection:
            task = self._get_owned(connection, "tasks", task_id, owner_id)
            if task["task_type"] != "GENERATE_PET_CANDIDATES":
                raise AppError("TASK_TYPE_MISMATCH", "该任务不是桌宠候选任务。", 409)
            selection = connection.execute(
                "SELECT * FROM pet_selections WHERE task_id = ?", (task_id,)
            ).fetchone()
            if selection is None:
                raise AppError("NO_SELECTION", "请先在验收台选择一只桌宠候选。", 409)
        try:
            profile_dict = desktop_launch.build_profile_from_db(self.settings.database_path, task_id)
            result = desktop_launch.write_profile_and_launch(profile_dict)
        except (LookupError, ValueError, FileNotFoundError, OSError) as exc:
            raise AppError(
                "DESKTOP_ACTIVATE_FAILED",
                f"桌宠激活失败：{exc}",
                500,
            ) from exc
        record_audit(
            self.database,
            event_type="desktop_pet_activated",
            status="ok",
            owner_id=owner_id,
            request_id=request_id,
            resource_type="pet_selection",
            resource_id=task_id,
            detail={"candidateRecordId": profile_dict["source"]["candidateRecordId"], "pid": result["pid"]},
        )
        return result

    def set_desktop_pet_state(
        self,
        *,
        owner_id: str,
        task_id: str,
        action: str,
        say: bool,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """向已激活的本地桌宠发送经过白名单校验的声明式状态。"""
        with self.database.connect() as connection:
            task = self._get_owned(connection, "tasks", task_id, owner_id)
            if task["task_type"] != "GENERATE_PET_CANDIDATES":
                raise AppError("TASK_TYPE_MISMATCH", "该任务不是桌宠候选任务。", 409)
            selection = connection.execute(
                "SELECT candidate_record_id FROM pet_selections WHERE task_id = ?", (task_id,)
            ).fetchone()
            if selection is None:
                raise AppError("NO_SELECTION", "请先选择并激活一只桌宠。", 409)
        try:
            result = desktop_launch.push_action(action, say=say)
        except (LookupError, ValueError, FileNotFoundError, OSError) as exc:
            raise AppError(
                "DESKTOP_NOT_RUNNING",
                "桌宠尚未运行，请先点击“放到桌面”。",
                409,
            ) from exc
        record_audit(
            self.database,
            event_type="desktop_pet_state_changed",
            status="ok",
            owner_id=owner_id,
            request_id=request_id,
            resource_type="pet_selection",
            resource_id=selection["candidate_record_id"],
            detail={"action": action, "say": say},
        )
        return result

    def _rebuild_candidate_preview(self, candidate: sqlite3.Row) -> tuple[str, bytes]:
        """Recreate a deterministic preview when an old cache file was removed."""
        preview_ref = render_preview(
            safe_json_loads(candidate["spec_json"], {}),
            safe_json_loads(candidate["copy_json"], {}),
            self.catalogs,
            self.settings,
        )
        content = self.preview_store.get(preview_ref)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE pet_candidates SET preview_ref = ? WHERE id = ?",
                (preview_ref, candidate["id"]),
            )
            connection.execute(
                "UPDATE pet_actions SET preview_ref = ? WHERE candidate_id = ?",
                (preview_ref, candidate["id"]),
            )
        return preview_ref, content

    def get_preview(self, owner_id: str, task_id: str, candidate_id: str) -> tuple[bytes, str]:
        with self.database.connect() as connection:
            task = self._get_owned(connection, "tasks", task_id, owner_id)
            row = connection.execute(
                "SELECT * FROM pet_candidates WHERE id = ? AND task_id = ?",
                (candidate_id, task["id"]),
            ).fetchone()
            if row is None:
                raise AppError("NOT_FOUND", "预览不存在。", 404)
            profile_row = self._get_owned(connection, "mood_profiles", row["profile_revision"], owner_id)
            self._assert_profile_available(connection, profile_row)
            preview_ref = row["preview_ref"]
        try:
            content = self.preview_store.get(preview_ref)
        except FileNotFoundError:
            preview_ref, content = self._rebuild_candidate_preview(row)
        media_type = "image/png" if preview_ref.endswith(".png") else "image/svg+xml"
        return content, media_type

    def _ensure_pet_actions_task(self, owner_id: str, task_id: str, candidate_id: str) -> dict[str, Any]:
        if not self.settings.image_enabled:
            with self.database.transaction(immediate=True) as connection:
                task = self._get_owned(connection, "tasks", task_id, owner_id)
                if task["task_type"] != "GENERATE_PET_CANDIDATES":
                    raise AppError("TASK_TYPE_MISMATCH", "该任务不是桌宠候选任务。", 409)
                candidate = connection.execute(
                    "SELECT preview_ref FROM pet_candidates WHERE id = ? AND task_id = ? AND owner_id = ?",
                    (candidate_id, task_id, owner_id),
                ).fetchone()
                if candidate is None:
                    raise AppError("CANDIDATE_NOT_FOUND", "桌宠候选不存在。", 404)
                existing = {
                    row["action"]
                    for row in connection.execute(
                        "SELECT action FROM pet_actions WHERE candidate_id = ?", (candidate_id,)
                    ).fetchall()
                }
                now = utc_now()
                for action in PET_ACTIONS:
                    if action in existing:
                        continue
                    connection.execute(
                        "INSERT INTO pet_actions(id, task_id, candidate_id, action, preview_ref, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (new_id("action"), task_id, candidate_id, action, candidate["preview_ref"], now),
                    )
            return {
                "taskId": None,
                "status": "ACTIONS_READY",
                "output": {"actionCount": len(PET_ACTIONS), "localFallback": True},
            }
        return self._create_task(
            owner_id=owner_id,
            task_type="GENERATE_PET_ACTIONS",
            idempotency_key=f"pet-actions:{candidate_id}",
            input_payload={"petTaskId": task_id, "candidateId": candidate_id},
            initial_node="N13_GENERATE_ACTIONS",
        )

    def _process_pet_actions(self, task: sqlite3.Row) -> None:
        task_id = task["id"]
        owner_id = task["owner_id"]
        payload = safe_json_loads(task["input_json"], {})
        candidate_id = payload["candidateId"]
        pet_task_id = payload["petTaskId"]
        with self.database.connect() as connection:
            candidate = connection.execute(
                "SELECT * FROM pet_candidates WHERE id = ? AND task_id = ? AND owner_id = ?",
                (candidate_id, pet_task_id, owner_id),
            ).fetchone()
            if candidate is None:
                raise AppError("CANDIDATE_NOT_FOUND", "桌宠候选不存在。", 404)
            profile_row = self._get_owned(connection, "mood_profiles", candidate["profile_revision"], owner_id)
            self._assert_profile_available(connection, profile_row)
            consent = connection.execute(
                "SELECT cd.id FROM consent_decisions cd "
                "JOIN playlist_imports pi ON pi.consent_decision_id = cd.id "
                "JOIN playlist_versions pv ON pv.import_id = pi.id "
                "WHERE pv.id = ?",
                (profile_row["playlist_version_id"],),
            ).fetchone()
            done = {row["action"] for row in connection.execute(
                "SELECT action FROM pet_actions WHERE candidate_id = ?", (candidate_id,)
            ).fetchall()}
        if len(done) == len(PET_ACTIONS):
            self._task_transition(
                task_id,
                status="ACTIONS_READY",
                node="N14_ACTIONS_READY",
                progress=100,
                output={"actionCount": len(PET_ACTIONS), "recovered": True},
                retry_allowed=False,
            )
            return
        profile = self._serialize_profile_row(profile_row)
        spec = safe_json_loads(candidate["spec_json"], {})
        copy = safe_json_loads(candidate["copy_json"], {})
        base_ref = candidate["preview_ref"]
        base_bytes = self.preview_store.get(base_ref)
        base_suffix = ".png" if base_ref.endswith(".png") else ".svg"
        gateway = (
            self.model_gateway
            if self._external_processing_allowed(consent["id"] if consent else None)
            and self._model_budget_available(owner_id)
            else ModelGateway()
        )
        self._task_transition(task_id, status="GENERATING_ACTIONS", node="N13_GENERATE_ACTIONS", progress=10)
        for index, action in enumerate(PET_ACTIONS):
            if action in done:
                continue
            if self._stop_if_cancelled(task_id):
                return
            image = gateway.generate_image(
                build_action_prompt(spec, copy, profile, self.catalogs, action),
                idempotency_key=f"{task_id}:{action}",
            )
            if image.image_bytes:
                preview_ref = self.preview_store.put(image.image_bytes, suffix=".png")
            else:
                preview_ref = self.preview_store.put(base_bytes, suffix=base_suffix)
            now = utc_now()
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO pet_actions(id, task_id, candidate_id, action, preview_ref, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (new_id("action"), task_id, candidate_id, action, preview_ref, now),
                )
            self._task_transition(
                task_id,
                status="GENERATING_ACTIONS",
                node="N13_GENERATE_ACTIONS",
                progress=10 + (index + 1) * 20,
            )
        self._task_transition(
            task_id,
            status="ACTIONS_READY",
            node="N14_ACTIONS_READY",
            progress=100,
            output={"actionCount": len(PET_ACTIONS)},
            retry_allowed=False,
        )

    def get_pet_actions(self, owner_id: str, task_id: str, candidate_id: str) -> dict[str, Any]:
        def read_rows() -> list[sqlite3.Row]:
            with self.database.connect() as connection:
                return connection.execute(
                    "SELECT * FROM pet_actions WHERE candidate_id = ? ORDER BY action", (candidate_id,)
                ).fetchall()

        with self.database.connect() as connection:
            task = self._get_owned(connection, "tasks", task_id, owner_id)
            candidate = connection.execute(
                "SELECT * FROM pet_candidates WHERE id = ? AND task_id = ?",
                (candidate_id, task["id"]),
            ).fetchone()
            if candidate is None:
                raise AppError("CANDIDATE_NOT_FOUND", "桌宠候选不存在。", 404)
        rows = read_rows()
        rows_by_action = {row["action"]: row for row in rows}
        generation_task = None
        if len(rows_by_action) < len(PET_ACTIONS):
            # Older selections may have been created while image generation was disabled.
            # Ensure generation, or materialize deterministic local fallbacks, so old records heal on read.
            generation_task = self._ensure_pet_actions_task(owner_id, task_id, candidate_id)
            if generation_task["status"] == "ACTIONS_READY":
                rows = read_rows()
                rows_by_action = {row["action"]: row for row in rows}
        generation_status = generation_task["status"] if generation_task else "ACTIONS_READY"
        if generation_status in {"FINAL_FAILED", "CANCELLED"}:
            generation_state = "failed"
        elif len(rows_by_action) == len(PET_ACTIONS):
            generation_state = "ready"
        else:
            generation_state = "generating"
        return {
            "generationState": generation_state,
            "fallbackMode": not self.settings.image_enabled,
            "actions": [
                {
                    "action": row["action"],
                    "previewUrl": f"/v1/pet-candidates/{task_id}/actions/{candidate_id}/{row['action']}",
                }
                for action in PET_ACTIONS
                if (row := rows_by_action.get(action)) is not None
            ]
        }

    def get_pet_action_preview(self, owner_id: str, task_id: str, candidate_id: str, action: str) -> tuple[bytes, str]:
        with self.database.connect() as connection:
            task = self._get_owned(connection, "tasks", task_id, owner_id)
            candidate = connection.execute(
                "SELECT * FROM pet_candidates WHERE id = ? AND task_id = ? AND owner_id = ?",
                (candidate_id, task["id"], owner_id),
            ).fetchone()
            if candidate is None:
                raise AppError("CANDIDATE_NOT_FOUND", "桌宠候选不存在。", 404)
            row = connection.execute(
                "SELECT * FROM pet_actions WHERE candidate_id = ? AND action = ?",
                (candidate_id, action),
            ).fetchone()
            if row is None:
                raise AppError("NOT_FOUND", "动作预览不存在。", 404)
            preview_ref = row["preview_ref"]
        try:
            content = self.preview_store.get(preview_ref)
        except FileNotFoundError:
            # Older databases can retain action references after their generated
            # preview cache has been cleaned. Heal those records with the stable
            # candidate preview instead of leaving the UI in a permanent error
            # state or repeatedly requesting the missing object.
            preview_ref = candidate["preview_ref"]
            try:
                content = self.preview_store.get(preview_ref)
            except FileNotFoundError:
                preview_ref, content = self._rebuild_candidate_preview(candidate)
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE pet_actions SET preview_ref = ? WHERE candidate_id = ? AND action = ?",
                    (preview_ref, candidate_id, action),
                )
        media_type = "image/png" if preview_ref.endswith(".png") else "image/svg+xml"
        return content, media_type

    def get_character_card(self, owner_id: str, task_id: str, candidate_id: str) -> bytes:
        with self.database.connect() as connection:
            task = self._get_owned(connection, "tasks", task_id, owner_id)
            row = connection.execute(
                "SELECT * FROM pet_candidates WHERE id = ? AND task_id = ?",
                (candidate_id, task["id"]),
            ).fetchone()
            if row is None:
                raise AppError("NOT_FOUND", "角色卡不存在。", 404)
            profile_row = self._get_owned(connection, "mood_profiles", row["profile_revision"], owner_id)
            self._assert_profile_available(connection, profile_row)
            representative = connection.execute(
                "SELECT t.title, t.artist FROM tracks t "
                "JOIN profile_tracks pt ON pt.track_ref = t.track_ref "
                "WHERE pt.profile_revision = ? AND pt.excluded = 0 "
                "ORDER BY t.row_ref LIMIT 5",
                (row["profile_revision"],),
            ).fetchall()
        profile = self._serialize_profile_row(profile_row)
        dna = build_dna(
            percentages=profile["percentages"],
            representative_tracks=[
                {"title": item["title"], "artist": item["artist"]} for item in representative
            ],
            catalogs=self.catalogs,
        )
        pet_image = None
        if row["preview_ref"].endswith(".png"):
            pet_image = self.preview_store.get(row["preview_ref"])
        svg = render_character_card(
            spec=safe_json_loads(row["spec_json"], {}),
            copy=safe_json_loads(row["copy_json"], {}),
            dna=dna,
            catalogs=self.catalogs,
            settings=self.settings,
            pet_image=pet_image,
        )
        return svg.encode("utf-8")

    def cleanup_expired_raw_files(self) -> int:
        cutoff = (datetime.now(UTC) - timedelta(hours=self.settings.raw_file_retention_hours)).isoformat(timespec="milliseconds")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, object_ref, status, file_hash FROM playlist_imports "
                "WHERE created_at < ? AND object_ref NOT LIKE 'deleted:%'",
                (cutoff,),
            ).fetchall()
        count = 0
        for row in rows:
            self.object_store.delete(row["object_ref"])
            next_status = "RAW_FILE_EXPIRED" if row["status"] == "MAPPING_REQUIRED" else row["status"]
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE playlist_imports SET object_ref = ?, status = ?, updated_at = ? WHERE id = ?",
                    (f"deleted:{row['file_hash']}", next_status, utc_now(), row["id"]),
                )
            count += 1
        return count
