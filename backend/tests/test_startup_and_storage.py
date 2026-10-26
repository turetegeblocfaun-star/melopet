from __future__ import annotations

from pathlib import Path

import pytest

from melopet_backend.config import Settings
from melopet_backend.model_gateway import build_model_gateway
from melopet_backend.object_store import LocalObjectStore
from melopet_backend.service import MeloPetService


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "app_env": "test",
        "database_url": f"sqlite:///{tmp_path / 'test.db'}",
        "object_store_path": tmp_path / "objects",
        "preview_store_path": tmp_path / "previews",
        "capability_token_pepper": "test-pepper",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_local_object_store_is_atomic_and_rejects_path_escape(tmp_path: Path):
    store = LocalObjectStore(tmp_path / "objects")
    object_ref = store.put(b"complete-content", suffix=".txt")
    assert store.get(object_ref) == b"complete-content"
    assert not list((tmp_path / "objects").glob(".upload-*"))
    with pytest.raises(ValueError, match="invalid object reference"):
        store.get("../outside.txt")
    store.delete(object_ref)
    assert not (tmp_path / "objects" / object_ref).exists()


def test_unknown_or_incomplete_model_provider_fails_startup(tmp_path: Path):
    with pytest.raises(ValueError, match="MODEL_PROVIDER_UNSUPPORTED"):
        build_model_gateway(_settings(tmp_path, model_provider="unknown"))
    with pytest.raises(ValueError, match="MODEL_CONFIGURATION_INCOMPLETE"):
        build_model_gateway(_settings(tmp_path, model_provider="openai_compatible"))


def test_unapproved_music_provider_fails_closed(tmp_path: Path):
    with pytest.raises(ValueError, match="MUSIC_SIGNAL_PROVIDER_NOT_APPROVED_OR_IMPLEMENTED"):
        MeloPetService(_settings(tmp_path, music_signal_provider="unapproved-provider"))


def test_missing_versioned_prompt_fails_model_startup(tmp_path: Path):
    settings = _settings(
        tmp_path,
        model_provider="openai_compatible",
        model_base_url="https://model.invalid/v1",
        model_api_key="test-key",
        mood_model_id="mood",
        copy_model_id="copy",
        model_daily_budget=1,
        model_input_cost_per_1m=1,
        prompt_mood_version="missing-prompt@1",
    )
    with pytest.raises(ValueError, match="MODEL_PROMPT_VERSION_NOT_FOUND"):
        MeloPetService(settings)


def test_production_refuses_local_database_dev_secret_and_dev_assets(tmp_path: Path):
    settings = _settings(
        tmp_path,
        app_env="production",
        capability_token_pepper="dev-only-secret",
    )
    with pytest.raises(ValueError) as raised:
        MeloPetService(settings)
    message = str(raised.value)
    assert "PRODUCTION_DATABASE_ADAPTER_NOT_CONFIGURED" in message
    assert "INSECURE_CAPABILITY_TOKEN_PEPPER" in message
    assert "PRODUCTION_ASSET_CATALOG_NOT_CONFIGURED" in message
