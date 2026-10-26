from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    database_url: str = "sqlite:///./runtime/melopet.db"
    object_store_path: Path = Path("./runtime/objects")
    preview_store_path: Path = Path("./runtime/previews")
    capability_token_pepper: str = "dev-only-change-before-production"

    model_provider: str = "disabled"
    model_base_url: str = ""
    model_api_key: str = ""
    mood_model_id: str = ""
    copy_model_id: str = ""
    image_model_id: str = ""
    image_provider: str = "disabled"
    image_base_url: str = "https://dashscope.aliyuncs.com"
    image_api_key: str = ""
    model_timeout_seconds: float = Field(default=20.0, ge=1, le=120)
    model_max_retries: int = Field(default=1, ge=0, le=1)
    model_daily_budget: float = Field(default=0, ge=0)
    model_input_cost_per_1m: float = Field(default=0, ge=0)
    model_output_cost_per_1m: float = Field(default=0, ge=0)

    music_signal_provider: str = "disabled"
    upload_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    playlist_max_tracks: int = Field(default=1000, ge=1, le=10000)
    playlist_url_timeout_seconds: float = Field(default=8.0, ge=1, le=30)
    raw_file_retention_hours: int = Field(default=24, ge=1)
    taxonomy_version: str = "0.2"
    mapping_rule_version: str = "0.1"
    asset_catalog_version: str = "dev-0.1"
    prompt_mood_version: str = "MP-MOOD-CLASSIFY-001@1.1.0"
    prompt_copy_version: str = "MP-PET-COPY-001@1.0.0"
    worker_poll_seconds: float = Field(default=1.0, ge=0.1, le=60)
    worker_lease_seconds: int = Field(default=60, ge=10, le=3600)

    @field_validator("database_url")
    @classmethod
    def only_sqlite_in_phase_one(cls, value: str) -> str:
        if not value.startswith("sqlite:///"):
            raise ValueError("M1 implementation currently supports sqlite:/// URLs only")
        return value

    def resolve_path(self, path: Path) -> Path:
        return path if path.is_absolute() else (BACKEND_DIR / path).resolve()

    @property
    def database_path(self) -> Path:
        raw = self.database_url.removeprefix("sqlite:///")
        return self.resolve_path(Path(raw))

    @property
    def object_path(self) -> Path:
        return self.resolve_path(self.object_store_path)

    @property
    def preview_path(self) -> Path:
        return self.resolve_path(self.preview_store_path)

    @property
    def migrations_path(self) -> Path:
        return BACKEND_DIR / "migrations"

    @property
    def catalogs_path(self) -> Path:
        return BACKEND_DIR / "catalogs"

    @property
    def prompts_path(self) -> Path:
        return BACKEND_DIR / "prompts"

    @property
    def assets_path(self) -> Path:
        return BACKEND_DIR / "assets"

    @property
    def model_enabled(self) -> bool:
        return (
            self.model_provider == "openai_compatible"
            and bool(self.model_base_url)
            and bool(self.model_api_key)
            and bool(self.mood_model_id)
            and bool(self.copy_model_id)
            and self.model_daily_budget > 0
            and (self.model_input_cost_per_1m > 0 or self.model_output_cost_per_1m > 0)
        )

    @property
    def image_enabled(self) -> bool:
        if not self.image_model_id:
            return False
        key = self.image_api_key or self.model_api_key
        if not key:
            return False
        if self.image_provider == "openai_compatible":
            return bool(self.model_base_url)
        if self.image_provider == "dashscope":
            return True
        return False

    def readiness_issues(self) -> list[str]:
        issues: list[str] = []
        if self.model_provider != "disabled" and not self.model_enabled:
            issues.append("MODEL_CONFIGURATION_INCOMPLETE")
        if self.music_signal_provider != "disabled":
            issues.append("MUSIC_SIGNAL_PROVIDER_NOT_IMPLEMENTED_OR_APPROVED")
        if self.app_env == "production" and self.capability_token_pepper.startswith("dev-only"):
            issues.append("INSECURE_CAPABILITY_TOKEN_PEPPER")
        return issues


@lru_cache
def get_settings() -> Settings:
    return Settings()
