"""Coleta nao repete oferta: ids com roteiro, narracao, video ou publicacao ficam de fora."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mlshorts.collectors.history import ProcessedProducts
from mlshorts.collectors.service import CollectionService
from mlshorts.config import CategoryConfig, PublishingConfig, Secrets, Settings
from mlshorts.models import PublicationStatus, QueuedPublication
from mlshorts.publish.store import build_store
from mlshorts.storage.paths import Paths

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


class FakeCollector:
    name = "fake"

    def __init__(self, products: list) -> None:
        self._products = products

    def collect_category(self, category_id: str, limit: int) -> list:
        return list(self._products)


@pytest.fixture
def paths(tmp_path) -> Paths:
    p = Paths(tmp_path)
    p.ensure()
    return p


def write_scripts(paths: Paths, *product_ids: str) -> None:
    payload = [
        {"product_id": pid, "scenes": [], "estimated_duration_seconds": 1.0} for pid in product_ids
    ]
    (paths.out / "scripts-20260101T000000Z.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def make_service(paths: Paths, products: list, history: ProcessedProducts) -> CollectionService:
    settings = Settings()
    settings.categories = [CategoryConfig(id="MLB1618", name="Cozinha")]
    settings.filters.min_reviews = 0
    return CollectionService(
        settings,
        paths=paths,
        secrets=Secrets(),
        collectors=[FakeCollector(products)],
        history=history,
    )


def test_ids_vem_de_roteiro_narracao_e_video(paths):
    write_scripts(paths, "MLB_ROTEIRO")
    (paths.out / "narration-20260101T000000Z.json").write_text(
        json.dumps([{"product_id": "MLB_NARRADO"}]), encoding="utf-8"
    )
    (paths.video / "MLB_RENDERIZADO.mp4").write_bytes(b"")
    (paths.video / "MLB_RENDERIZADO.ass").write_text("", encoding="utf-8")

    assert ProcessedProducts(paths=paths).ids() == {
        "MLB_ROTEIRO",
        "MLB_NARRADO",
        "MLB_RENDERIZADO",
    }


def test_ids_incluem_a_fila_de_publicacao_em_qualquer_status(paths, tmp_path):
    queue = tmp_path / "publications.sqlite3"
    store = build_store("sqlite", queue)
    for product_id, status in (
        ("MLB_PENDENTE", PublicationStatus.PENDING),
        ("MLB_PUBLICADO", PublicationStatus.PUBLISHED),
        ("MLB_FALHOU", PublicationStatus.FAILED),
    ):
        store.add(
            QueuedPublication(
                product_id=product_id,
                niche="Cozinha",
                media_path=f"data/video/{product_id}.mp4",
                status=status,
                scheduled_for=NOW,
            )
        )

    config = PublishingConfig(backend="sqlite", queue_path=str(queue))
    assert ProcessedProducts(paths=paths, publishing=config).ids() == {
        "MLB_PENDENTE",
        "MLB_PUBLICADO",
        "MLB_FALHOU",
    }


def test_fila_ausente_ou_ilegivel_nao_derruba_o_historico(paths, tmp_path):
    (paths.out / "scripts-quebrado.json").write_text("{ nao e json", encoding="utf-8")
    config = PublishingConfig(backend="sqlite", queue_path=str(tmp_path / "nao-existe.sqlite3"))

    assert ProcessedProducts(paths=paths, publishing=config).ids() == set()


def test_coleta_descarta_produto_ja_processado(paths, product_factory):
    write_scripts(paths, "MLB_USADO")
    novo = product_factory(id="MLB_NOVO")
    usado = product_factory(id="MLB_USADO")
    service = make_service(paths, [usado, novo], ProcessedProducts(paths=paths))

    products = service.collect(download_images=False)

    assert [product.id for product in products] == ["MLB_NOVO"]


def test_coleta_sem_produto_novo_devolve_vazio(paths, product_factory):
    write_scripts(paths, "MLB_USADO")
    service = make_service(paths, [product_factory(id="MLB_USADO")], ProcessedProducts(paths=paths))

    assert service.collect(download_images=False) == []


def test_include_processed_desliga_o_filtro(paths, product_factory):
    write_scripts(paths, "MLB_USADO")
    service = make_service(paths, [product_factory(id="MLB_USADO")], ProcessedProducts(paths=paths))
    service.settings.collector.skip_processed = False

    products = service.collect(download_images=False)

    assert [product.id for product in products] == ["MLB_USADO"]


def test_mesma_oferta_nao_repete_entre_categorias_da_mesma_rodada(paths, product_factory):
    repetido = product_factory(id="MLB_REPETIDO")
    service = make_service(paths, [repetido], ProcessedProducts(paths=paths))
    service.settings.categories = [
        CategoryConfig(id="MLB1618", name="Cozinha"),
        CategoryConfig(id="MLB1051", name="Celulares"),
    ]

    products = service.collect(download_images=False)

    assert [product.id for product in products] == ["MLB_REPETIDO"]


def test_video_renderizado_conta_como_processado(paths, product_factory):
    (paths.video / "MLB_USADO.mp4").write_bytes(b"")
    service = make_service(
        paths,
        [product_factory(id="MLB_USADO"), product_factory(id="MLB_NOVO")],
        ProcessedProducts(paths=paths),
    )

    products = service.collect(download_images=False)

    assert [product.id for product in products] == ["MLB_NOVO"]
    assert Path(paths.video / "MLB_USADO.mp4").exists()
