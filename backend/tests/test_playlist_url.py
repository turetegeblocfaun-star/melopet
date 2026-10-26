from __future__ import annotations

import httpx
import pytest

from melopet_backend.errors import AppError
from melopet_backend.netease import (
    extract_netease_playlist_id,
    parse_netease_wiki_signals,
    resolve_netease_playlist,
    tracks_to_playlist_text,
)

from .conftest import issue_auth


def _payload(tracks: list[dict], *, track_count: int | None = None) -> dict:
    return {
        "code": 200,
        "result": {
            "name": "test-playlist",
            "trackCount": track_count if track_count is not None else len(tracks),
            "tracks": tracks,
        },
    }


def _track(name: str, artists: list[str], album: str, duration: int, track_id: int) -> dict:
    return {
        "name": name,
        "artists": [{"name": artist} for artist in artists],
        "album": {"name": album},
        "duration": duration,
        "id": track_id,
    }


def _mock_client(payload: dict, status_code: int = 200) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status_code, json=payload)))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("http://music.163.com/playlist/18236662989/3936224297?userid=3936224297", "18236662989"),
        ("https://music.163.com/playlist?id=18236662989", "18236662989"),
        ("https://music.163.com/#/playlist?id=18236662989", "18236662989"),
        ("https://music.163.com/m/playlist?id=18236662989", "18236662989"),
        (
            "分享雨是未知数的歌单《万兽之王演唱会》"
            "http://music.163.com/playlist/18236662989/3936224297?userid=3936224297 (@网易云音乐)",
            "18236662989",
        ),
        ("title,artist\n晴天,周杰伦", None),
        ("", None),
    ],
)
def test_extract_netease_playlist_id(text: str, expected: str | None):
    assert extract_netease_playlist_id(text) == expected


def test_resolve_netease_playlist_returns_normalized_tracks():
    payload = _payload(
        [
            _track("平庸 (Live)", ["薛之谦"], "音乐缘计划2 第3期", 246424, 3315586072),
            _track("到这", ["郭冠廷"], "反派角色", 248657, 2091351597),
        ]
    )
    client = _mock_client(payload)
    tracks = resolve_netease_playlist("18236662989", client=client, max_tracks=1000)
    client.close()
    assert tracks == [
        {
            "title": "平庸 (Live)",
            "artist": "薛之谦",
            "album": "音乐缘计划2 第3期",
            "duration_ms": 246424,
            "platform_track_id": "3315586072",
        },
        {
            "title": "到这",
            "artist": "郭冠廷",
            "album": "反派角色",
            "duration_ms": 248657,
            "platform_track_id": "2091351597",
        },
    ]


def test_resolve_netease_playlist_joins_multiple_artists_and_defaults_unknown():
    payload = _payload([_track("合唱", ["A", "B"], "", 0, 1)])
    client = _mock_client(payload)
    tracks = resolve_netease_playlist("1", client=client)
    client.close()
    assert tracks[0]["artist"] == "A/B"
    assert tracks[0]["album"] == ""
    assert tracks[0]["duration_ms"] is None


def test_resolve_netease_playlist_empty_raises():
    client = _mock_client({"code": 200, "result": {"trackCount": 0, "tracks": []}})
    with pytest.raises(AppError) as raised:
        resolve_netease_playlist("1", client=client)
    client.close()
    assert raised.value.error_code == "PLAYLIST_EMPTY"


def test_resolve_netease_playlist_not_found_raises():
    client = _mock_client({"code": 20001})
    with pytest.raises(AppError) as raised:
        resolve_netease_playlist("1", client=client)
    client.close()
    assert raised.value.error_code == "PLAYLIST_URL_RESOLVE_FAILED"


def test_resolve_netease_playlist_over_limit_raises():
    tracks = [_track(f"Song {index}", ["Artist"], "Album", 1000, index) for index in range(11)]
    client = _mock_client(_payload(tracks, track_count=11))
    with pytest.raises(AppError) as raised:
        resolve_netease_playlist("1", client=client, max_tracks=10)
    client.close()
    assert raised.value.error_code == "FILE_TOO_LARGE"


def test_tracks_to_playlist_text_uses_expected_headers():
    text = tracks_to_playlist_text(
        [
            {
                "title": "晴天",
                "artist": "周杰伦",
                "album": "叶惠美",
                "duration_ms": 269000,
                "platform_track_id": "186016",
                "genre": "流行-华语流行",
                "mood": "怀旧 / 温暖",
                "bpm": 76,
            }
        ]
    )
    lines = text.splitlines()
    assert lines[0] == "title,artist,album,durationMs,platformTrackId,genre,mood,bpm,tags"
    assert lines[1] == "晴天,周杰伦,叶惠美,269000,186016,流行-华语流行,怀旧 / 温暖,76,"


def test_parse_netease_wiki_signals_extracts_genre_mood_and_bpm():
    def creative(kind: str, *, resources: list[str] | None = None, links: list[str] | None = None):
        return {
            "creativeType": kind,
            "resources": [
                {"uiElement": {"mainTitle": {"title": value}}}
                for value in resources or []
            ],
            "uiElement": {"textLinks": [{"text": value} for value in links or []]},
        }

    payload = {
        "code": 200,
        "data": {
            "blocks": [
                {
                    "code": "SONG_PLAY_ABOUT_SONG_BASIC",
                    "creatives": [
                        creative("songTag", resources=["流行-华语流行", "流行-华语流行"]),
                        creative("songBizTag", resources=["孤独", "悲伤"]),
                        creative("bpm", links=["73"]),
                    ],
                }
            ]
        },
    }
    assert parse_netease_wiki_signals(payload) == {
        "genre": "流行-华语流行",
        "mood": "孤独 / 悲伤",
        "bpm": 73.0,
    }


def test_song_wiki_enrichment_uses_cache_and_only_fetches_misses():
    playlist = _payload(
        [
            _track("Cached", ["A"], "Album", 1000, 1),
            _track("Fetched", ["B"], "Album", 1000, 2),
        ]
    )
    requested_song_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/playlist/detail":
            return httpx.Response(200, json=playlist)
        requested_song_ids.append(request.url.params["songId"])
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "blocks": [
                        {
                            "code": "SONG_PLAY_ABOUT_SONG_BASIC",
                            "creatives": [
                                {
                                    "creativeType": "songBizTag",
                                    "resources": [{"uiElement": {"mainTitle": {"title": "悲伤"}}}],
                                    "uiElement": {},
                                }
                            ],
                        }
                    ]
                },
            },
        )

    stored: dict[str, dict] = {}
    client = httpx.Client(transport=httpx.MockTransport(handler))
    tracks = resolve_netease_playlist(
        "playlist",
        client=client,
        signal_loader=lambda song_ids: {song_id: {"mood": "平静"} for song_id in song_ids if song_id == "1"},
        signal_writer=stored.update,
    )
    client.close()

    assert requested_song_ids == ["2"]
    assert tracks[0]["mood"] == "平静"
    assert tracks[1]["mood"] == "悲伤"
    assert stored == {"2": {"mood": "悲伤"}}


def test_import_netease_url_end_to_end(client, monkeypatch):
    monkeypatch.setattr(
        "melopet_backend.service.resolve_netease_playlist",
        lambda playlist_id, **kwargs: [
            {"title": "平庸 (Live)", "artist": "薛之谦", "album": "音乐缘计划2 第3期", "duration_ms": 246424},
            {"title": "到这", "artist": "郭冠廷", "album": "反派角色", "duration_ms": 248657},
        ],
    )
    auth = issue_auth(client)
    created = client.post(
        "/v1/playlist-imports",
        headers={"Authorization": auth["Authorization"], "Idempotency-Key": "url-import-key-1"},
        json={
            "text": "http://music.163.com/playlist/18236662989/3936224297?userid=3936224297",
            "consentExternalProcessing": False,
        },
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["headers"] == [
        "title",
        "artist",
        "album",
        "durationMs",
        "platformTrackId",
        "genre",
        "mood",
        "bpm",
        "tags",
    ]

    mapped = client.put(
        f"/v1/playlist-imports/{data['importId']}/mapping",
        headers={"Authorization": auth["Authorization"], "Idempotency-Key": "url-mapping-key-1"},
        json={"mapping": {"title": "title", "artist": "artist", "album": "album", "durationMs": "durationMs"}},
    )
    assert mapped.status_code == 200, mapped.text
    assert mapped.json()["data"]["normalizedCount"] == 2


def test_save_mapping_recovers_stale_url_import(client, monkeypatch):
    from melopet_backend.service import MeloPetService

    resolved = [{"title": "平庸 (Live)", "artist": "薛之谦", "album": "音乐缘计划2 第3期", "duration_ms": 246424}]
    monkeypatch.setattr("melopet_backend.service.resolve_netease_playlist", lambda playlist_id, **kwargs: resolved)

    auth = issue_auth(client)
    # Simulate an import created before URL resolution shipped: store the raw link.
    original_resolve = MeloPetService._resolve_playlist_source
    monkeypatch.setattr(
        MeloPetService,
        "_resolve_playlist_source",
        lambda self, data: (data, None),
    )
    created = client.post(
        "/v1/playlist-imports",
        headers={"Authorization": auth["Authorization"], "Idempotency-Key": "stale-url-import-key"},
        json={"text": "http://music.163.com/playlist/18236662989", "consentExternalProcessing": False},
    )
    assert created.status_code == 201, created.text
    import_id = created.json()["data"]["importId"]
    assert created.json()["data"]["headers"] == ["title", "artist"]

    # Restore URL resolution and confirm mapping now recovers the stale import.
    monkeypatch.setattr(MeloPetService, "_resolve_playlist_source", original_resolve)
    mapped = client.put(
        f"/v1/playlist-imports/{import_id}/mapping",
        headers={"Authorization": auth["Authorization"], "Idempotency-Key": "stale-url-mapping-key"},
        json={"mapping": {"title": "title", "artist": "artist"}},
    )
    assert mapped.status_code == 200, mapped.text
    assert mapped.json()["data"]["normalizedCount"] == 1
