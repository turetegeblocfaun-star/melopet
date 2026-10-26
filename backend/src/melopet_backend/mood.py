from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .catalogs import CatalogBundle
from .model_gateway import ModelCallResult, ModelGateway

AUDIO_FEATURE_VERSION = "melopet-audio-features@1.0.0"
AUDIO_SIGNAL_FIELDS = [
    "audioEnergy",
    "audioBrightness",
    "audioDynamics",
    "audioPulse",
    "tempoConfidence",
    "audioFeatureVersion",
]


@dataclass(slots=True)
class ClassifiedTrack:
    track_ref: str
    auto_mood_id: str
    final_mood_id: str
    confidence_band: str
    reason_code: str
    source_mode: str
    source_refs: list[str]
    evidence: list[str]
    excluded: bool
    correction_source: str = "automatic"


@dataclass(slots=True)
class ClassificationBatch:
    tracks: list[ClassifiedTrack]
    model_call: ModelCallResult | None


@dataclass(slots=True)
class ProfileComputation:
    counts: dict[str, int]
    percentages: list[dict[str, int | str]]
    known: int
    unknown: int
    excluded: int
    normalized_total: int
    quality_flags: list[str]


def _rule_candidate(signals: dict[str, Any], catalogs: CatalogBundle) -> tuple[str, float, list[str]]:
    if not signals:
        return "UNKNOWN", 0.0, []
    text_fields = {key: str(value).casefold() for key, value in signals.items() if key in {"genre", "mood", "tags"}}
    scores: Counter[str] = Counter()
    evidence: dict[str, list[str]] = {}
    field_weights = {"mood": 4.0, "genre": 2.6, "tags": 1.6}
    for mood in catalogs.taxonomy:
        mood_id = mood["moodId"]
        if mood_id == "UNKNOWN":
            continue
        for field, value in text_fields.items():
            match_count = sum(keyword.casefold() in value for keyword in mood.get("keywords", []))
            if match_count:
                multiplier = 1.5 if field == "mood" and match_count >= 2 else 1.0
                if field == "tags" and match_count >= 2:
                    multiplier = 1.5
                scores[mood_id] += field_weights[field] * multiplier
                evidence.setdefault(mood_id, []).append(f"{field}:semantic_match")
    bpm = signals.get("bpm")
    if isinstance(bpm, (int, float)):
        if bpm >= 150:
            scores["heroic_drive"] += 0.8
            evidence.setdefault("heroic_drive", []).append("bpm:high")
        elif 115 <= bpm < 150:
            scores["joy_bright"] += 0.7
            evidence.setdefault("joy_bright", []).append("bpm:medium_high")
        elif bpm <= 80:
            scores["calm_focus"] += 0.7
            evidence.setdefault("calm_focus", []).append("bpm:low")

    def audio_value(field: str) -> float | None:
        value = signals.get(field)
        if isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1:
            return float(value)
        return None

    if signals.get("audioFeatureVersion") == AUDIO_FEATURE_VERSION:
        energy = audio_value("audioEnergy")
        brightness = audio_value("audioBrightness")
        pulse = audio_value("audioPulse")
        tempo_confidence = audio_value("tempoConfidence")
        reliable_bpm = bpm if isinstance(bpm, (int, float)) and (tempo_confidence or 0) >= 0.3 else None
        is_heroic = (
            energy is not None
            and pulse is not None
            and energy >= 0.72
            and pulse >= 0.35
            and ((reliable_bpm is not None and reliable_bpm >= 132) or (brightness or 0) >= 0.58)
        )
        is_joy = (
            energy is not None
            and brightness is not None
            and pulse is not None
            and 0.46 <= energy < 0.72
            and brightness >= 0.42
            and pulse >= 0.28
            and reliable_bpm is not None
            and 96 <= reliable_bpm < 150
        )
        is_calm = (
            energy is not None
            and pulse is not None
            and energy <= 0.38
            and pulse <= 0.25
            and (reliable_bpm is None or reliable_bpm <= 100)
        )
        if is_heroic:
            scores["heroic_drive"] += 2.5
            evidence.setdefault("heroic_drive", []).extend(["audioEnergy:high", "audioPulse:strong"])
        elif is_joy:
            scores["joy_bright"] += 2.3
            evidence.setdefault("joy_bright", []).extend(["audioBrightness:bright", "audioPulse:steady"])
        elif is_calm:
            scores["calm_focus"] += 2.3
            evidence.setdefault("calm_focus", []).extend(["audioEnergy:low", "audioPulse:weak"])
    if not scores:
        return "UNKNOWN", 0.0, []
    ordered = {mood_id: index for index, mood_id in enumerate(catalogs.taxonomy_order)}
    ranked = sorted(scores.items(), key=lambda item: (-item[1], ordered[item[0]]))
    winner, winner_score = ranked[0]
    runner_up_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = winner_score - runner_up_score
    if winner_score < 2.0 or margin < 0.7:
        return "UNKNOWN", 0.4, ["signals:conflict"]
    confidence = min(0.95, 0.52 + winner_score * 0.08 + margin * 0.025)
    return winner, confidence, list(dict.fromkeys(evidence.get(winner, [])))[:8]


def classify_tracks(
    tracks: list[dict[str, Any]],
    catalogs: CatalogBundle,
    gateway: ModelGateway,
    *,
    model_idempotency_key: str | None = None,
) -> ClassificationBatch:
    classified: dict[str, ClassifiedTrack] = {}
    model_candidates: list[dict[str, Any]] = []
    for track in tracks:
        track_ref = track["trackRef"]
        if track["excluded"]:
            classified[track_ref] = ClassifiedTrack(
                track_ref,
                "UNKNOWN",
                "UNKNOWN",
                "待确认",
                "EXCLUDED_DUPLICATE",
                "uploaded_signals" if track["allowedSignals"] else "manual_only",
                [],
                [],
                True,
            )
            continue
        proposed, score, evidence = _rule_candidate(track["allowedSignals"], catalogs)
        source_mode = "uploaded_signals" if track["allowedSignals"] else "manual_only"
        source_refs = [f"upload:{track_ref}:{field}" for field in track["allowedSignals"]]
        if proposed != "UNKNOWN":
            classified[track_ref] = ClassifiedTrack(
                track_ref,
                proposed,
                proposed,
                "高" if score >= 0.8 else "中",
                "MATCHED_RULE",
                source_mode,
                source_refs,
                evidence,
                False,
            )
        else:
            classified[track_ref] = ClassifiedTrack(
                track_ref,
                "UNKNOWN",
                "UNKNOWN",
                "待确认",
                "MISSING_SIGNAL" if not track["allowedSignals"] else "OUT_OF_TAXONOMY",
                source_mode,
                source_refs,
                evidence,
                False,
            )
            model_candidates.append(
                {
                    "trackRef": track_ref,
                    "title": track.get("title", ""),
                    "artist": track.get("artist", ""),
                    "signals": track["allowedSignals"],
                    "sourceRefs": source_refs,
                }
            )

    model_call: ModelCallResult | None = None
    if model_candidates:
        payload = {
            "task_type": "song_mood_classification",
            "taxonomyVersion": catalogs.taxonomy_version,
            "taxonomy": [
                {
                    "moodId": mood["moodId"],
                    "definition": mood["definition"],
                    "boundary": mood["boundary"],
                }
                for mood in catalogs.taxonomy
            ],
            "sourcePolicy": {
                "allowedSignalFields": ["genre", "mood", "tags", "bpm", *AUDIO_SIGNAL_FIELDS],
                "unknownWhenInsufficient": True,
            },
            "tracks": model_candidates,
        }
        model_call = gateway.classify_moods(payload, idempotency_key=model_idempotency_key)
        if model_call.output is not None and getattr(model_call.output, "status", None) == "ok":
            requested = {item["trackRef"] for item in model_candidates}
            for item in model_call.output.items:  # type: ignore[attr-defined]
                if item.track_ref not in requested:
                    continue
                proposed = item.proposed_primary_mood_id
                if proposed not in catalogs.taxonomy_by_id or item.score < 0.3:
                    continue
                prior = classified[item.track_ref]
                prior.auto_mood_id = proposed
                prior.final_mood_id = proposed
                prior.confidence_band = "高" if item.score >= 0.8 else ("中" if item.score >= 0.5 else "低")
                prior.reason_code = item.reason_code
                prior.evidence = item.evidence_summary[:8]

    ordered_tracks = [classified[track["trackRef"]] for track in tracks]
    return ClassificationBatch(ordered_tracks, model_call)


def largest_remainder(counts: dict[str, int], order: list[str]) -> list[dict[str, int | str]]:
    total = sum(counts.values())
    if total <= 0:
        return []
    exact = {key: value * 100 / total for key, value in counts.items() if value > 0}
    floors = {key: math.floor(value) for key, value in exact.items()}
    remaining = 100 - sum(floors.values())
    order_index = {key: index for index, key in enumerate(order)}
    ranked = sorted(
        exact,
        key=lambda key: (-(exact[key] - floors[key]), order_index.get(key, len(order))),
    )
    for key in ranked[:remaining]:
        floors[key] += 1
    return [{"moodId": key, "percent": floors[key]} for key in order if key in floors and floors[key] > 0]


def compute_profile(tracks: list[ClassifiedTrack], taxonomy_order: list[str]) -> ProfileComputation:
    excluded = sum(track.excluded for track in tracks)
    active = [track for track in tracks if not track.excluded]
    counts_counter = Counter(track.final_mood_id for track in active)
    counts = {key: counts_counter[key] for key in taxonomy_order if counts_counter[key]}
    percentages = largest_remainder(counts, taxonomy_order)
    unknown = counts_counter["UNKNOWN"]
    known = len(active) - unknown
    flags: list[str] = []
    if len(active) < 10:
        flags.append("LOW_SAMPLE_TRIAL")
    if active and unknown / len(active) > 0.5:
        flags.append("HIGH_UNKNOWN_RATE")
    if active and sum(int(item["percent"]) for item in percentages) != 100:
        raise ValueError("PERCENT_INVARIANT_FAILED")
    if known + unknown + excluded != len(tracks):
        raise ValueError("PROFILE_COUNT_INVARIANT_FAILED")
    return ProfileComputation(
        counts=counts,
        percentages=percentages,
        known=known,
        unknown=unknown,
        excluded=excluded,
        normalized_total=len(tracks),
        quality_flags=flags,
    )
