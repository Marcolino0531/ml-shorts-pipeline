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
    # a API do ML exige token de usuario: o refresh token vem do consentimento e rotaciona
    # a cada renovacao (o codigo regrava o novo valor no .env)
    ml_refresh_token: str | None = None
    ml_site_id: str = "MLB"
    ml_affiliate_tag: str | None = None

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    script_provider: str = "openai"

    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str | None = None

    # OAuth do YouTube: client + refresh token gerados uma vez no consentimento
    youtube_client_id: str | None = None
    youtube_client_secret: str | None = None
    youtube_refresh_token: str | None = None
    # opcional: le as estatisticas publicas do video sem depender do escopo do consentimento
    youtube_api_key: str | None = None
    tiktok_access_token: str | None = None

    @property
    def has_ml_credentials(self) -> bool:
        return bool(self.ml_client_id and self.ml_client_secret and self.ml_refresh_token)

    @property
    def has_youtube_credentials(self) -> bool:
        return bool(
            self.youtube_client_id and self.youtube_client_secret and self.youtube_refresh_token
        )


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


class TTSConfig(BaseModel):
    """Parametros da narracao na ElevenLabs."""

    # vazio usa ELEVENLABS_VOICE_ID do .env
    voice_id: str | None = None
    model_id: str = "eleven_multilingual_v2"
    # 0.0 = mais expressivo/instavel, 1.0 = monotono e previsivel
    stability: float = 0.45
    similarity_boost: float = 0.8
    style: float = 0.0
    use_speaker_boost: bool = True
    output_format: str = "mp3_44100_128"
    # silencio inserido entre cenas na montagem do video
    pause_between_scenes_seconds: float = 0.25

    @property
    def file_extension(self) -> str:
        return ".mp3" if self.output_format.startswith("mp3") else ".pcm"


class YouTubeConfig(BaseModel):
    """Upload pela YouTube Data API v3 (o formato vertical + #Shorts define o Shorts)."""

    # 22 = People & Blogs; 26 = Howto & Style
    category_id: str = "22"
    privacy_status: str = "public"
    made_for_kids: bool = False
    # marcacao obrigatoria para o video entrar no feed de Shorts
    shorts_tag: str = "#Shorts"
    upload_chunk_size: int = 5 * 1024 * 1024


class TikTokConfig(BaseModel):
    """Content Posting API: init -> upload do arquivo -> consulta do status."""

    base_url: str = "https://open.tiktokapis.com"
    privacy_level: str = "SELF_ONLY"
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False
    chunk_size: int = 10 * 1024 * 1024
    status_poll_attempts: int = 10
    status_poll_seconds: float = 3.0


class PublishingConfig(BaseModel):
    """Controle de ritmo de publicacao: nunca postar tudo de uma vez."""

    # intervalo minimo entre duas publicacoes do mesmo nicho
    min_interval_hours: float = 24.0
    # sobrescreve o intervalo para nichos especificos, ex.: {"Informatica": 12}
    interval_hours_by_niche: dict[str, float] = Field(default_factory=dict)
    # json | sqlite
    backend: str = "sqlite"
    # caminho do arquivo de fila/historico, relativo a raiz do projeto
    queue_path: str = "data/out/publications.sqlite3"
    # quantos videos no maximo publicar por execucao do `publish-queue`
    max_per_run: int = 1
    # true exige aprovacao manual (botao do dashboard) antes de qualquer publicacao
    require_approval: bool = False
    # destinos ativos: dry-run | youtube | tiktok
    platforms: list[str] = Field(default_factory=lambda: ["dry-run"])
    hashtags_by_niche: dict[str, list[str]] = Field(default_factory=dict)
    default_hashtags: list[str] = Field(default_factory=list)
    max_hashtags: int = 8
    # parametro de rastreio do programa de afiliados do Mercado Livre
    affiliate_param: str = "matt_word"
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)
    tiktok: TikTokConfig = Field(default_factory=TikTokConfig)

    def interval_for(self, niche: str) -> float:
        return self.interval_hours_by_niche.get(niche, self.min_interval_hours)

    def hashtags_for(self, niche: str) -> list[str]:
        """Hashtags do nicho + as globais, sem repetir e respeitando `max_hashtags`."""
        tags: list[str] = []
        for tag in [*self.hashtags_by_niche.get(niche, []), *self.default_hashtags]:
            normalized = tag if tag.startswith("#") else f"#{tag}"
            if normalized.lower() not in {existing.lower() for existing in tags}:
                tags.append(normalized)
        return tags[: self.max_hashtags]


class VideoConfig(BaseModel):
    """Formato de saida e estilo das legendas dinamicas."""

    width: int = 1080
    height: int = 1920
    fps: int = 30
    max_duration_seconds: int = 45
    # fundo atras da imagem do produto (as imagens do ML sao quadradas)
    background_color: str = "black"
    # leve zoom por cena para o video nao ficar estatico
    zoom_per_scene: float = 0.08
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "192k"
    # legendas: fonte, tamanho e quantas palavras aparecem por vez
    font_name: str = "DejaVu Sans"
    font_size: int = 64
    font_color: str = "&H00FFFFFF"
    outline_color: str = "&H00000000"
    outline: int = 4
    caption_words_per_chunk: int = 3
    caption_margin_bottom: int = 420


class Settings(BaseModel):
    categories: list[CategoryConfig] = Field(default_factory=list)
    collector: CollectorConfig = Field(default_factory=CollectorConfig)
    filters: FilterConfig = Field(default_factory=FilterConfig)
    scriptgen: ScriptGenConfig = Field(default_factory=ScriptGenConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
    publishing: PublishingConfig = Field(default_factory=PublishingConfig)


def load_settings(path: Path | None = None) -> Settings:
    settings_path = path or DEFAULT_SETTINGS_FILE
    raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    return Settings.model_validate(raw)


@lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    return Secrets()
