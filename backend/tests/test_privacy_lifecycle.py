from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from melopet_backend.api import create_app
from melopet_backend.config import Settings
from melopet_backend.model_gateway import ModelCallResult, ModelGateway
from melopet_backend.service import MeloPetService
from melopet_backend.worker import Worker

from .conftest import create_mapped_import, issue_auth, known_playlist_csv


def _headers(auth: dict[str, str], key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": auth["Authorization"]}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def _complete_flow(client: TestClient, service: MeloPetService):
    auth = issue_auth(client)
    mapped = create_mapped_import(client, auth, known_playlist_csv())
    analysis_response = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_headers(auth, "privacy-analysis-key"),
    )
    analysis_task_id = analysis_response.json()["data"]["taskId"]
    assert Worker(service).run_once()
    analysis = client.get(
        f"/v1/tasks/{analysis_task_id}", headers=_headers(auth)
    ).json()["data"]
    revision = analysis["output"]["profileRevision"]
    client.post(
        f"/v1/mood-profiles/{revision}/confirm",
        headers=_headers(auth, "privacy-confirm-key"),
    )
    pet_response = client.post(
        f"/v1/mood-profiles/{revision}/pet-candidates",
        headers=_headers(auth, "privacy-pet-key"),
    )
    pet_task_id = pet_response.json()["data"]["taskId"]
    assert Worker(service).run_once()
    candidates = client.get(
        f"/v1/pet-candidates/{pet_task_id}", headers=_headers(auth)
    ).json()["data"]["candidates"]
    return auth, mapped["importId"], revision, pet_task_id, candidates


def test_signal_packages_and_task_heartbeat_are_persisted(client: TestClient, service: MeloPetService):
    auth = issue_auth(client)
    mapped = create_mapped_import(client, auth, known_playlist_csv())
    response = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_headers(auth, "signal-analysis-key"),
    )
    task_id = response.json()["data"]["taskId"]
    assert Worker(service).run_once()
    task = client.get(f"/v1/tasks/{task_id}", headers=_headers(auth)).json()["data"]
    assert task["status"] == "PROFILE_READY"
    assert task["heartbeatAt"]
    assert task["workflowVersion"] == "melopet-m1@1.0.0"
    with service.database.connect() as connection:
        packages = connection.execute(
            "SELECT * FROM signal_packages WHERE task_id = ? ORDER BY track_ref", (task_id,)
        ).fetchall()
    assert len(packages) == 12
    assert all(row["source_mode"] == "uploaded_signals" for row in packages)
    serialized = "\n".join(row["allowed_signals_json"] + row["source_refs_json"] for row in packages)
    assert "Song 0" not in serialized
    assert "Artist 0" not in serialized


def test_consent_can_be_read_revoked_and_replayed(client: TestClient):
    auth = issue_auth(client)
    created = client.post(
        "/v1/playlist-imports",
        headers=_headers(auth, "consent-create-key"),
        json={
            "text": "title,artist,genre\nOne,A,rock",
            "consentExternalProcessing": True,
        },
    ).json()["data"]
    consent_id = created["consentId"]
    before = client.get(f"/v1/consents/{consent_id}", headers=_headers(auth))
    assert before.status_code == 200
    assert before.json()["data"]["decision"] == "allow"
    revoked = client.post(
        f"/v1/consents/{consent_id}/revoke",
        headers=_headers(auth, "consent-revoke-key"),
    )
    replay = client.post(
        f"/v1/consents/{consent_id}/revoke",
        headers=_headers(auth, "consent-revoke-key"),
    )
    assert revoked.status_code == replay.status_code == 200
    assert revoked.json()["data"]["decision"] == "deny"
    assert revoked.json()["data"]["revokedAt"]
    assert replay.json()["data"]["revokedAt"] == revoked.json()["data"]["revokedAt"]


class RecordingGateway(ModelGateway):
    def __init__(self):
        self.mood_calls = 0

    def classify_moods(self, payload, *, idempotency_key=None):
        del payload, idempotency_key
        self.mood_calls += 1
        return ModelCallResult(None, "recording", "model", "prompt", "hash", 0, 0, 0, 1, "fallback")


def test_revoked_consent_prevents_new_model_egress(tmp_path: Path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'revocation.db'}",
        object_store_path=tmp_path / "objects",
        preview_store_path=tmp_path / "previews",
        capability_token_pepper="test-pepper",
        model_provider="openai_compatible",
        model_base_url="https://model.invalid/v1",
        model_api_key="test-key",
        mood_model_id="mood-model",
        copy_model_id="copy-model",
        model_daily_budget=1,
        model_input_cost_per_1m=1,
    )
    gateway = RecordingGateway()
    service = MeloPetService(settings, model_gateway=gateway)
    with TestClient(create_app(settings, service)) as client:
        auth = issue_auth(client)
        created = client.post(
            "/v1/playlist-imports",
            headers=_headers(auth, "revoked-import-key"),
            json={
                "text": "title,artist,genre\n"
                + "\n".join(f"Song {i},Artist {i},unmapped" for i in range(12)),
                "consentExternalProcessing": True,
            },
        ).json()["data"]
        mapped_response = client.put(
            f"/v1/playlist-imports/{created['importId']}/mapping",
            headers=_headers(auth, "revoked-mapping-key"),
            json={"mapping": {"title": "title", "artist": "artist", "genre": "genre"}},
        )
        assert mapped_response.status_code == 200
        mapped = {"importId": created["importId"], **mapped_response.json()["data"]}
        imported = client.get(
            f"/v1/playlist-imports/{mapped['importId']}", headers=_headers(auth)
        ).json()["data"]
        client.post(
            f"/v1/consents/{imported['consentId']}/revoke",
            headers=_headers(auth, "revoke-before-worker"),
        )
        task = client.post(
            f"/v1/playlist-imports/{mapped['importId']}/analyses",
            headers=_headers(auth, "revoked-analysis-key"),
        ).json()["data"]
        assert Worker(service).run_once()
        result = client.get(f"/v1/tasks/{task['taskId']}", headers=_headers(auth)).json()["data"]
        assert result["status"] == "PROFILE_READY"
        assert result["output"]["unknownCount"] == 12
        assert gateway.mood_calls == 0


def test_playlist_deletion_blocks_access_and_physically_removes_data(client: TestClient, service: MeloPetService):
    auth, import_id, revision, pet_task_id, candidates = _complete_flow(client, service)
    selected = client.post(
        f"/v1/pet-candidates/{pet_task_id}/select",
        headers=_headers(auth, "privacy-selection-key"),
        json={"candidateRecordId": candidates[0]["candidateRecordId"]},
    )
    assert selected.status_code == 200
    selection_id = selected.json()["data"]["selectionId"]
    candidate_record_id = candidates[0]["candidateRecordId"]
    with service.database.connect() as connection:
        raw_ref = connection.execute(
            "SELECT object_ref FROM playlist_imports WHERE id = ?", (import_id,)
        ).fetchone()["object_ref"]
        preview_ref = connection.execute(
            "SELECT preview_ref FROM pet_candidates WHERE task_id = ? ORDER BY candidate_index LIMIT 1",
            (pet_task_id,),
        ).fetchone()["preview_ref"]
    preview_path = service.settings.preview_path / preview_ref
    raw_path = service.settings.object_path / raw_ref
    assert preview_path.is_file()
    assert raw_path.is_file()

    requested = client.delete(
        f"/v1/playlist-imports/{import_id}",
        headers=_headers(auth, "delete-import-key"),
    )
    assert requested.status_code == 202
    deletion_id = requested.json()["data"]["deletionId"]
    deletion_task_id = requested.json()["data"]["task"]["taskId"]
    cancel = client.post(
        f"/v1/tasks/{deletion_task_id}/cancel",
        headers=_headers(auth, "cancel-deletion-key"),
    )
    assert cancel.status_code == 409
    assert cancel.json()["errorCode"] == "DELETION_NOT_CANCELLABLE"
    blocked = client.get(f"/v1/mood-profiles/{revision}", headers=_headers(auth))
    assert blocked.status_code == 410
    assert blocked.json()["errorCode"] == "DELETION_PENDING"

    assert Worker(service).run_once()
    deletion = client.get(f"/v1/deletions/{deletion_id}", headers=_headers(auth))
    assert deletion.status_code == 200
    assert deletion.json()["data"]["status"] == "DELETION_COMPLETED"
    assert deletion.json()["data"]["task"]["status"] == "DELETION_COMPLETED"
    assert client.get(f"/v1/playlist-imports/{import_id}", headers=_headers(auth)).status_code == 404
    assert client.get(f"/v1/mood-profiles/{revision}", headers=_headers(auth)).status_code == 404
    assert not preview_path.exists()
    assert not raw_path.exists()

    replay = client.delete(
        f"/v1/playlist-imports/{import_id}",
        headers=_headers(auth, "delete-import-replay-key"),
    )
    assert replay.status_code == 202
    assert replay.json()["data"]["deletionId"] == deletion_id

    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM playlist_imports").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM playlist_versions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM signal_packages").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM mood_profiles").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM profile_tracks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM pet_actions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM pet_candidates").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM pet_selections").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE resource_id IN (?, ?)",
                (selection_id, candidate_record_id),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM request_idempotency WHERE resource_id = ?",
                (selection_id,),
            ).fetchone()[0]
            == 0
        )


def test_playlist_deletion_removes_corrected_revision_chain_after_consent_revocation(
    client: TestClient,
    service: MeloPetService,
):
    auth, import_id, revision, _pet_task_id, _candidates = _complete_flow(client, service)
    profile = client.get(
        f"/v1/mood-profiles/{revision}",
        headers=_headers(auth),
    ).json()["data"]
    corrected = client.post(
        f"/v1/mood-profiles/{revision}/corrections",
        headers=_headers(auth, "delete-corrected-profile-key"),
        json={
            "expectedRevision": revision,
            "corrections": [
                {
                    "trackRef": profile["tracks"][0]["trackRef"],
                    "action": "set_mood",
                    "moodId": "calm_focus",
                }
            ],
        },
    )
    assert corrected.status_code == 200
    consent_id = client.get(
        f"/v1/playlist-imports/{import_id}",
        headers=_headers(auth),
    ).json()["data"]["consentId"]
    revoked = client.post(
        f"/v1/consents/{consent_id}/revoke",
        headers=_headers(auth, "delete-revoked-consent-key"),
    )
    assert revoked.status_code == 200

    requested = client.delete(
        f"/v1/playlist-imports/{import_id}",
        headers=_headers(auth, "delete-corrected-import-key"),
    )
    assert requested.status_code == 202
    deletion_id = requested.json()["data"]["deletionId"]
    assert Worker(service).run_once()
    deletion = client.get(
        f"/v1/deletions/{deletion_id}",
        headers=_headers(auth),
    ).json()["data"]
    assert deletion["status"] == "DELETION_COMPLETED"


def test_deletion_and_consent_enforce_owner_and_idempotency(client: TestClient):
    owner = issue_auth(client)
    attacker = issue_auth(client)
    created = client.post(
        "/v1/playlist-imports",
        headers=_headers(owner, "owner-private-import"),
        json={"text": "title,artist\nSecret,A"},
    ).json()["data"]
    consent_id = created["consentId"]
    assert client.get(f"/v1/consents/{consent_id}", headers=_headers(attacker)).status_code == 403
    assert (
        client.post(
            f"/v1/consents/{consent_id}/revoke",
            headers=_headers(attacker, "attacker-revoke-key"),
        ).status_code
        == 403
    )
    no_key = client.delete(f"/v1/playlist-imports/{created['importId']}", headers=_headers(owner))
    assert no_key.status_code == 400
    assert no_key.json()["errorCode"] == "IDEMPOTENCY_KEY_REQUIRED"
    forbidden = client.delete(
        f"/v1/playlist-imports/{created['importId']}",
        headers=_headers(attacker, "attacker-delete-key"),
    )
    assert forbidden.status_code == 403


def test_deletion_immediately_blocks_queued_work_and_raw_remapping(
    client: TestClient,
    service: MeloPetService,
):
    auth = issue_auth(client)
    mapped = create_mapped_import(client, auth, known_playlist_csv())
    analysis = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_headers(auth, "pending-delete-analysis"),
    ).json()["data"]
    deletion = client.delete(
        f"/v1/playlist-imports/{mapped['importId']}",
        headers=_headers(auth, "pending-delete-request"),
    ).json()["data"]

    remap = client.put(
        f"/v1/playlist-imports/{mapped['importId']}/mapping",
        headers=_headers(auth, "pending-delete-remap"),
        json={"mapping": {"title": "title", "artist": "artist", "genre": "genre"}},
    )
    assert remap.status_code == 410
    assert remap.json()["errorCode"] == "DELETION_PENDING"

    assert Worker(service).run_once()
    stopped = client.get(f"/v1/tasks/{analysis['taskId']}", headers=_headers(auth)).json()["data"]
    assert stopped["status"] == "FINAL_FAILED"
    assert stopped["errorCode"] == "DELETION_PENDING"
    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM signal_packages").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM mood_profiles").fetchone()[0] == 0

    assert Worker(service).run_once()
    completed = client.get(
        f"/v1/deletions/{deletion['deletionId']}",
        headers=_headers(auth),
    ).json()["data"]
    assert completed["status"] == "DELETION_COMPLETED"


def test_expired_raw_file_is_removed_and_cannot_be_remapped(client: TestClient, service: MeloPetService):
    auth = issue_auth(client)
    created = client.post(
        "/v1/playlist-imports",
        headers=_headers(auth, "retention-import-key"),
        json={"text": "title,artist\nOld Song,Old Artist"},
    ).json()["data"]
    with service.database.connect() as connection:
        object_ref = connection.execute(
            "SELECT object_ref FROM playlist_imports WHERE id = ?", (created["importId"],)
        ).fetchone()["object_ref"]
    path = service.settings.object_path / object_ref
    assert path.is_file()
    with service.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE playlist_imports SET created_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (created["importId"],),
        )
    assert service.cleanup_expired_raw_files() == 1
    assert not path.exists()
    remap = client.put(
        f"/v1/playlist-imports/{created['importId']}/mapping",
        headers=_headers(auth, "retention-mapping-key"),
        json={"mapping": {"title": "title", "artist": "artist"}},
    )
    assert remap.status_code == 410
    assert remap.json()["errorCode"] == "RAW_FILE_EXPIRED"
