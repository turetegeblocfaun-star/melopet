from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import issue_auth


def _headers(auth: dict[str, str], key: str) -> dict[str, str]:
    return {"Authorization": auth["Authorization"], "Idempotency-Key": key}


def test_missing_auth_and_idempotency_are_safe(client: TestClient):
    unauthenticated = client.post("/v1/playlist-imports", json={"text": "title,artist\nOne,A"})
    assert unauthenticated.status_code == 401
    auth = issue_auth(client)
    no_key = client.post(
        "/v1/playlist-imports",
        headers={"Authorization": auth["Authorization"]},
        json={"text": "title,artist\nOne,A"},
    )
    assert no_key.status_code == 400
    assert no_key.json()["errorCode"] == "IDEMPOTENCY_KEY_REQUIRED"

    malformed = client.post(
        "/v1/playlist-imports",
        headers={
            "Authorization": auth["Authorization"],
            "Idempotency-Key": "malformed-json-key",
            "Content-Type": "application/json",
        },
        content=b"{not-json",
    )
    assert malformed.status_code == 400
    assert malformed.json()["errorCode"] == "INVALID_REQUEST"


def test_upload_boundaries_and_formula_text_are_never_executed(client: TestClient):
    auth = issue_auth(client)
    empty = client.post(
        "/v1/playlist-imports",
        headers=_headers(auth, "empty-input-key"),
        json={"text": ""},
    )
    assert empty.status_code == 400
    assert empty.json()["errorCode"] == "FILE_EMPTY"

    created = client.post(
        "/v1/playlist-imports",
        headers=_headers(auth, "formula-input-key"),
        json={"text": "title,artist,genre\nSong,A,=HYPERLINK(http://bad)"},
    )
    import_id = created.json()["data"]["importId"]
    mapped = client.put(
        f"/v1/playlist-imports/{import_id}/mapping",
        headers=_headers(auth, "formula-map-key"),
        json={"mapping": {"title": "title", "artist": "artist", "genre": "genre"}},
    )
    assert mapped.status_code == 200
    assert any(issue["code"] == "UNTRUSTED_CELL_PREFIX" for issue in mapped.json()["data"]["issues"])


def test_multipart_csv_upload_is_supported(client: TestClient):
    auth = issue_auth(client)
    response = client.post(
        "/v1/playlist-imports",
        headers=_headers(auth, "multipart-upload-key"),
        files={"file": ("playlist.csv", b"title,artist\nOne,A", "text/csv")},
        data={"consentExternalProcessing": "false"},
    )
    assert response.status_code == 201
    assert response.json()["data"]["headers"] == ["title", "artist"]


def test_changed_consent_does_not_reuse_old_import(client: TestClient):
    auth = issue_auth(client)
    text = "title,artist\nOne,A"
    denied = client.post(
        "/v1/playlist-imports",
        headers=_headers(auth, "consent-denied-key"),
        json={"text": text, "consentExternalProcessing": False},
    )
    allowed = client.post(
        "/v1/playlist-imports",
        headers=_headers(auth, "consent-allowed-key"),
        json={"text": text, "consentExternalProcessing": True},
    )
    assert denied.status_code == allowed.status_code == 201
    assert denied.json()["data"]["importId"] != allowed.json()["data"]["importId"]


def test_audit_log_does_not_store_song_text(client: TestClient, service):
    auth = issue_auth(client)
    secret_title = "Do Not Store This Song Title"
    created = client.post(
        "/v1/playlist-imports",
        headers=_headers(auth, "audit-import-key"),
        json={"text": f"title,artist\n{secret_title},Private Artist"},
    )
    assert created.status_code == 201
    with service.database.connect() as connection:
        serialized = "\n".join(row["detail_json"] for row in connection.execute("SELECT * FROM audit_events"))
    assert secret_title not in serialized
    assert "Private Artist" not in serialized


def test_readiness_discloses_unverified_layers(client: TestClient):
    response = client.get("/readyz")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["state"] == "degraded"
    assert data["ruleOnly"] is True
    assert data["realModelAcceptance"] == "pending"
    assert data["productionAssets"] is False
    assert data["productionReady"] is False
    assert data["frontendImplemented"] is True
