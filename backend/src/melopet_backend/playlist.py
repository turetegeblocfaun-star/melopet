from __future__ import annotations

import csv
import io
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .errors import AppError
from .schemas import ColumnMapping
from .util import json_hash, new_id

PARSER_VERSION = "playlist-parser@1.2.0"
ALLOWED_UPLOAD_SUFFIXES = {".csv", ".txt"}
ALLOWED_CONTENT_TYPES = {
    "text/csv",
    "text/plain",
    "application/csv",
    "application/vnd.ms-excel",
    "application/octet-stream",
}
FORMULA_PREFIXES = ("=", "+", "-", "@")


@dataclass(slots=True)
class ParsedTable:
    headers: list[str]
    rows: list[dict[str, str]]
    input_kind: str


@dataclass(slots=True)
class NormalizedTrack:
    track_ref: str
    row_ref: int
    title: str
    artist: str
    album: str | None
    duration_ms: int | None
    platform_track_id: str | None
    allowed_signals: dict[str, Any]
    match_status: str
    excluded: bool
    duplicate_of: str | None


@dataclass(slots=True)
class NormalizationResult:
    tracks: list[NormalizedTrack]
    issues: list[dict[str, Any]]
    valid_count: int
    invalid_count: int
    duplicate_count: int
    content_hash: str


def decode_input(data: bytes) -> str:
    if not data:
        raise AppError("FILE_EMPTY", "文件或粘贴内容为空，请按模板补充歌曲。")
    if b"\x00" in data:
        raise AppError("FILE_DAMAGED", "内容包含无法解析的二进制数据，请重新导出 UTF-8 CSV。")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError("FILE_ENCODING_UNSUPPORTED", "仅支持 UTF-8 编码，请重新导出后上传。") from exc


def _clean_header(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def _parse_plain_lines(lines: list[str]) -> ParsedTable:
    rows: list[dict[str, str]] = []
    for line in lines:
        parts = re.split(r"\s(?:-|—|–)\s", line, maxsplit=1)
        if len(parts) == 2:
            rows.append({"title": parts[0], "artist": parts[1]})
        else:
            rows.append({"title": line, "artist": ""})
    return ParsedTable(["title", "artist"], rows, "text")


def parse_table(data: bytes, max_tracks: int) -> ParsedTable:
    text = decode_input(data)
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise AppError("FILE_EMPTY", "文件或粘贴内容为空，请按模板补充歌曲。")

    first = lines[0]
    delimiters = [delimiter for delimiter in (",", "\t", ";", "|") if delimiter in first]
    if not delimiters:
        table = _parse_plain_lines(lines)
    else:
        delimiter = max(delimiters, key=first.count)
        reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
        raw_rows = list(reader)
        if not raw_rows:
            raise AppError("FILE_EMPTY", "文件中没有可读取的数据。")
        headers = [_clean_header(value) for value in raw_rows[0]]
        if any(not value for value in headers) or len(headers) != len(set(headers)):
            raise AppError("FILE_DAMAGED", "表头为空或重复，请修正后重新上传。")
        rows = [
            {header: (values[index] if index < len(values) else "") for index, header in enumerate(headers)}
            for values in raw_rows[1:]
        ]
        table = ParsedTable(headers, rows, "csv")

    if len(table.rows) > max_tracks:
        raise AppError(
            "FILE_TOO_LARGE",
            f"当前最多支持 {max_tracks} 首歌曲，请拆分歌单后重试。",
            details={"maxTracks": max_tracks, "receivedTracks": len(table.rows)},
        )
    return table


def suggest_mapping(headers: list[str]) -> dict[str, str | None]:
    aliases = {
        "title": ["title", "track", "track_name", "song", "song_name", "歌名", "歌曲"],
        "artist": ["artist", "artist_name", "singer", "艺人", "歌手"],
        "album": ["album", "album_name", "专辑"],
        "durationMs": ["durationMs", "duration_ms", "duration", "时长"],
        "platformTrackId": ["platformTrackId", "track_id", "id", "歌曲id"],
        "genre": ["genre", "流派", "风格"],
        "mood": ["mood", "氛围", "情绪"],
        "bpm": ["bpm", "tempo", "速度"],
        "tags": ["tags", "tag", "标签"],
        "audioEnergy": ["audioEnergy", "audio_energy"],
        "audioBrightness": ["audioBrightness", "audio_brightness"],
        "audioDynamics": ["audioDynamics", "audio_dynamics"],
        "audioPulse": ["audioPulse", "audio_pulse"],
        "tempoConfidence": ["tempoConfidence", "tempo_confidence"],
        "audioFeatureVersion": ["audioFeatureVersion", "audio_feature_version"],
    }
    lowered = {header.casefold(): header for header in headers}
    result: dict[str, str | None] = {}
    for field, choices in aliases.items():
        result[field] = next((lowered[item.casefold()] for item in choices if item.casefold() in lowered), None)
    return result


def _normalize_text(value: str, *, maximum: int = 300) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized[:maximum]


def _optional_value(row: dict[str, str], column: str | None) -> str | None:
    if not column:
        return None
    value = _normalize_text(row.get(column, ""))
    return value or None


def _integer(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(float(value))
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _bounded_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) and 0 <= parsed <= 1 else None


def normalize_tracks(table: ParsedTable, mapping: ColumnMapping) -> NormalizationResult:
    columns = [value for value in mapping.model_dump(by_alias=True).values() if value]
    missing = sorted(set(columns) - set(table.headers))
    if missing:
        raise AppError(
            "MAPPING_INVALID",
            "映射引用了不存在的列，请重新选择。",
            details={"missingColumns": missing},
        )

    tracks: list[NormalizedTrack] = []
    issues: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    duplicate_count = 0
    invalid_count = 0
    for index, row in enumerate(table.rows, start=2 if table.input_kind == "csv" else 1):
        title = _normalize_text(row.get(mapping.title, ""))
        artist = _normalize_text(row.get(mapping.artist, ""))
        if not title or not artist:
            invalid_count += 1
            issues.append(
                {
                    "row": index,
                    "code": "MISSING_REQUIRED_FIELD",
                    "fields": [field for field, value in (("title", title), ("artist", artist)) if not value],
                }
            )
            continue
        dedupe_key = f"{title.casefold()}\x1f{artist.casefold()}"
        track_ref = new_id("track")
        duplicate_of = seen.get(dedupe_key)
        excluded = duplicate_of is not None
        if excluded:
            duplicate_count += 1
            issues.append({"row": index, "code": "DUPLICATE_KEEP_FIRST"})
        else:
            seen[dedupe_key] = track_ref

        signals: dict[str, Any] = {}
        for field in ("genre", "mood", "tags"):
            column = getattr(mapping, field)
            value = _optional_value(row, column)
            if value:
                signals[field] = value
                if value.startswith(FORMULA_PREFIXES):
                    issues.append({"row": index, "code": "UNTRUSTED_CELL_PREFIX", "field": field})
        bpm_value = _optional_value(row, mapping.bpm)
        if bpm_value:
            try:
                bpm = float(bpm_value)
            except ValueError:
                issues.append({"row": index, "code": "INVALID_BPM"})
            else:
                if 20 <= bpm <= 300:
                    signals["bpm"] = bpm
                else:
                    issues.append({"row": index, "code": "INVALID_BPM"})

        audio_fields = (
            ("audioEnergy", mapping.audio_energy),
            ("audioBrightness", mapping.audio_brightness),
            ("audioDynamics", mapping.audio_dynamics),
            ("audioPulse", mapping.audio_pulse),
            ("tempoConfidence", mapping.tempo_confidence),
        )
        for field, column in audio_fields:
            raw_value = _optional_value(row, column)
            if raw_value is None:
                continue
            parsed_value = _bounded_float(raw_value)
            if parsed_value is None:
                issues.append({"row": index, "code": "INVALID_AUDIO_FEATURE", "field": field})
            else:
                signals[field] = parsed_value
        feature_version = _optional_value(row, mapping.audio_feature_version)
        if feature_version:
            signals["audioFeatureVersion"] = feature_version[:120]

        tracks.append(
            NormalizedTrack(
                track_ref=track_ref,
                row_ref=index,
                title=title,
                artist=artist,
                album=_optional_value(row, mapping.album),
                duration_ms=_integer(_optional_value(row, mapping.duration_ms)),
                platform_track_id=_optional_value(row, mapping.platform_track_id),
                allowed_signals=signals,
                match_status="UPLOADED_SIGNALS" if signals else "MANUAL_ONLY",
                excluded=excluded,
                duplicate_of=duplicate_of,
            )
        )
    content_hash = json_hash(
        [
            {
                "title": track.title,
                "artist": track.artist,
                "signals": track.allowed_signals,
                "excluded": track.excluded,
            }
            for track in tracks
        ]
    )
    return NormalizationResult(
        tracks=tracks,
        issues=issues,
        valid_count=sum(not track.excluded for track in tracks),
        invalid_count=invalid_count,
        duplicate_count=duplicate_count,
        content_hash=content_hash,
    )
