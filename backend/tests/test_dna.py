from __future__ import annotations

from melopet_backend.catalogs import load_catalogs
from melopet_backend.config import Settings
from melopet_backend.dna import build_dna


def _catalogs(tmp_path) -> object:
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'dna.db'}",
        object_store_path=tmp_path / "objects",
        preview_store_path=tmp_path / "previews",
    )
    return load_catalogs(settings)


def test_dna_derives_energy_keywords_and_summary(tmp_path):
    catalogs = _catalogs(tmp_path)
    dna = build_dna(
        percentages=[
            {"moodId": "healing_warm", "percent": 60},
            {"moodId": "calm_focus", "percent": 30},
            {"moodId": "UNKNOWN", "percent": 10},
        ],
        representative_tracks=[{"title": "晴天", "artist": "周杰伦"}],
        catalogs=catalogs,
    )
    assert dna["genres"] == ["民谣 / 舒缓流行", "氛围 / Lo-fi"]
    assert dna["energy"] == 36  # (40*60 + 24*30 + 50*10) / 100
    assert "治愈" in dna["keywords"]
    assert "平静" in dna["keywords"]
    assert dna["summary"]
    assert dna["representativeTracks"] == [{"title": "晴天", "artist": "周杰伦"}]
    assert "治愈" in dna["explanation"]


def test_dna_unknown_profile_is_safe(tmp_path):
    catalogs = _catalogs(tmp_path)
    dna = build_dna(
        percentages=[{"moodId": "UNKNOWN", "percent": 100}],
        representative_tracks=[],
        catalogs=catalogs,
    )
    assert dna["genres"] == ["多元风格"]
    assert dna["energy"] == 50
    assert dna["keywords"]
    assert dna["summary"]
    assert "信号不足" in dna["explanation"]
