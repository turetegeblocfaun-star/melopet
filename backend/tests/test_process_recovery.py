from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from melopet_backend.api import create_app
from melopet_backend.service import MeloPetService
from melopet_backend.worker import Worker

from .conftest import create_mapped_import, issue_auth, known_playlist_csv


def _headers(auth: dict[str, str], key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": auth["Authorization"]}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def test_queued_task_and_capability_survive_service_recreation(settings):
    first_service = MeloPetService(settings)
    with TestClient(create_app(settings, first_service)) as first_client:
        auth = issue_auth(first_client)
        mapped = create_mapped_import(first_client, auth, known_playlist_csv())
        created = first_client.post(
            f"/v1/playlist-imports/{mapped['importId']}/analyses",
            headers=_headers(auth, "restart-analysis-key"),
        ).json()["data"]

    restarted_service = MeloPetService(settings)
    assert Worker(restarted_service).run_once()
    with TestClient(create_app(settings, restarted_service)) as restarted_client:
        task = restarted_client.get(
            f"/v1/tasks/{created['taskId']}", headers=_headers(auth)
        ).json()["data"]
        assert task["status"] == "PROFILE_READY"
        assert task["output"]["profileRevision"]
        assert restarted_client.get(
            f"/v1/playlist-imports/{mapped['importId']}", headers=_headers(auth)
        ).status_code == 200


def test_expired_claim_recovers_with_new_service_and_corrupt_journal(settings):
    first_service = MeloPetService(settings)
    with TestClient(create_app(settings, first_service)) as client:
        auth = issue_auth(client)
        mapped = create_mapped_import(client, auth, known_playlist_csv())
        task_id = client.post(
            f"/v1/playlist-imports/{mapped['importId']}/analyses",
            headers=_headers(auth, "crash-recovery-key"),
        ).json()["data"]["taskId"]
    claimed = first_service.claim_task("crashed-worker")
    assert claimed and claimed["id"] == task_id
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="milliseconds")
    with first_service.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE tasks SET status = 'CLASSIFYING', lease_until = ?, journal_json = 'not-json' WHERE id = ?",
            (expired, task_id),
        )

    restarted = MeloPetService(settings)
    assert Worker(restarted).run_once()
    with TestClient(create_app(settings, restarted)) as client:
        task = client.get(f"/v1/tasks/{task_id}", headers=_headers(auth)).json()["data"]
    assert task["status"] == "PROFILE_READY"
    assert task["attempt"] == 2


def test_max_attempts_fail_closed_instead_of_staying_processing(client: TestClient, service):
    auth = issue_auth(client)
    mapped = create_mapped_import(client, auth, known_playlist_csv())
    task_id = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_headers(auth, "max-attempt-key"),
    ).json()["data"]["taskId"]
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="milliseconds")
    with service.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE tasks SET status = 'CLASSIFYING', attempt = 3, lease_owner = 'dead', lease_until = ? WHERE id = ?",
            (expired, task_id),
        )
    assert not Worker(service).run_once()
    task = client.get(f"/v1/tasks/{task_id}", headers=_headers(auth)).json()["data"]
    assert task["status"] == "FINAL_FAILED"
    assert task["errorCode"] == "MAX_ATTEMPTS_EXCEEDED"
    assert task["retryAllowed"] is False


def test_terminal_task_cannot_return_to_running(client: TestClient, service):
    auth = issue_auth(client)
    mapped = create_mapped_import(client, auth, known_playlist_csv())
    task_id = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_headers(auth, "terminal-state-key"),
    ).json()["data"]["taskId"]
    assert Worker(service).run_once()
    try:
        service._task_transition(task_id, status="CLASSIFYING", node="N05_CLASSIFY_MOOD", progress=20)
    except RuntimeError as exc:
        assert str(exc) == "TERMINAL_TASK_TRANSITION_REJECTED"
    else:
        raise AssertionError("terminal task transitioned back to running")
