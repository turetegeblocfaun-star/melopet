from __future__ import annotations

from fastapi.testclient import TestClient

from melopet_backend.catalogs import load_catalogs
from melopet_backend.config import Settings
from melopet_backend.pet import build_image_prompt
from melopet_backend.worker import Worker

from .conftest import create_mapped_import, issue_auth, known_playlist_csv


def _headers(auth: dict[str, str], key: str) -> dict[str, str]:
    return {"Authorization": auth["Authorization"], "Idempotency-Key": key}


def _confirmed_revision(client: TestClient, service) -> tuple[dict[str, str], str]:
    auth = issue_auth(client)
    mapped = create_mapped_import(client, auth, known_playlist_csv())
    analysis = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_headers(auth, "custom-analysis-key"),
    )
    assert analysis.status_code == 202
    assert Worker(service).run_once()
    task_id = analysis.json()["data"]["taskId"]
    task = client.get(f"/v1/tasks/{task_id}", headers={"Authorization": auth["Authorization"]}).json()["data"]
    revision = task["output"]["profileRevision"]
    confirmed = client.post(
        f"/v1/mood-profiles/{revision}/confirm",
        headers=_headers(auth, "custom-confirm-key"),
    )
    assert confirmed.status_code == 200
    return auth, revision


def test_pet_asset_catalog_is_exposed(client: TestClient):
    auth = issue_auth(client)
    response = client.get("/v1/catalogs/pet-assets", headers={"Authorization": auth["Authorization"]})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["version"] == "dev-0.1"
    assert {item["id"] for item in data["species"]} >= {"species_cat_dev", "species_fox_dev", "species_spirit_dev"}
    assert all("colors" in item for item in data["palettes"])
    assert data["headphone"]["id"] == "accessory_headphone_dev"


def test_pet_customization_overrides_spec(client: TestClient, service):
    auth, revision = _confirmed_revision(client, service)
    response = client.post(
        f"/v1/mood-profiles/{revision}/pet-candidates",
        headers=_headers(auth, "custom-pet-key"),
        json={
            "customization": {
                "speciesId": "species_fox_dev",
                "paletteId": "palette_rose_dev",
                "motionId": "motion_bounce_dev",
                "headphone": True,
            }
        },
    )
    assert response.status_code == 202, response.text
    task_id = response.json()["data"]["taskId"]
    assert Worker(service).run_once()
    candidates = client.get(
        f"/v1/pet-candidates/{task_id}",
        headers={"Authorization": auth["Authorization"]},
    ).json()["data"]["candidates"]
    assert len(candidates) == 3
    for candidate in candidates:
        spec = candidate["petSpec"]
        assert spec["speciesId"] == "species_fox_dev"
        assert spec["bodyPaletteId"] == "palette_rose_dev"
        assert spec["idleMotionId"] == "motion_bounce_dev"
        assert spec["accessoryIds"] == ["accessory_headphone_dev"]
        assert set(spec["customized"]) == {"speciesId", "bodyPaletteId", "idleMotionId", "accessoryIds"}


def test_pet_customization_rejects_invalid_asset(client: TestClient, service):
    auth, revision = _confirmed_revision(client, service)
    response = client.post(
        f"/v1/mood-profiles/{revision}/pet-candidates",
        headers=_headers(auth, "custom-bad-key"),
        json={"customization": {"speciesId": "species_dragon_dev"}},
    )
    assert response.status_code == 400
    assert response.json()["errorCode"] == "PET_CUSTOMIZATION_INVALID"


def test_character_card_returns_svg(client: TestClient, service):
    auth, revision = _confirmed_revision(client, service)
    task = client.post(
        f"/v1/mood-profiles/{revision}/pet-candidates",
        headers=_headers(auth, "card-pet-key"),
    ).json()["data"]
    assert Worker(service).run_once()
    candidates = client.get(
        f"/v1/pet-candidates/{task['taskId']}",
        headers={"Authorization": auth["Authorization"]},
    ).json()["data"]["candidates"]
    card = client.get(
        f"/v1/pet-candidates/{task['taskId']}/cards/{candidates[0]['candidateRecordId']}",
        headers={"Authorization": auth["Authorization"]},
    )
    assert card.status_code == 200
    assert card.headers["content-type"].startswith("image/svg+xml")
    assert "歌单角色卡".encode() in card.content
    assert "能量值".encode() in card.content


def test_build_image_prompt_follows_ui_spec(tmp_path):
    catalogs = load_catalogs(Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'prompt.db'}",
        object_store_path=tmp_path / "objects",
        preview_store_path=tmp_path / "previews",
    ))
    spec = {
        "speciesId": "species_fox_dev",
        "bodyPaletteId": "palette_rose_dev",
        "accessoryIds": ["accessory_headphone_dev"],
        "idleMotionId": "motion_float_dev",
    }
    prompt = build_image_prompt(
        spec,
        {"petName": "小晚", "intro": "温柔、慢热、轻怀旧"},
        {"percentages": [{"moodId": "healing_warm", "percent": 60}]},
        catalogs,
    )
    assert "狐狸" in prompt
    assert "小晚" in prompt
    assert "大耳机" in prompt
    assert "Q版" in prompt
    assert "1:1.5" in prompt
    assert "干净纯色" in prompt
    assert catalogs.mapping["paletteColors"]["palette_rose_dev"][0] in prompt
