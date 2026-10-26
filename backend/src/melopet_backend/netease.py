from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .errors import AppError

NETEASE_PLAYLIST_API = "https://music.163.com/api/playlist/detail"
NETEASE_SONG_WIKI_API = "https://music.163.com/api/song/play/about/block/page"
NETEASE_SIGNAL_PROVIDER_VERSION = "netease-song-wiki@1.0.0"
NETEASE_SIGNAL_WORKERS = 8
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
REFERER = "https://music.163.com/"
DEFAULT_TIMEOUT_SECONDS = 8.0


def extract_netease_playlist_id(text: str) -> str | None:
    """Return the NetEase playlist id found in pasted text, or None.

    Accepts the common share formats:

    * ``https://music.163.com/playlist?id=18236662989``
    * ``https://music.163.com/#/playlist?id=18236662989``
    * ``http://music.163.com/playlist/18236662989/3936224297?userid=3936224297``
    * A full share line such as ``分享雨是未知数的歌单《...》http://music.163.com/... (@网易云音乐)``
    """
    if "music.163.com" not in text:
        return None
    match = re.search(r"[?&#]id=(\d+)", text)
    if match:
        return match.group(1)
    match = re.search(r"/playlist/(\d+)", text)
    if match:
        return match.group(1)
    return None


def _clean_text(value: Any, *, maximum: int = 300) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    return re.sub(r"\s+", " ", normalized)[:maximum]


def tracks_to_playlist_text(tracks: list[dict[str, Any]]) -> str:
    """Serialize resolved tracks as CSV the existing parser can consume."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["title", "artist", "album", "durationMs", "platformTrackId", "genre", "mood", "bpm", "tags"])
    for track in tracks:
        writer.writerow(
            [
                track.get("title", ""),
                track.get("artist", ""),
                track.get("album", ""),
                track.get("duration_ms") or "",
                track.get("platform_track_id", ""),
                track.get("genre", ""),
                track.get("mood", ""),
                track.get("bpm") or "",
                track.get("tags", ""),
            ]
        )
    return buffer.getvalue()


def parse_netease_wiki_signals(payload: Any) -> dict[str, Any]:
    """Extract stable classification fields from the song-wiki response."""
    if not isinstance(payload, dict) or payload.get("code") != 200:
        return {}
    data = payload.get("data")
    blocks = data.get("blocks") if isinstance(data, dict) else None
    if not isinstance(blocks, list):
        return {}

    genres: list[str] = []
    moods: list[str] = []
    bpm: float | None = None
    for block in blocks:
        if not isinstance(block, dict) or block.get("code") != "SONG_PLAY_ABOUT_SONG_BASIC":
            continue
        for creative in block.get("creatives") or []:
            if not isinstance(creative, dict):
                continue
            creative_type = creative.get("creativeType")
            values: list[str] = []
            for resource in creative.get("resources") or []:
                if not isinstance(resource, dict):
                    continue
                ui_element = resource.get("uiElement") or {}
                main_title = ui_element.get("mainTitle") or {}
                value = _clean_text(main_title.get("title"), maximum=80)
                if value:
                    values.append(value)
            ui_element = creative.get("uiElement") or {}
            for text_link in ui_element.get("textLinks") or []:
                if not isinstance(text_link, dict):
                    continue
                value = _clean_text(text_link.get("text"), maximum=80)
                if value:
                    values.append(value)
            if creative_type == "songTag":
                genres.extend(values)
            elif creative_type == "songBizTag":
                moods.extend(values)
            elif creative_type == "bpm" and values:
                try:
                    parsed_bpm = float(values[0])
                except ValueError:
                    continue
                if 20 <= parsed_bpm <= 300:
                    bpm = parsed_bpm

    signals: dict[str, Any] = {}
    if genres:
        signals["genre"] = " / ".join(dict.fromkeys(genres))[:300]
    if moods:
        signals["mood"] = " / ".join(dict.fromkeys(moods))[:300]
    if bpm is not None:
        signals["bpm"] = bpm
    return signals


def _fetch_netease_wiki_signals(client: httpx.Client, song_id: str, timeout: float) -> dict[str, Any]:
    try:
        response = client.get(
            NETEASE_SONG_WIKI_API,
            params={"songId": song_id},
            headers={"User-Agent": USER_AGENT, "Referer": REFERER, "Accept": "application/json"},
            timeout=min(timeout, 4.0),
        )
        response.raise_for_status()
        return parse_netease_wiki_signals(response.json())
    except (httpx.HTTPError, ValueError):
        return {}


def resolve_netease_playlist(
    playlist_id: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_tracks: int = 1000,
    client: httpx.Client | None = None,
    signal_loader: Callable[[list[str]], dict[str, dict[str, Any]]] | None = None,
    signal_writer: Callable[[dict[str, dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    """Fetch a NetEase playlist and return normalized track dictionaries."""
    if client is None:
        with httpx.Client() as owned_client:
            return resolve_netease_playlist(
                playlist_id,
                timeout=timeout,
                max_tracks=max_tracks,
                client=owned_client,
                signal_loader=signal_loader,
                signal_writer=signal_writer,
            )
    params = {"id": playlist_id, "n": max_tracks}
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Accept": "application/json",
    }
    try:
        response = client.get(NETEASE_PLAYLIST_API, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException as exc:
        raise AppError(
            "PLAYLIST_URL_TIMEOUT",
            "歌单链接解析超时，请稍后重试。",
            502,
            details={"provider": "netease"},
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise AppError(
            "PLAYLIST_URL_RESOLVE_FAILED",
            "无法访问这个歌单，请确认链接有效后重试。",
            502,
            details={"provider": "netease"},
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise AppError(
            "PLAYLIST_URL_RESOLVE_FAILED",
            "无法解析歌单链接，请确认链接有效后重试。",
            502,
            details={"provider": "netease"},
        ) from exc
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise AppError(
            "PLAYLIST_URL_RESOLVE_FAILED",
            "无法访问这个歌单，请确认链接有效后重试。",
            502,
            details={"provider": "netease"},
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise AppError("PLAYLIST_EMPTY", "这个歌单里没有可导入的歌曲。", 422)

    track_count = result.get("trackCount")
    if isinstance(track_count, int) and track_count > max_tracks:
        raise AppError(
            "FILE_TOO_LARGE",
            f"当前最多支持 {max_tracks} 首歌曲，这个歌单有 {track_count} 首，请拆分后重试。",
            details={"maxTracks": max_tracks, "receivedTracks": track_count},
        )

    raw_tracks = result.get("tracks") or []
    tracks: list[dict[str, Any]] = []
    for item in raw_tracks[:max_tracks]:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("name"))
        if not title:
            continue
        artists = [_clean_text(artist.get("name")) for artist in (item.get("artists") or []) if isinstance(artist, dict)]
        artist = "/".join(value for value in artists if value) or "未知艺人"
        album = _clean_text((item.get("album") or {}).get("name"))
        duration = item.get("duration")
        duration_ms = int(duration) if isinstance(duration, (int, float)) and duration and duration > 0 else None
        track_id = item.get("id")
        platform_track_id = str(track_id) if isinstance(track_id, (int, str)) and str(track_id).isdigit() else None
        tracks.append(
            {
                "title": title,
                "artist": artist,
                "album": album,
                "duration_ms": duration_ms,
                "platform_track_id": platform_track_id,
            }
        )
    if not tracks:
        raise AppError("PLAYLIST_EMPTY", "这个歌单里没有可导入的歌曲。", 422)

    song_ids = list(
        dict.fromkeys(
            track["platform_track_id"]
            for track in tracks
            if track.get("platform_track_id")
        )
    )
    cached_signals: dict[str, dict[str, Any]] = {}
    if signal_loader and song_ids:
        try:
            cached_signals = signal_loader(song_ids)
        except Exception:
            cached_signals = {}
    missing_song_ids = [song_id for song_id in song_ids if song_id not in cached_signals]
    fetched_signals: dict[str, dict[str, Any]] = {}
    if missing_song_ids:
        workers = min(NETEASE_SIGNAL_WORKERS, len(missing_song_ids))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="netease-signal") as executor:
            pending = {
                executor.submit(_fetch_netease_wiki_signals, client, song_id, timeout): song_id
                for song_id in missing_song_ids
            }
            for future in as_completed(pending):
                song_id = pending[future]
                try:
                    fetched_signals[song_id] = future.result()
                except Exception:
                    fetched_signals[song_id] = {}
        if signal_writer:
            try:
                signal_writer(fetched_signals)
            except Exception:
                pass

    signal_by_song_id = {**cached_signals, **fetched_signals}
    for track in tracks:
        song_id = track.get("platform_track_id")
        if song_id:
            track.update(signal_by_song_id.get(song_id, {}))
    return tracks
