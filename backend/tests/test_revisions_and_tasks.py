from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from melopet_backend.worker import Worker

from .conftest import create_mapped_import, issue_auth, known_playlist_csv


def _headers(auth: dict[str, str], key: str) -> dict[str, str]:
    return {"Authorization": auth["Authorization"], "Idempotency-Key": key}


def _analyzed_profile(client: TestClient, service):
    auth = issue_auth(client)
    mapped = create_mapped_import(client, auth, known_playlist_csv())
    response = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_headers(auth, "revision-analysis-key"),
    )
    Worker(service).run_once()
    task = client.get(
        f"/v1/tasks/{response.json()['data']['taskId']}",
        headers={"Authorization": auth["Authorization"]},
    ).json()["data"]
    revision = task["output"]["profileRevision"]
    profile = client.get(f"/v1/mood-profiles/{revision}", headers={"Authorization": auth["Authorization"]}).json()["data"]
    return auth, profile


def test_correction_creates_immutable_revision_and_detects_conflict(client: TestClient, service):
    auth, original = _analyzed_profile(client, service)
    track_ref = original["tracks"][0]["trackRef"]
    corrected_response = client.post(
        f"/v1/mood-profiles/{original['revision']}/corrections",
        headers=_headers(auth, "correction-key-001"),
        json={
            "expectedRevision": original["revision"],
            "corrections": [{"trackRef": track_ref, "action": "set_mood", "moodId": "calm_focus"}],
        },
    )
    assert corrected_response.status_code == 200
    corrected = corrected_response.json()["data"]
    assert corrected["revision"] != original["revision"]
    assert corrected["parentRevision"] == original["revision"]

    original_again = client.get(
        f"/v1/mood-profiles/{original['revision']}",
        headers={"Authorization": auth["Authorization"]},
    ).json()["data"]
    assert original_again["tracks"][0]["finalMoodId"] == original["tracks"][0]["finalMoodId"]

    stale = client.post(
        f"/v1/mood-profiles/{original['revision']}/corrections",
        headers=_headers(auth, "correction-key-002"),
        json={
            "expectedRevision": original["revision"],
            "corrections": [{"trackRef": track_ref, "action": "exclude"}],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["errorCode"] == "REVISION_CONFLICT"


def test_expired_worker_lease_is_reclaimed(client: TestClient, service):
    auth = issue_auth(client)
    mapped = create_mapped_import(client, auth, known_playlist_csv())
    response = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_headers(auth, "lease-analysis-key"),
    )
    task_id = response.json()["data"]["taskId"]
    expired = (datetime.now(UTC) - timedelta(seconds=5)).isoformat(timespec="milliseconds")
    with service.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE tasks SET status='CLASSIFYING', node='N05_CLASSIFY_MOOD', "
            "lease_owner='dead-worker', lease_until=? WHERE id=?",
            (expired, task_id),
        )
    assert Worker(service).run_once()
    task = client.get(f"/v1/tasks/{task_id}", headers={"Authorization": auth["Authorization"]}).json()["data"]
    assert task["status"] == "PROFILE_READY"
    assert task["attempt"] == 1


def test_active_lease_prevents_double_claim(client: TestClient, service):
    auth = issue_auth(client)
    mapped = create_mapped_import(client, auth, known_playlist_csv())
    client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_headers(auth, "single-claim-analysis-key"),
    )
    first = service.claim_task("worker-one")
    second = service.claim_task("worker-two")
    assert first is not None
    assert second is None


def test_active_duplicate_task_returns_existing_even_with_new_key(client: TestClient, service):
    auth = issue_auth(client)
    mapped = create_mapped_import(client, auth, known_playlist_csv())
    first = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_headers(auth, "duplicate-active-one"),
    )
    second = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_headers(auth, "duplicate-active-two"),
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["taskId"] == second.json()["data"]["taskId"]
    with service.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_all_correction_actions_preserve_old_revision(client: TestClient, service):
    auth, original = _analyzed_profile(client, service)
    first = original["tracks"][0]
    excluded = client.post(
        f"/v1/mood-profiles/{original['revision']}/corrections",
        headers=_headers(auth, "exclude-correction-key"),
        json={
            "expectedRevision": original["revision"],
            "corrections": [{"trackRef": first["trackRef"], "action": "exclude"}],
        },
    ).json()["data"]
    assert excluded["excludedCount"] == original["excludedCount"] + 1
    excluded_detail = client.get(
        f"/v1/mood-profiles/{excluded['revision']}", headers={"Authorization": auth["Authorization"]}
    ).json()["data"]
    assert excluded_detail["tracks"][0]["excluded"] is True

    included = client.post(
        f"/v1/mood-profiles/{excluded['revision']}/corrections",
        headers=_headers(auth, "include-correction-key"),
        json={
            "expectedRevision": excluded["revision"],
            "corrections": [{"trackRef": first["trackRef"], "action": "include"}],
        },
    ).json()["data"]
    assert included["excludedCount"] == original["excludedCount"]

    changed = client.post(
        f"/v1/mood-profiles/{included['revision']}/corrections",
        headers=_headers(auth, "set-correction-key"),
        json={
            "expectedRevision": included["revision"],
            "corrections": [
                {"trackRef": first["trackRef"], "action": "set_mood", "moodId": "calm_focus"}
            ],
        },
    ).json()["data"]
    restored = client.post(
        f"/v1/mood-profiles/{changed['revision']}/corrections",
        headers=_headers(auth, "restore-correction-key"),
        json={
            "expectedRevision": changed["revision"],
            "corrections": [{"trackRef": first["trackRef"], "action": "restore_auto"}],
        },
    ).json()["data"]
    restored_detail = client.get(
        f"/v1/mood-profiles/{restored['revision']}", headers={"Authorization": auth["Authorization"]}
    ).json()["data"]
    assert restored_detail["tracks"][0]["finalMoodId"] == first["autoMoodId"]
    assert restored_detail["tracks"][0]["correctionSource"] == "automatic"


def test_cancelled_task_never_runs(client: TestClient, service):
    auth = issue_auth(client)
    mapped = create_mapped_import(client, auth, known_playlist_csv())
    response = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_headers(auth, "cancel-analysis-key"),
    )
    task_id = response.json()["data"]["taskId"]
    cancel = client.post(f"/v1/tasks/{task_id}/cancel", headers=_headers(auth, "cancel-task-key"))
    cancel_replay = client.post(f"/v1/tasks/{task_id}/cancel", headers=_headers(auth, "cancel-task-key"))
    assert cancel.status_code == 200
    assert cancel_replay.status_code == 200
    assert cancel_replay.json()["data"]["taskId"] == task_id
    assert not Worker(service).run_once()
    task = client.get(f"/v1/tasks/{task_id}", headers={"Authorization": auth["Authorization"]}).json()["data"]
    assert task["status"] == "CANCELLED"
