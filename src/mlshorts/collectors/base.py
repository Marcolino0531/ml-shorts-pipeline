"""Contrato comum entre coletores (API oficial e scraping)."""

from __future__ import annotations

from typing import Protocol

from mlshorts.models import Product


class ProductCollector(Protocol):
    """Fonte de produtos em alta do Mercado Livre."""

    name: str

    def collect_category(self, category_id: str, limit: int) -> list[Product]:
        """Retorna produtos em alta da categoria, ja normalizados."""
        ...


class CollectorError(RuntimeError):
    """Falha recuperavel de coleta: permite cair para a proxima estrategia."""
