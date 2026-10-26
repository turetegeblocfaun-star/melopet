from __future__ import annotations

from fastapi.testclient import TestClient

from melopet_backend.worker import Worker

from .conftest import create_mapped_import, issue_auth, known_playlist_csv


def _authorization(auth: dict[str, str], key: str) -> dict[str, str]:
    return {"Authorization": auth["Authorization"], "Idempotency-Key": key}


def test_full_backend_flow_without_frontend(client: TestClient, service, monkeypatch):
    auth = issue_auth(client)
    mood_catalog = client.get("/v1/catalogs/moods", headers={"Authorization": auth["Authorization"]})
    assert mood_catalog.status_code == 200
    assert mood_catalog.json()["data"]["version"] == "0.2"
    assert mood_catalog.json()["data"]["items"][-1]["moodId"] == "UNKNOWN"
    mapped = create_mapped_import(client, auth, known_playlist_csv())
    assert mapped["normalizedCount"] == 12
    assert mapped["invalidCount"] == 0

    created_task = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_authorization(auth, "analysis-key-001"),
    )
    assert created_task.status_code == 202
    analysis_task_id = created_task.json()["data"]["taskId"]
    assert Worker(service).run_once()

    task = client.get(f"/v1/tasks/{analysis_task_id}", headers={"Authorization": auth["Authorization"]}).json()["data"]
    assert task["status"] == "PROFILE_READY"
    revision = task["output"]["profileRevision"]
    profile = client.get(f"/v1/mood-profiles/{revision}", headers={"Authorization": auth["Authorization"]}).json()["data"]
    assert profile["knownCount"] == 12
    assert profile["unknownCount"] == 0
    assert sum(item["percent"] for item in profile["percentages"]) == 100

    confirmed = client.post(
        f"/v1/mood-profiles/{revision}/confirm",
        headers=_authorization(auth, "confirm-key-001"),
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["confirmedAt"]

    pet_task_response = client.post(
        f"/v1/mood-profiles/{revision}/pet-candidates",
        headers=_authorization(auth, "pet-task-key-001"),
    )
    assert pet_task_response.status_code == 202
    pet_task_id = pet_task_response.json()["data"]["taskId"]
    assert Worker(service).run_once()
    candidates_response = client.get(
        f"/v1/pet-candidates/{pet_task_id}",
        headers={"Authorization": auth["Authorization"]},
    )
    assert candidates_response.status_code == 200
    candidates = candidates_response.json()["data"]["candidates"]
    assert len(candidates) == 3
    assert all(item["petSpec"]["developmentOnly"] for item in candidates)
    assert all(item["petSpec"]["copyRef"].startswith("pet-copy:pet_") for item in candidates)
    assert all(item["copy"]["promptVersion"] == "fixed-template@1.0.0" for item in candidates)

    selected = client.post(
        f"/v1/pet-candidates/{pet_task_id}/select",
        headers=_authorization(auth, "pet-selection-key-001"),
        json={"candidateRecordId": candidates[0]["candidateRecordId"], "expectedSelectionVersion": 0},
    )
    assert selected.status_code == 200
    assert selected.json()["data"]["selectionVersion"] == 1
    assert selected.json()["data"]["candidateRecordId"] == candidates[0]["candidateRecordId"]
    ready_actions = client.get(
        f"/v1/pet-candidates/{pet_task_id}/actions/{candidates[0]['candidateRecordId']}",
        headers={"Authorization": auth["Authorization"]},
    ).json()["data"]
    assert ready_actions["generationState"] == "ready"
    assert ready_actions["fallbackMode"] is True
    assert {item["action"] for item in ready_actions["actions"]} == {"idle", "listen", "dance", "sleep"}
    for action in ready_actions["actions"]:
        action_preview = client.get(action["previewUrl"], headers={"Authorization": auth["Authorization"]})
        assert action_preview.status_code == 200
        assert action_preview.headers["content-type"].startswith("image/svg+xml")
    monkeypatch.setattr(
        "melopet_backend.desktop_launch.push_action",
        lambda action, *, say: {"accepted": True, "action": action, "pid": 1234, "say": say},
    )
    desktop_state = client.post(
        f"/v1/pet-candidates/{pet_task_id}/desktop-state",
        headers={"Authorization": auth["Authorization"]},
        json={"action": "dance", "say": True},
    )
    assert desktop_state.status_code == 200
    assert desktop_state.json()["data"] == {"accepted": True, "action": "dance", "pid": 1234, "say": True}
    replayed_selection = client.post(
        f"/v1/pet-candidates/{pet_task_id}/select",
        headers=_authorization(auth, "pet-selection-key-001"),
        json={"candidateRecordId": candidates[0]["candidateRecordId"], "expectedSelectionVersion": 0},
    )
    assert replayed_selection.json()["data"] == selected.json()["data"]
    changed_selection = client.post(
        f"/v1/pet-candidates/{pet_task_id}/select",
        headers=_authorization(auth, "pet-selection-key-002"),
        json={"candidateRecordId": candidates[1]["candidateRecordId"], "expectedSelectionVersion": 1},
    )
    assert changed_selection.status_code == 200
    assert changed_selection.json()["data"]["selectionVersion"] == 2
    stale_selection = client.post(
        f"/v1/pet-candidates/{pet_task_id}/select",
        headers=_authorization(auth, "pet-selection-key-003"),
        json={"candidateRecordId": candidates[2]["candidateRecordId"], "expectedSelectionVersion": 1},
    )
    assert stale_selection.status_code == 409
    assert stale_selection.json()["errorCode"] == "SELECTION_CONFLICT"
    candidates_after_selection = client.get(
        f"/v1/pet-candidates/{pet_task_id}",
        headers={"Authorization": auth["Authorization"]},
    ).json()["data"]
    assert candidates_after_selection["selection"]["candidateRecordId"] == candidates[1]["candidateRecordId"]

    preview = client.get(candidates[0]["previewUrl"], headers={"Authorization": auth["Authorization"]})
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/svg+xml")
    assert b"DEV ONLY" in preview.content

    replay_task_response = client.post(
        f"/v1/mood-profiles/{revision}/pet-candidates",
        headers=_authorization(auth, "pet-task-key-002"),
    )
    replay_task_id = replay_task_response.json()["data"]["taskId"]
    assert Worker(service).run_once()
    replay_candidates = client.get(
        f"/v1/pet-candidates/{replay_task_id}",
        headers={"Authorization": auth["Authorization"]},
    ).json()["data"]["candidates"]
    assert len(replay_candidates) == 3
    assert [item["petSpec"] for item in replay_candidates] == [item["petSpec"] for item in candidates]


def test_idempotency_replays_and_rejects_different_payload(client: TestClient):
    auth = issue_auth(client)
    headers = _authorization(auth, "same-import-key")
    first = client.post(
        "/v1/playlist-imports",
        headers=headers,
        json={"text": "title,artist\nOne,A", "consentExternalProcessing": False},
    )
    replay = client.post(
        "/v1/playlist-imports",
        headers=headers,
        json={"text": "title,artist\nOne,A", "consentExternalProcessing": False},
    )
    conflict = client.post(
        "/v1/playlist-imports",
        headers=headers,
        json={"text": "title,artist\nDifferent,B", "consentExternalProcessing": False},
    )
    assert first.status_code == replay.status_code == 201
    assert first.json()["data"]["importId"] == replay.json()["data"]["importId"]
    assert conflict.status_code == 409
    assert conflict.json()["errorCode"] == "IDEMPOTENCY_CONFLICT"


def test_object_permissions_are_enforced(client: TestClient):
    owner = issue_auth(client)
    attacker = issue_auth(client)
    created = client.post(
        "/v1/playlist-imports",
        headers=_authorization(owner, "owner-import-key"),
        json={"text": "title,artist\nPrivate Song,Private Artist"},
    )
    import_id = created.json()["data"]["importId"]
    forbidden = client.get(
        f"/v1/playlist-imports/{import_id}",
        headers={"Authorization": attacker["Authorization"]},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["errorCode"] == "FORBIDDEN"


def test_unknown_rate_blocks_confirmation(client: TestClient, service):
    auth = issue_auth(client)
    rows = ["title,artist,genre", *[f"Song {i},Artist {i},unmapped" for i in range(10)]]
    mapped = create_mapped_import(client, auth, "\n".join(rows))
    task_response = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_authorization(auth, "unknown-analysis-key"),
    )
    task_id = task_response.json()["data"]["taskId"]
    Worker(service).run_once()
    task = client.get(f"/v1/tasks/{task_id}", headers={"Authorization": auth["Authorization"]}).json()["data"]
    revision = task["output"]["profileRevision"]
    blocked = client.post(
        f"/v1/mood-profiles/{revision}/confirm",
        headers=_authorization(auth, "unknown-confirm-key"),
    )
    assert blocked.status_code == 409
    assert blocked.json()["errorCode"] == "LOW_SIGNAL_BLOCKED"


def test_user_can_correct_unknowns_then_confirm(client: TestClient, service):
    auth = issue_auth(client)
    rows = ["title,artist,genre", *[f"Unknown {i},Artist {i}," for i in range(12)]]
    mapped = create_mapped_import(client, auth, "\n".join(rows))
    created = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_authorization(auth, "correct-unknown-analysis"),
    )
    assert Worker(service).run_once()
    task = client.get(
        f"/v1/tasks/{created.json()['data']['taskId']}", headers={"Authorization": auth["Authorization"]}
    ).json()["data"]
    original = client.get(
        f"/v1/mood-profiles/{task['output']['profileRevision']}",
        headers={"Authorization": auth["Authorization"]},
    ).json()["data"]
    assert original["unknownCount"] == 12
    corrected = client.post(
        f"/v1/mood-profiles/{original['revision']}/corrections",
        headers=_authorization(auth, "correct-unknown-batch"),
        json={
            "expectedRevision": original["revision"],
            "corrections": [
                {"trackRef": item["trackRef"], "action": "set_mood", "moodId": "joy_bright"}
                for item in original["tracks"][:10]
            ],
        },
    ).json()["data"]
    assert corrected["knownCount"] == 10
    assert corrected["unknownCount"] == 2
    confirmed = client.post(
        f"/v1/mood-profiles/{corrected['revision']}/confirm",
        headers=_authorization(auth, "confirm-corrected-profile"),
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["confirmedAt"]


def test_low_sample_profile_is_viewable_but_not_confirmable(client: TestClient, service):
    auth = issue_auth(client)
    mapped = create_mapped_import(client, auth, known_playlist_csv(9))
    created = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_authorization(auth, "low-sample-analysis"),
    )
    assert Worker(service).run_once()
    task = client.get(
        f"/v1/tasks/{created.json()['data']['taskId']}", headers={"Authorization": auth["Authorization"]}
    ).json()["data"]
    profile = client.get(
        f"/v1/mood-profiles/{task['output']['profileRevision']}",
        headers={"Authorization": auth["Authorization"]},
    ).json()["data"]
    assert "LOW_SAMPLE_TRIAL" in profile["qualityFlags"]
    blocked = client.post(
        f"/v1/mood-profiles/{profile['revision']}/confirm",
        headers=_authorization(auth, "low-sample-confirm"),
    )
    assert blocked.status_code == 409
    assert blocked.json()["errorCode"] == "LOW_SAMPLE_BLOCKED"
