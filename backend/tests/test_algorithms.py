from __future__ import annotations

import pytest

from melopet_backend.model_gateway import ModelCallResult, ModelGateway
from melopet_backend.mood import classify_tracks, compute_profile, largest_remainder
from melopet_backend.pet import generate_copy, map_pet_spec
from melopet_backend.schemas import MoodClassificationOutput, PetCopyOutput

AUDIO_FEATURE_VERSION = "melopet-audio-features@1.0.0"


def test_largest_remainder_is_exact_and_stable(service):
    result = largest_remainder(
        {"heroic_drive": 1, "joy_bright": 1, "UNKNOWN": 1},
        service.catalogs.taxonomy_order,
    )
    assert sum(item["percent"] for item in result) == 100
    assert result == [
        {"moodId": "heroic_drive", "percent": 34},
        {"moodId": "joy_bright", "percent": 33},
        {"moodId": "UNKNOWN", "percent": 33},
    ]


def test_title_and_artist_are_never_classification_signals(service):
    tracks = [
        {
            "trackRef": "track_1",
            "title": "happy energetic rock",
            "artist": "Famous Artist",
            "allowedSignals": {},
            "excluded": False,
        }
    ]
    batch = classify_tracks(tracks, service.catalogs, ModelGateway())
    assert batch.tracks[0].final_mood_id == "UNKNOWN"
    assert batch.tracks[0].reason_code == "MISSING_SIGNAL"


@pytest.mark.parametrize(
    ("signals", "expected_mood"),
    [
        (
            {
                "audioEnergy": 0.86,
                "audioBrightness": 0.70,
                "audioPulse": 0.58,
                "tempoConfidence": 0.65,
                "bpm": 142,
                "audioFeatureVersion": AUDIO_FEATURE_VERSION,
            },
            "heroic_drive",
        ),
        (
            {
                "audioEnergy": 0.62,
                "audioBrightness": 0.70,
                "audioPulse": 0.45,
                "tempoConfidence": 0.72,
                "bpm": 122,
                "audioFeatureVersion": AUDIO_FEATURE_VERSION,
            },
            "joy_bright",
        ),
        (
            {
                "audioEnergy": 0.22,
                "audioBrightness": 0.25,
                "audioPulse": 0.10,
                "tempoConfidence": 0.15,
                "bpm": 72,
                "audioFeatureVersion": AUDIO_FEATURE_VERSION,
            },
            "calm_focus",
        ),
    ],
)
def test_fast_audio_features_supply_conservative_mood_candidates(service, signals, expected_mood):
    batch = classify_tracks(
        [{"trackRef": "audio", "allowedSignals": signals, "excluded": False}],
        service.catalogs,
        ModelGateway(),
    )
    assert batch.tracks[0].final_mood_id == expected_mood
    assert any(item.startswith("audio") for item in batch.tracks[0].evidence)


def test_ambiguous_or_unknown_version_audio_features_remain_unknown(service):
    base = {
        "audioEnergy": 0.55,
        "audioBrightness": 0.30,
        "audioPulse": 0.20,
        "tempoConfidence": 0.40,
        "bpm": 110,
    }
    tracks = [
        {
            "trackRef": "ambiguous",
            "allowedSignals": {**base, "audioFeatureVersion": AUDIO_FEATURE_VERSION},
            "excluded": False,
        },
        {
            "trackRef": "unknown-version",
            "allowedSignals": {
                **base,
                "audioEnergy": 0.9,
                "audioPulse": 0.8,
                "audioFeatureVersion": "untrusted@9",
            },
            "excluded": False,
        },
    ]
    batch = classify_tracks(tracks, service.catalogs, ModelGateway())
    assert [track.final_mood_id for track in batch.tracks] == ["UNKNOWN", "UNKNOWN"]


def test_explicit_mood_semantics_override_conflicting_audio_features(service):
    batch = classify_tracks(
        [
            {
                "trackRef": "semantic-first",
                "allowedSignals": {
                    "mood": "平静",
                    "bpm": 142,
                    "audioEnergy": 0.9,
                    "audioBrightness": 0.8,
                    "audioPulse": 0.7,
                    "tempoConfidence": 0.8,
                    "audioFeatureVersion": AUDIO_FEATURE_VERSION,
                },
                "excluded": False,
            }
        ],
        service.catalogs,
        ModelGateway(),
    )
    assert batch.tracks[0].final_mood_id == "calm_focus"
    assert "mood:semantic_match" in batch.tracks[0].evidence


def test_profile_invariants_include_unknown_and_excluded(service):
    tracks = classify_tracks(
        [
            {"trackRef": "a", "allowedSignals": {"genre": "rock"}, "excluded": False},
            {"trackRef": "b", "allowedSignals": {}, "excluded": False},
            {"trackRef": "c", "allowedSignals": {}, "excluded": True},
        ],
        service.catalogs,
        ModelGateway(),
    ).tracks
    profile = compute_profile(tracks, service.catalogs.taxonomy_order)
    assert profile.known == 1
    assert profile.unknown == 1
    assert profile.excluded == 1
    assert profile.normalized_total == 3
    assert sum(item["percent"] for item in profile.percentages) == 100


class UnsafeMoodGateway(ModelGateway):
    def classify_moods(self, payload, *, idempotency_key=None):
        del payload, idempotency_key
        output = MoodClassificationOutput.model_validate(
            {
                "status": "ok",
                "task_type": "song_mood_classification",
                "items": [
                    {
                        "trackRef": "a",
                        "proposedPrimaryMoodId": "not-in-taxonomy",
                        "score": 0.99,
                        "evidenceSummary": ["untrusted"],
                        "reasonCode": "MATCHED",
                    },
                    {
                        "trackRef": "b",
                        "proposedPrimaryMoodId": "joy_bright",
                        "score": 0.2,
                        "evidenceSummary": ["weak"],
                        "reasonCode": "MATCHED",
                    },
                    {
                        "trackRef": "not-requested",
                        "proposedPrimaryMoodId": "joy_bright",
                        "score": 0.99,
                        "evidenceSummary": ["fabricated"],
                        "reasonCode": "MATCHED",
                    },
                ],
                "warnings": [],
                "promptVersion": "test",
            }
        )
        return ModelCallResult(output, "fake", "fake", "test", "hash", 0, 0, 0, 1, "ok")


def test_model_cannot_escape_track_taxonomy_or_confidence_guards(service):
    batch = classify_tracks(
        [
            {"trackRef": "a", "allowedSignals": {"genre": "unmapped-a"}, "excluded": False},
            {"trackRef": "b", "allowedSignals": {"genre": "unmapped-b"}, "excluded": False},
        ],
        service.catalogs,
        UnsafeMoodGateway(),
    )
    assert [track.final_mood_id for track in batch.tracks] == ["UNKNOWN", "UNKNOWN"]


class UnsafeCopyGateway(ModelGateway):
    def generate_copy(self, payload, *, idempotency_key=None):
        del payload, idempotency_key
        output = PetCopyOutput.model_validate(
            {
                "status": "ok",
                "task_type": "pet_copy_generation",
                "petName": "诊断者",
                "intro": "你患有抑郁，这就是你的真实性格。",
                "clickLines": ["你就是这样。", "确定无疑。", "接受判断。"],
                "warnings": [],
                "promptVersion": "unsafe-test",
            }
        )
        return ModelCallResult(output, "fake", "fake", "test", "hash", 0, 0, 0, 1, "ok")


def test_unsafe_model_copy_is_discarded_for_fixed_template(service):
    profile = {
        "percentages": [{"moodId": "joy_bright", "percent": 100}],
        "unknownCount": 0,
        "normalizedTotal": 12,
        "excludedCount": 0,
    }
    spec = {"candidateId": "pet_test", "traitBindings": [], "seed": "00" * 32}
    copy, result = generate_copy(spec, profile, UnsafeCopyGateway(), service.settings)
    assert result and result.status == "ok"
    assert copy["promptVersion"] == "fixed-template@1.0.0"
    assert "TEMPLATE_FALLBACK" in copy["warnings"]
    assert "抑郁" not in copy["intro"]


def test_disabled_asset_fails_closed(service):
    profile = {
        "revision": "profile_test",
        "percentages": [{"moodId": "heroic_drive", "percent": 100}],
    }
    spec = map_pet_spec(
        profile=profile,
        playlist_hash="abc",
        candidate_index=0,
        catalogs=service.catalogs,
    )
    asset = service.catalogs.assets[spec["speciesId"]]
    original = asset["enabled"]
    asset["enabled"] = False
    try:
        with pytest.raises(ValueError, match="ASSET_INVALID"):
            map_pet_spec(
                profile=profile,
                playlist_hash="abc",
                candidate_index=0,
                catalogs=service.catalogs,
            )
    finally:
        asset["enabled"] = original
