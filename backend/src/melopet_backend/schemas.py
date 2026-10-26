from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

ShortEvidence = Annotated[str, StringConstraints(max_length=160)]
ShortLine = Annotated[str, StringConstraints(max_length=24)]
ShortWarning = Annotated[str, StringConstraints(max_length=160)]


class ColumnMapping(BaseModel):
    title: str
    artist: str
    album: str | None = None
    duration_ms: str | None = Field(default=None, alias="durationMs")
    platform_track_id: str | None = Field(default=None, alias="platformTrackId")
    genre: str | None = None
    mood: str | None = None
    bpm: str | None = None
    tags: str | None = None
    audio_energy: str | None = Field(default=None, alias="audioEnergy")
    audio_brightness: str | None = Field(default=None, alias="audioBrightness")
    audio_dynamics: str | None = Field(default=None, alias="audioDynamics")
    audio_pulse: str | None = Field(default=None, alias="audioPulse")
    tempo_confidence: str | None = Field(default=None, alias="tempoConfidence")
    audio_feature_version: str | None = Field(default=None, alias="audioFeatureVersion")

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @model_validator(mode="after")
    def required_columns_are_distinct(self) -> ColumnMapping:
        if self.title == self.artist:
            raise ValueError("title and artist must use different columns")
        return self


class MappingRequest(BaseModel):
    mapping: ColumnMapping

    model_config = {"extra": "forbid"}


class CorrectionItem(BaseModel):
    track_ref: str = Field(alias="trackRef")
    action: Literal["set_mood", "exclude", "include", "restore_auto"]
    mood_id: str | None = Field(default=None, alias="moodId")

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @model_validator(mode="after")
    def mood_required_for_set(self) -> CorrectionItem:
        if self.action == "set_mood" and not self.mood_id:
            raise ValueError("moodId is required for set_mood")
        return self


class CorrectionsRequest(BaseModel):
    expected_revision: str = Field(alias="expectedRevision")
    corrections: list[CorrectionItem] = Field(min_length=1, max_length=1000)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class PetSelectionRequest(BaseModel):
    candidate_record_id: str = Field(alias="candidateRecordId", min_length=1, max_length=200)
    expected_selection_version: int | None = Field(
        default=None,
        alias="expectedSelectionVersion",
        ge=0,
    )

    model_config = {"populate_by_name": True, "extra": "forbid"}


class PetCustomization(BaseModel):
    species_id: str | None = Field(default=None, alias="speciesId", min_length=1, max_length=120)
    palette_id: str | None = Field(default=None, alias="paletteId", min_length=1, max_length=120)
    motion_id: str | None = Field(default=None, alias="motionId", min_length=1, max_length=120)
    headphone: bool | None = None

    model_config = {"populate_by_name": True, "extra": "forbid"}


class PetCustomizationRequest(BaseModel):
    customization: PetCustomization | None = None

    model_config = {"extra": "forbid"}


class DesktopPetStateRequest(BaseModel):
    action: Literal["idle", "listen", "dance", "sleep"]
    say: bool = True

    model_config = {"extra": "forbid"}


class ModelMoodItem(BaseModel):
    track_ref: str = Field(alias="trackRef")
    proposed_primary_mood_id: str = Field(alias="proposedPrimaryMoodId")
    score: float = Field(ge=0, le=1)
    evidence_summary: list[ShortEvidence] = Field(alias="evidenceSummary", max_length=8)
    reason_code: Literal["MATCHED", "MISSING_SIGNAL", "CONFLICTING_SIGNAL", "OUT_OF_TAXONOMY"] = Field(alias="reasonCode")

    model_config = {"populate_by_name": True, "extra": "forbid"}


class MoodClassificationOutput(BaseModel):
    status: Literal["ok", "invalid_input", "insufficient_context", "refused", "retryable_error"]
    task_type: Literal["song_mood_classification"] = Field(alias="task_type")
    items: list[ModelMoodItem] = Field(default_factory=list)
    warnings: list[ShortWarning] = Field(default_factory=list, max_length=20)
    prompt_version: str = Field(alias="promptVersion", max_length=120)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class PetCopyOutput(BaseModel):
    status: Literal["ok", "invalid_input", "refused", "retryable_error"]
    task_type: Literal["pet_copy_generation"] = Field(alias="task_type")
    pet_name: str = Field(alias="petName", max_length=8)
    intro: str = Field(max_length=60)
    click_lines: list[ShortLine] = Field(alias="clickLines", min_length=3, max_length=5)
    warnings: list[ShortWarning] = Field(default_factory=list, max_length=20)
    prompt_version: str = Field(alias="promptVersion", max_length=120)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class Envelope(BaseModel):
    request_id: str = Field(alias="requestId")
    status: Literal["ok", "error"]
    error_code: str | None = Field(default=None, alias="errorCode")
    safe_message: str | None = Field(default=None, alias="safeMessage")
    data: dict[str, Any] | None = None

    model_config = {"populate_by_name": True, "extra": "forbid"}
