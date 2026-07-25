"""Orquestra a etapa 1 do pipeline: coleta -> filtro -> imagens -> persistencia."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from mlshorts.collectors.base import ProductCollector
from mlshorts.collectors.filters import apply_filters
from mlshorts.collectors.images import download_product_images
from mlshorts.collectors.mercadolivre_api import MercadoLivreAPICollector
from mlshorts.collectors.mercadolivre_scraper import MercadoLivreScraperCollector
from mlshorts.config import Secrets, Settings, get_secrets
from mlshorts.models import Product
from mlshorts.storage.paths import Paths

logger = logging.getLogger(__name__)


class CollectionService:
    def __init__(
        self,
        settings: Settings,
        paths: Paths | None = None,
        secrets: Secrets | None = None,
        collectors: list[ProductCollector] | None = None,
    ) -> None:
        self.settings = settings
        self.paths = paths or Paths()
        self.secrets = secrets or get_secrets()
        self.collectors = collectors or self._default_collectors()

    def _default_collectors(self) -> list[ProductCollector]:
        """API oficial primeiro; scraping como fallback."""
        collector_config = self.settings.collector
        chain: list[ProductCollector] = []
        if self.secrets.has_ml_credentials:
            chain.append(
                MercadoLivreAPICollector(
                    secrets=self.secrets,
                    max_reviews=collector_config.max_reviews_per_product,
                    max_images=collector_config.max_images_per_product,
                )
            )
        else:
            logger.warning("Sem credenciais do Mercado Livre: usando apenas o coletor de scraping.")
        chain.append(
            MercadoLivreScraperCollector(
                max_reviews=collector_config.max_reviews_per_product,
                max_images=collector_config.max_images_per_product,
            )
        )
        return chain

    def collect(self, download_images: bool = True) -> list[Product]:
        self.paths.ensure()
        selected: list[Product] = []
        for category in self.settings.categories:
            raw_products = self._collect_with_fallback(category.id)
            for product in raw_products:
                product.category_name = category.name
            approved = apply_filters(
                raw_products,
                self.settings.filters,
                limit=self.settings.collector.max_products_per_category,
            )
            logger.info(
                "Categoria %s: %d coletados, %d aprovados",
                category.id,
                len(raw_products),
                len(approved),
            )
            selected.extend(approved)

        if download_images:
            for product in selected:
                download_product_images(product, self.paths.images)

        self._persist(selected)
        return selected

    def _collect_with_fallback(self, category_id: str) -> list[Product]:
        errors: list[str] = []
        for collector in self.collectors:
            try:
                products = collector.collect_category(
                    category_id, self.settings.collector.highlights_per_category
                )
            except Exception as exc:  # noqa: BLE001 - fallback deliberado para o proximo coletor
                logger.warning("Coletor %s falhou em %s: %s", collector.name, category_id, exc)
                errors.append(f"{collector.name}: {exc}")
                continue
            if products:
                return products
            logger.info("Coletor %s nao retornou produtos para %s", collector.name, category_id)
        if errors:
            logger.error("Todos os coletores falharam para %s: %s", category_id, " | ".join(errors))
        return []

    def _persist(self, products: list[Product]) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.paths.raw / f"products-{stamp}.json"
        payload = [product.model_dump(mode="json") for product in products]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Produtos salvos em %s", path)
        return path
