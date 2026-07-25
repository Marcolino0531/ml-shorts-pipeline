"""Carregamento de configuracao: segredos via .env e parametros via config/settings.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.yaml"
DATA_DIR = PROJECT_ROOT / "data"


class Secrets(BaseSettings):
    """Credenciais lidas do ambiente (ou do arquivo .env na raiz do projeto)."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    ml_client_id: str | None = None
    ml_client_secret: str | None = None
    ml_site_id: str = "MLB"
    ml_affiliate_tag: str | None = None

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    script_provider: str = "openai"

    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str | None = None

    @property
    def has_ml_credentials(self) -> bool:
        return bool(self.ml_client_id and self.ml_client_secret)


class CategoryConfig(BaseModel):
    id: str
    name: str | None = None


class CollectorConfig(BaseModel):
    highlights_per_category: int = 20
    max_products_per_category: int = 5
    max_reviews_per_product: int = 8
    max_images_per_product: int = 5


class FilterConfig(BaseModel):
    min_rating: float = 4.5
    min_reviews: int = 20
    min_sold_quantity: int = 500
    min_image_width: int = 800


class ScriptGenConfig(BaseModel):
    provider: str | None = None  # openai | anthropic; None usa SCRIPT_PROVIDER do .env
    model_openai: str = "gpt-4o-mini"
    model_anthropic: str = "claude-3-5-sonnet-latest"
    temperature: float = 0.8
    # ritmo medio de narracao em pt-BR usado para estimar a duracao
    words_per_second: float = 2.6
    retry_when_too_long: bool = True


class VideoConfig(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    max_duration_seconds: int = 45


class Settings(BaseModel):
    categories: list[CategoryConfig] = Field(default_factory=list)
    collector: CollectorConfig = Field(default_factory=CollectorConfig)
    filters: FilterConfig = Field(default_factory=FilterConfig)
    scriptgen: ScriptGenConfig = Field(default_factory=ScriptGenConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)


def load_settings(path: Path | None = None) -> Settings:
    settings_path = path or DEFAULT_SETTINGS_FILE
    raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    return Settings.model_validate(raw)


@lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    return Secrets()
