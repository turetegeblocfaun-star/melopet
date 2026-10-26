from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from melopet_backend.api import create_app
from melopet_backend.config import Settings
from melopet_backend.service import MeloPetService


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        object_store_path=tmp_path / "objects",
        preview_store_path=tmp_path / "previews",
        capability_token_pepper="test-pepper-that-is-not-used-in-production",
        model_provider="disabled",
        music_signal_provider="disabled",
    )


@pytest.fixture
def service(settings: Settings) -> MeloPetService:
    return MeloPetService(settings)


@pytest.fixture
def client(settings: Settings, service: MeloPetService):
    app = create_app(settings, service)
    with TestClient(app) as test_client:
        yield test_client


def issue_auth(client: TestClient) -> dict[str, str]:
    response = client.post("/v1/capabilities/anonymous")
    assert response.status_code == 201
    data = response.json()["data"]
    return {
        "Authorization": f"Bearer {data['capabilityToken']}",
        "ownerRef": data["ownerRef"],
    }


def known_playlist_csv(count: int = 12) -> str:
    genres = ["rock energetic", "happy pop", "ambient focus", "warm acoustic"]
    rows = ["title,artist,genre"]
    rows.extend(f"Song {index},Artist {index},{genres[index % len(genres)]}" for index in range(count))
    return "\n".join(rows)


def create_mapped_import(client: TestClient, auth: dict[str, str], text: str) -> dict:
    headers = {"Authorization": auth["Authorization"], "Idempotency-Key": "import-key-001"}
    created = client.post(
        "/v1/playlist-imports",
        headers=headers,
        json={"text": text, "consentExternalProcessing": False},
    )
    assert created.status_code == 201, created.text
    import_id = created.json()["data"]["importId"]
    mapped = client.put(
        f"/v1/playlist-imports/{import_id}/mapping",
        headers={"Authorization": auth["Authorization"], "Idempotency-Key": "mapping-key-001"},
        json={"mapping": {"title": "title", "artist": "artist", "genre": "genre"}},
    )
    assert mapped.status_code == 200, mapped.text
    return {"importId": import_id, **mapped.json()["data"]}
