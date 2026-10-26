from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from melopet_backend.errors import AppError
from melopet_backend.playlist import decode_input, normalize_tracks, parse_table, suggest_mapping
from melopet_backend.schemas import ColumnMapping
from melopet_backend.worker import Worker

from .conftest import create_mapped_import, issue_auth, known_playlist_csv


def _headers(auth: dict[str, str], key: str) -> dict[str, str]:
    return {"Authorization": auth["Authorization"], "Idempotency-Key": key}


@pytest.mark.parametrize("delimiter", [",", "\t", ";", "|"])
def test_supported_delimiters_and_utf8_bom(delimiter: str):
    content = f"title{delimiter}artist{delimiter}genre\nOne{delimiter}A{delimiter}rock".encode()
    table = parse_table(b"\xef\xbb\xbf" + content, 1000)
    assert table.headers == ["title", "artist", "genre"]
    assert table.rows[0]["title"] == "One"


@pytest.mark.parametrize(
    ("content", "error_code"),
    [
        (b"", "FILE_EMPTY"),
        (b"title,artist\x00\nOne,A", "FILE_DAMAGED"),
        ("title,artist\n歌曲,歌手".encode("utf-16"), "FILE_DAMAGED"),
        (b"title,title\nOne,A", "FILE_DAMAGED"),
    ],
)
def test_damaged_and_unsupported_inputs_fail_closed(content: bytes, error_code: str):
    with pytest.raises(AppError) as raised:
        parse_table(content, 1000)
    assert raised.value.error_code == error_code


def test_decode_rejects_invalid_utf8_without_nul():
    with pytest.raises(AppError) as raised:
        decode_input(b"\xff\xfeabc")
    assert raised.value.error_code == "FILE_ENCODING_UNSUPPORTED"


def test_track_limit_is_enforced_before_persistence():
    content = "title,artist\n" + "\n".join(f"Song {index},Artist {index}" for index in range(11))
    with pytest.raises(AppError) as raised:
        parse_table(content.encode(), 10)
    assert raised.value.error_code == "FILE_TOO_LARGE"
    assert raised.value.details == {"maxTracks": 10, "receivedTracks": 11}


def test_fast_audio_feature_columns_are_mapped_and_validated():
    content = (
        "title,artist,audioEnergy,audioBrightness,audioDynamics,audioPulse,tempoConfidence,audioFeatureVersion\n"
        "One,A,0.82,0.61,0.32,0.55,0.74,melopet-audio-features@1.0.0\n"
        "Two,B,1.2,invalid,0.4,0.3,0.5,melopet-audio-features@1.0.0"
    )
    table = parse_table(content.encode(), 1000)
    mapping = suggest_mapping(table.headers)
    result = normalize_tracks(table, ColumnMapping.model_validate(mapping))

    assert result.tracks[0].allowed_signals == {
        "audioEnergy": 0.82,
        "audioBrightness": 0.61,
        "audioDynamics": 0.32,
        "audioPulse": 0.55,
        "tempoConfidence": 0.74,
        "audioFeatureVersion": "melopet-audio-features@1.0.0",
    }
    assert "audioEnergy" not in result.tracks[1].allowed_signals
    assert "audioBrightness" not in result.tracks[1].allowed_signals
    assert [issue["field"] for issue in result.issues] == ["audioEnergy", "audioBrightness"]


def test_file_extension_content_type_and_mapping_are_validated(client: TestClient):
    auth = issue_auth(client)
    unsupported = client.post(
        "/v1/playlist-imports",
        headers=_headers(auth, "unsupported-file-key"),
        files={"file": ("playlist.exe", b"title,artist\nOne,A", "text/csv")},
    )
    assert unsupported.status_code == 400
    assert unsupported.json()["errorCode"] == "FILE_UNSUPPORTED"

    spoofed = client.post(
        "/v1/playlist-imports",
        headers=_headers(auth, "spoofed-file-key"),
        files={"file": ("playlist.csv", b"title,artist\nOne,A", "image/png")},
    )
    assert spoofed.status_code == 400
    assert spoofed.json()["errorCode"] == "FILE_SPOOFED"

    created = client.post(
        "/v1/playlist-imports",
        headers=_headers(auth, "mapping-validation-import"),
        json={"text": "title,artist\nOne,A"},
    ).json()["data"]
    same_column = client.put(
        f"/v1/playlist-imports/{created['importId']}/mapping",
        headers=_headers(auth, "mapping-same-column"),
        json={"mapping": {"title": "title", "artist": "title"}},
    )
    assert same_column.status_code == 422
    missing_column = client.put(
        f"/v1/playlist-imports/{created['importId']}/mapping",
        headers=_headers(auth, "mapping-missing-column"),
        json={"mapping": {"title": "title", "artist": "missing"}},
    )
    assert missing_column.status_code == 400
    assert missing_column.json()["errorCode"] == "MAPPING_INVALID"


@pytest.mark.parametrize("track_count", [30, 100, 500, 1000])
def test_rule_only_backend_scales_to_documented_playlist_sizes(
    track_count: int,
    client: TestClient,
    service,
):
    started = time.monotonic()
    auth = issue_auth(client)
    mapped = create_mapped_import(client, auth, known_playlist_csv(track_count))
    assert mapped["normalizedCount"] == track_count
    response = client.post(
        f"/v1/playlist-imports/{mapped['importId']}/analyses",
        headers=_headers(auth, f"scale-analysis-{track_count}"),
    )
    task_id = response.json()["data"]["taskId"]
    assert Worker(service).run_once()
    task = client.get(
        f"/v1/tasks/{task_id}", headers={"Authorization": auth["Authorization"]}
    ).json()["data"]
    assert task["status"] == "PROFILE_READY"
    profile = client.get(
        f"/v1/mood-profiles/{task['output']['profileRevision']}?page_size=200",
        headers={"Authorization": auth["Authorization"]},
    ).json()["data"]
    assert profile["knownCount"] == track_count
    assert profile["unknownCount"] == 0
    assert sum(item["percent"] for item in profile["percentages"]) == 100
    assert time.monotonic() - started < 20
