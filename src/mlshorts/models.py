"""Modelos de dominio compartilhados por todas as etapas do pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Review(BaseModel):
    id: str
    rate: int
    title: str | None = None
    content: str
    likes: int = 0
    date_created: datetime | None = None


class ProductImage(BaseModel):
    id: str
    url: HttpUrl
    width: int = 0
    height: int = 0
    local_path: str | None = None


class Product(BaseModel):
    """Produto coletado do Mercado Livre, ja normalizado."""

    id: str
    title: str
    permalink: HttpUrl
    category_id: str
    category_name: str | None = None
    price: float
    currency_id: str = "BRL"
    sold_quantity: int = 0
    rating: float | None = None
    reviews_total: int = 0
    free_shipping: bool = False
    brand: str | None = None
    highlights: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)
    images: list[ProductImage] = Field(default_factory=list)
    positive_reviews: list[Review] = Field(default_factory=list)
    source: str = "api"
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def affiliate_link(self) -> str:
        return str(self.permalink)


class SceneRole(str, Enum):
    """Blocos obrigatorios do formato Viral Hook."""

    GANCHO = "gancho"
    APRESENTACAO = "apresentacao"
    PROVA_SOCIAL = "prova_social"
    CTA = "cta"


class Scene(BaseModel):
    """Cena do roteiro, no formato devolvido pelo LLM."""

    model_config = ConfigDict(populate_by_name=True)

    role: SceneRole = Field(alias="bloco")
    narration: str = Field(alias="fala_narrador", min_length=1)
    visual: str = Field(alias="instrucao_visual", min_length=1)

    @property
    def word_count(self) -> int:
        return len(self.narration.split())


class VideoScript(BaseModel):
    product_id: str
    scenes: list[Scene]
    estimated_duration_seconds: float
    provider: str | None = None
    model: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def narration(self) -> str:
        return " ".join(scene.narration for scene in self.scenes)

    def scene_for(self, role: SceneRole) -> Scene | None:
        return next((scene for scene in self.scenes if scene.role is role), None)


class VideoMetadata(BaseModel):
    product_id: str
    title: str
    description: str
    hashtags: list[str]
    affiliate_link: str
    media_path: str | None = None


class PublicationStatus(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class QueuedPublication(BaseModel):
    """Video aguardando (ou ja liberado para) publicacao em um nicho."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    product_id: str
    niche: str
    media_path: str
    metadata: VideoMetadata | None = None
    status: PublicationStatus = PublicationStatus.PENDING
    scheduled_for: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: datetime | None = None
    error: str | None = None

    def is_due(self, now: datetime) -> bool:
        return self.status is PublicationStatus.PENDING and self.scheduled_for <= now
