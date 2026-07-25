from __future__ import annotations

import json

from mlshorts.collectors.service import CollectionService
from mlshorts.config import CategoryConfig, Secrets, Settings
from mlshorts.storage.paths import Paths


class FakeCollector:
    def __init__(self, name: str, products=None, error: Exception | None = None) -> None:
        self.name = name
        self._products = products or []
        self._error = error
        self.calls = 0

    def collect_category(self, category_id: str, limit: int):
        self.calls += 1
        if self._error:
            raise self._error
        return self._products


def _settings() -> Settings:
    settings = Settings()
    settings.categories = [CategoryConfig(id="MLB1051", name="Celulares")]
    return settings


def test_usa_fallback_quando_primeiro_coletor_falha(tmp_path, product_factory):
    failing = FakeCollector("api", error=RuntimeError("401"))
    working = FakeCollector("scraper", products=[product_factory()])
    service = CollectionService(
        _settings(),
        paths=Paths(tmp_path),
        secrets=Secrets(),
        collectors=[failing, working],
    )

    products = service.collect(download_images=False)

    assert failing.calls == 1 and working.calls == 1
    assert [product.id for product in products] == ["MLB123"]
    assert products[0].category_name == "Celulares"


def test_persiste_json_com_produtos_aprovados(tmp_path, product_factory):
    service = CollectionService(
        _settings(),
        paths=Paths(tmp_path),
        secrets=Secrets(),
        collectors=[
            FakeCollector("api", products=[product_factory(), product_factory(id="x", rating=2.0)])
        ],
    )

    service.collect(download_images=False)

    files = list((tmp_path / "raw").glob("products-*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert [item["id"] for item in payload] == ["MLB123"]
