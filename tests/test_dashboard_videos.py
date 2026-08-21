"""Visao por video do painel: produto, status editorial, legenda, link e metricas do YouTube."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from mlshorts.config import PublishingConfig, Secrets, Settings
from mlshorts.dashboard.data import PLACEHOLDER, DashboardData
from mlshorts.dashboard.youtube_stats import (
    VIDEOS_URL,
    VideoStatistics,
    YouTubeStatsClient,
    video_id_from_url,
)
from mlshorts.models import Product, PublicationStatus, VideoMetadata
from mlshorts.publish.metadata import MetadataBuilder
from mlshorts.publish.scheduler import PublicationScheduler
from mlshorts.publish.store import JsonPublicationStore
from mlshorts.storage.paths import Paths

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
VIDEO_ID = "dQw4w9WgXcQ"


class FakeStatsClient:
    """Duplo do cliente da Data API; `error` simula a chamada falhando."""

    def __init__(
        self, stats: dict[str, VideoStatistics] | None = None, error: Exception | None = None
    ) -> None:
        self.stats = stats or {}
        self.error = error
        self.calls: list[list[str]] = []

    def fetch(self, video_ids) -> dict[str, VideoStatistics]:
        self.calls.append(list(video_ids))
        if self.error is not None:
            raise self.error
        return self.stats


def make_product(product_id: str = "MLB1") -> Product:
    return Product(
        id=product_id,
        title="Fone bluetooth",
        permalink="https://produto.mercadolivre.com.br/MLB1",
        price=199.9,
        category_id="MLB1051",
    )


def make_metadata(product_id: str = "MLB1") -> VideoMetadata:
    return VideoMetadata(
        product_id=product_id,
        title="Esse fone e absurdo #Shorts",
        description="Fone bluetooth\n🛒 Compre aqui: https://ml.com/MLB1?matt_word=tag",
        hashtags=["#achados"],
        affiliate_link="https://ml.com/MLB1?matt_word=tag",
    )


def build_data(tmp_path, stats_client=None) -> DashboardData:
    paths = Paths(tmp_path / "data")
    paths.ensure()
    scheduler = PublicationScheduler(
        JsonPublicationStore(tmp_path / "queue.json"),
        config=PublishingConfig(require_approval=True),
    )
    return DashboardData(
        Settings(),
        paths=paths,
        scheduler=scheduler,
        stats_client=stats_client,
        metadata_builder=MetadataBuilder(
            PublishingConfig(), secrets=Secrets(ml_affiliate_tag="minhatag")
        ),
    )


def write_products(data: DashboardData, *products: Product) -> None:
    (data.paths.raw / "products-20260725T120000Z.json").write_text(
        json.dumps([product.model_dump(mode="json") for product in products], ensure_ascii=False),
        encoding="utf-8",
    )


def add_image(data: DashboardData, product_id: str = "MLB1") -> None:
    directory = data.paths.images / product_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "0.jpg").write_bytes(b"img")
    (directory / "1.jpg").write_bytes(b"img")


def test_video_renderizado_fora_da_fila_e_rascunho(tmp_path):
    data = build_data(tmp_path)
    write_products(data, make_product())
    add_image(data)
    (data.paths.video / "MLB1-final.mp4").write_bytes(b"video")

    (row,) = data.video_rows()

    assert row.status == "rascunho"
    assert (row.product_title, row.product_id) == ("Fone bluetooth", "MLB1")
    assert row.image == data.paths.images / "MLB1" / "0.jpg"
    assert row.video == data.paths.video / "MLB1-final.mp4"
    assert row.caption is None
    # sem publicacao ainda, mostra o link que o publish vai usar
    assert "matt_word=minhatag" in (row.affiliate_link or "")
    assert row.published_at_display == PLACEHOLDER
    assert (row.views_display, row.likes_display, row.comments_display) == (
        PLACEHOLDER,
        PLACEHOLDER,
        PLACEHOLDER,
    )


def test_item_pendente_aparece_como_na_fila_com_legenda_e_link(tmp_path):
    data = build_data(tmp_path)
    write_products(data, make_product())
    data.scheduler.submit(
        "MLB1", "Celulares", "data/video/MLB1-final.mp4", metadata=make_metadata(), now=NOW
    )

    (row,) = data.video_rows()

    assert row.status == "na fila de publicacao"
    assert row.caption == make_metadata().description
    assert row.affiliate_link == "https://ml.com/MLB1?matt_word=tag"
    assert row.published_at is None
    assert row.youtube_video_id is None
    assert row.as_table_row()["Views (YouTube)"] == PLACEHOLDER


def test_publicado_no_youtube_traz_views_curtidas_e_comentarios(tmp_path):
    stats = FakeStatsClient({VIDEO_ID: VideoStatistics(views=1234, likes=56, comments=7)})
    data = build_data(tmp_path, stats_client=stats)
    write_products(data, make_product())
    item = data.scheduler.submit(
        "MLB1", "Celulares", "data/video/MLB1-final.mp4", metadata=make_metadata(), now=NOW
    )
    item.status = PublicationStatus.PUBLISHED
    item.published_at = NOW
    item.published_urls = {"youtube": f"https://www.youtube.com/watch?v={VIDEO_ID}"}
    data.scheduler.store.update(item)

    (row,) = data.video_rows()

    assert row.status == "publicado"
    assert row.youtube_video_id == VIDEO_ID
    assert stats.calls == [[VIDEO_ID]]
    assert (row.views_display, row.likes_display, row.comments_display) == ("1.234", "56", "7")
    assert row.published_at_display == "2026-07-25T12:00+00:00"


def test_falha_nas_estatisticas_mantem_a_linha_com_placeholder(tmp_path, caplog):
    stats = FakeStatsClient(error=httpx.HTTPError("403 sem permissao"))
    data = build_data(tmp_path, stats_client=stats)
    write_products(data, make_product())
    item = data.scheduler.submit("MLB1", "Celulares", "a.mp4", metadata=make_metadata(), now=NOW)
    item.status = PublicationStatus.PUBLISHED
    item.published_at = NOW
    item.published_urls = {"youtube": f"https://www.youtube.com/shorts/{VIDEO_ID}"}
    data.scheduler.store.update(item)

    (row,) = data.video_rows()

    assert row.status == "publicado"
    assert row.caption == make_metadata().description
    assert row.published_at_display == "2026-07-25T12:00+00:00"
    assert row.statistics is None
    assert row.views_display == PLACEHOLDER
    assert "estatisticas do YouTube" in caplog.text


def test_uma_linha_por_produto_sem_duplicar_o_que_esta_na_fila(tmp_path):
    data = build_data(tmp_path)
    write_products(data, make_product(), make_product("MLB2"))
    (data.paths.video / "MLB1-final.mp4").write_bytes(b"video")
    (data.paths.video / "MLB2-final.mp4").write_bytes(b"video")
    data.scheduler.submit("MLB1", "Celulares", "data/video/MLB1-final.mp4", now=NOW)

    rows = data.video_rows()

    assert [(row.product_id, row.status) for row in rows] == [
        ("MLB1", "na fila de publicacao"),
        ("MLB2", "rascunho"),
    ]


def test_produto_ausente_do_ultimo_products_json_nao_derruba_a_linha(tmp_path):
    data = build_data(tmp_path)
    data.scheduler.submit("MLB404", "Celulares", "a.mp4", now=NOW)

    (row,) = data.video_rows()

    assert row.product_title == "MLB404"
    assert row.affiliate_link is None
    assert row.as_table_row()["Link de afiliado"] == PLACEHOLDER


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://www.youtube.com/watch?v={VIDEO_ID}", VIDEO_ID),
        (f"https://www.youtube.com/shorts/{VIDEO_ID}", VIDEO_ID),
        (f"https://youtu.be/{VIDEO_ID}", VIDEO_ID),
        ("https://www.tiktok.com/@conta/video/123", None),
    ],
)
def test_extracao_do_id_do_video(url, expected):
    assert video_id_from_url(url) == expected


@respx.mock
def test_cliente_de_estatisticas_usa_api_key_e_converte_os_contadores():
    route = respx.get(VIDEOS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": VIDEO_ID,
                        "statistics": {
                            "viewCount": "1024",
                            "likeCount": "64",
                            "commentCount": "8",
                        },
                    }
                ]
            },
        )
    )
    client = YouTubeStatsClient(secrets=Secrets(youtube_api_key="chave"))

    stats = client.fetch([VIDEO_ID, VIDEO_ID, ""])

    assert stats == {VIDEO_ID: VideoStatistics(views=1024, likes=64, comments=8)}
    request = route.calls[0].request
    assert request.url.params["part"] == "statistics"
    # ids repetidos entram uma unica vez na chamada
    assert request.url.params["id"] == VIDEO_ID
    assert request.url.params["key"] == "chave"


@respx.mock
def test_cliente_de_estatisticas_usa_o_oauth_de_publicacao_sem_api_key():
    route = respx.get(VIDEOS_URL).mock(
        return_value=httpx.Response(200, json={"items": [{"id": VIDEO_ID, "statistics": {}}]})
    )
    client = YouTubeStatsClient(secrets=Secrets(), access_token=lambda: "token-oauth")

    stats = client.fetch([VIDEO_ID])

    # video com contadores ocultos: nada de metrica inventada
    assert stats == {VIDEO_ID: VideoStatistics()}
    assert route.calls[0].request.headers["Authorization"] == "Bearer token-oauth"
    assert "key" not in route.calls[0].request.url.params


@respx.mock
def test_cliente_de_estatisticas_devolve_vazio_quando_a_api_falha(caplog):
    respx.get(VIDEOS_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
    client = YouTubeStatsClient(secrets=Secrets(youtube_api_key="chave"))

    assert client.fetch([VIDEO_ID]) == {}
    assert "'--'" in caplog.text


def test_cliente_de_estatisticas_sem_ids_nao_chama_a_api():
    client = YouTubeStatsClient(secrets=Secrets(youtube_api_key="chave"))

    assert client.fetch([]) == {}
