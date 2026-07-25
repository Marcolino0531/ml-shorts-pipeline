"""Modelos de dominio compartilhados por todas as etapas do pipeline."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, HttpUrl


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


class VideoScript(BaseModel):
    product_id: str
    hook: str
    body: list[str]
    call_to_action: str
    estimated_duration_seconds: float

    @property
    def narration(self) -> str:
        return " ".join([self.hook, *self.body, self.call_to_action])


class VideoMetadata(BaseModel):
    product_id: str
    title: str
    description: str
    hashtags: list[str]
    affiliate_link: str
    media_path: str | None = None
