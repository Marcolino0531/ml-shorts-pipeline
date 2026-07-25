"""Regras de selecao dos produtos coletados."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from mlshorts.config import FilterConfig
from mlshorts.models import Product

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilterResult:
    product: Product
    approved: bool
    reasons: list[str]


def evaluate(product: Product, config: FilterConfig) -> FilterResult:
    reasons: list[str] = []
    if product.rating is None or product.rating < config.min_rating:
        reasons.append(f"nota {product.rating} < {config.min_rating}")
    if product.reviews_total < config.min_reviews:
        reasons.append(f"avaliacoes {product.reviews_total} < {config.min_reviews}")
    if product.sold_quantity < config.min_sold_quantity:
        reasons.append(f"vendas {product.sold_quantity} < {config.min_sold_quantity}")
    if not any(image.width >= config.min_image_width for image in product.images):
        reasons.append(f"nenhuma imagem com largura >= {config.min_image_width}")
    return FilterResult(product=product, approved=not reasons, reasons=reasons)


def apply_filters(
    products: list[Product], config: FilterConfig, limit: int | None = None
) -> list[Product]:
    """Mantem apenas produtos aprovados, ordenados por vendas e nota."""
    approved: list[Product] = []
    for product in products:
        result = evaluate(product, config)
        if result.approved:
            approved.append(product)
        else:
            logger.info(
                "Descartado %s (%s): %s", product.id, product.title, "; ".join(result.reasons)
            )
    approved.sort(key=lambda p: (p.sold_quantity, p.rating or 0.0), reverse=True)
    return approved[:limit] if limit else approved
