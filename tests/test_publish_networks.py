from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from mlshorts.config import PublishingConfig, Secrets, TikTokConfig, YouTubeConfig
from mlshorts.models import (
    Product,
    PublicationStatus,
    QueuedPublication,
    Scene,
    SceneRole,
    VideoMetadata,
    VideoScript,
)
from mlshorts.publish import (
    MetadataBuilder,
    MetadataService,
    MultiPublisher,
    PublishError,
    TikTokPublisher,
    YouTubePublisher,
    build_publisher,
)
from mlshorts.publish.metadata import TIKTOK_CAPTION_LIMIT
from mlshorts.publish.tiktok import INIT_PATH, STATUS_PATH
from mlshorts.storage.paths import Paths

NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def make_secrets(**overrides: str) -> Secrets:
    return Secrets(ml_affiliate_tag="afiliado123", **overrides)


def make_product(**overrides: object) -> Product:
    data: dict[str, object] = {
        "id": "MLB123",
        "title": "Fone Bluetooth XYZ com cancelamento de ruido",
        "permalink": "https://produto.mercadolivre.com.br/MLB-123-fone",
        "category_id": "MLB1051",
        "category_name": "Celulares",
        "price": 199.9,
        "sold_quantity": 5000,
        "rating": 4.8,
        "reviews_total": 320,
    }
    data.update(overrides)
    return Product.model_validate(data)


def make_script() -> VideoScript:
    scenes = [
        Scene.model_validate(
            {
                "bloco": role.value,
                "fala_narrador": f"fala de {role.value}",
                "instrucao_visual": "close no produto",
            }
        )
        for role in SceneRole
    ]
    return VideoScript(product_id="MLB123", scenes=scenes, estimated_duration_seconds=32.0)


def make_config(**overrides: object) -> PublishingConfig:
    data: dict[str, object] = {
        "default_hashtags": ["achadinhos", "mercadolivre"],
        "hashtags_by_niche": {"Celulares": ["celular", "#tecnologia"]},
    }
    data.update(overrides)
    return PublishingConfig.model_validate(data)


def make_item(tmp_path, metadata: VideoMetadata | None = None) -> QueuedPublication:
    media = tmp_path / "MLB123.mp4"
    media.write_bytes(b"conteudo do mp4")
    return QueuedPublication(
        product_id="MLB123",
        niche="Celulares",
        media_path=str(media),
        metadata=metadata,
        scheduled_for=NOW,
    )


# ------------------------------------------------------------------ metadados


def test_hashtags_do_nicho_vem_antes_das_globais_sem_repetir():
    config = make_config(default_hashtags=["celular", "achadinhos"], max_hashtags=3)

    assert config.hashtags_for("Celulares") == ["#celular", "#tecnologia", "#achadinhos"]
    assert config.hashtags_for("Outro") == ["#celular", "#achadinhos"]


def test_link_de_afiliado_recebe_a_tag_sem_duplicar_query():
    builder = MetadataBuilder(make_config(), make_secrets())

    link = builder.affiliate_link("https://produto.mercadolivre.com.br/MLB-123?ref=abc")

    assert "ref=abc" in link
    assert "matt_word=afiliado123" in link
    assert builder.affiliate_link(link).count("matt_word") == 1


def test_sem_tag_de_afiliado_o_link_fica_intacto(caplog):
    builder = MetadataBuilder(make_config(), Secrets())

    assert builder.affiliate_link("https://x.com/a") == "https://x.com/a"
    assert "ML_AFFILIATE_TAG ausente" in caplog.text


def test_metadados_tem_titulo_hashtags_e_link(tmp_path):
    builder = MetadataBuilder(make_config(), make_secrets())

    metadata = builder.build(make_product(), "Celulares", media_path=tmp_path / "v.mp4")

    assert metadata.title.endswith("#Shorts")
    assert (
        metadata.title.startswith("fala de gancho") is False
    )  # sem roteiro usa o titulo do produto
    assert metadata.hashtags == ["#celular", "#tecnologia", "#achadinhos", "#mercadolivre"]
    assert "matt_word=afiliado123" in metadata.affiliate_link
    assert metadata.affiliate_link in metadata.description
    assert "⭐ 4.8 com 320 avaliacoes" in metadata.description
    assert "5000+ vendidos" in metadata.description
    assert "#anuncio" in metadata.description


def test_titulo_usa_o_gancho_do_roteiro_e_respeita_o_limite():
    builder = MetadataBuilder(make_config(), make_secrets())
    script = make_script()
    script.scenes[0].narration = "palavra " * 40

    metadata = builder.build(make_product(), "Celulares", script=script)

    assert len(metadata.title) <= 100
    assert metadata.title.endswith("#Shorts")
    assert "…" in metadata.title


def test_caption_do_tiktok_corta_no_limite():
    builder = MetadataBuilder(make_config(), make_secrets())
    metadata = builder.build(make_product(), "Celulares")

    curta = builder.caption(metadata)
    cortada = builder.caption(metadata, limit=30)

    assert metadata.affiliate_link in curta
    assert "#celular" in curta
    assert len(curta) <= TIKTOK_CAPTION_LIMIT
    assert len(cortada) == 30 and cortada.endswith("…")


def test_metadata_service_le_produto_e_roteiro_dos_artefatos(tmp_path):
    paths = Paths(tmp_path / "data")
    paths.ensure()
    (paths.raw / "products-2026.json").write_text(
        json.dumps([make_product().model_dump(mode="json")]), encoding="utf-8"
    )
    (paths.out / "scripts-2026.json").write_text(
        json.dumps([make_script().model_dump(mode="json", by_alias=True)]), encoding="utf-8"
    )
    service = MetadataService(
        make_config(), paths=paths, builder=MetadataBuilder(make_config(), make_secrets())
    )

    metadata = service.build_for("MLB123", "Celulares", media_path="data/video/MLB123.mp4")

    assert metadata is not None
    assert metadata.title.startswith("fala de gancho")
    assert metadata.media_path == "data/video/MLB123.mp4"
    assert service.build_for("MLB999", "Celulares") is None


# ------------------------------------------------------------------- youtube


class FakeRequest:
    def __init__(self, response: dict[str, object] | None, chunks: int = 2) -> None:
        self.response = response
        self.remaining = chunks

    def next_chunk(self) -> tuple[object | None, dict[str, object] | None]:
        self.remaining -= 1
        if self.remaining > 0:
            return object(), None
        return None, self.response


class FakeVideos:
    def __init__(self, response: dict[str, object] | None) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def insert(self, *, part, body, media_body):
        self.calls.append({"part": part, "body": body, "media": media_body})
        return FakeRequest(self.response)


class FakeYouTube:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self._videos = FakeVideos(response if response is not None else {"id": "abc123"})

    def videos(self) -> FakeVideos:
        return self._videos


def test_youtube_marca_shorts_e_devolve_a_url(tmp_path):
    metadata = MetadataBuilder(make_config(), make_secrets()).build(make_product(), "Celulares")
    item = make_item(tmp_path, metadata)
    resource = FakeYouTube()
    publisher = YouTubePublisher(YouTubeConfig(), make_secrets(), resource=resource)

    url = publisher.publish(item)

    body = resource.videos().calls[0]["body"]
    snippet = body["snippet"]
    assert url == "https://www.youtube.com/watch?v=abc123"
    assert item.published_urls == {"youtube": url}
    assert snippet["title"].endswith("#Shorts")
    assert "#Shorts" in snippet["description"]
    assert snippet["categoryId"] == "22"
    assert snippet["tags"][0] == "celular"  # sem o '#'
    assert body["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    assert resource.videos().calls[0]["part"] == "snippet,status"


def test_youtube_nao_duplica_a_tag_de_shorts(tmp_path):
    metadata = VideoMetadata(
        product_id="MLB123",
        title="Ja tem #shorts aqui",
        description="descricao com #Shorts",
        hashtags=[],
        affiliate_link="https://x.com",
    )
    resource = FakeYouTube()

    YouTubePublisher(YouTubeConfig(), make_secrets(), resource=resource).publish(
        make_item(tmp_path, metadata)
    )

    snippet = resource.videos().calls[0]["body"]["snippet"]
    assert snippet["title"].lower().count("#shorts") == 1
    assert snippet["description"].lower().count("#shorts") == 1


def test_youtube_exige_metadados_e_arquivo(tmp_path):
    publisher = YouTubePublisher(YouTubeConfig(), make_secrets(), resource=FakeYouTube())

    with pytest.raises(PublishError, match="sem metadados"):
        publisher.publish(make_item(tmp_path))

    item = make_item(
        tmp_path,
        VideoMetadata(
            product_id="MLB123", title="t", description="d", hashtags=[], affiliate_link="l"
        ),
    )
    item.media_path = str(tmp_path / "sumiu.mp4")
    with pytest.raises(PublishError, match="nao encontrado"):
        publisher.publish(item)


def test_youtube_resposta_sem_id(tmp_path):
    metadata = VideoMetadata(
        product_id="MLB123", title="t", description="d", hashtags=[], affiliate_link="l"
    )
    publisher = YouTubePublisher(
        YouTubeConfig(), make_secrets(), resource=FakeYouTube({"erro": "quota"})
    )

    with pytest.raises(PublishError, match="sem id"):
        publisher.publish(make_item(tmp_path, metadata))


def test_youtube_sem_credenciais():
    publisher = YouTubePublisher(YouTubeConfig(), Secrets())

    with pytest.raises(PublishError, match="YOUTUBE_CLIENT_ID"):
        publisher.resource  # noqa: B018 - a propriedade e que constroi as credenciais


# -------------------------------------------------------------------- tiktok


def tiktok_publisher(**overrides: object) -> TikTokPublisher:
    config = TikTokConfig(status_poll_seconds=0.0, **overrides)
    client = httpx.Client(base_url=config.base_url)
    return TikTokPublisher(
        config,
        make_secrets(tiktok_access_token="token-tt"),
        client=client,
        caption_builder=MetadataBuilder(make_config(), make_secrets()),
        sleep=lambda _seconds: None,
    )


def metadata_for_tiktok() -> VideoMetadata:
    return MetadataBuilder(make_config(), make_secrets()).build(make_product(), "Celulares")


@respx.mock
def test_tiktok_faz_init_upload_e_espera_publicacao(tmp_path):
    item = make_item(tmp_path, metadata_for_tiktok())
    size = len(b"conteudo do mp4")
    init = respx.post(f"https://open.tiktokapis.com{INIT_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"publish_id": "pub-1", "upload_url": "https://upload.tiktok/abc"},
                "error": {"code": "ok"},
            },
        )
    )
    upload = respx.put("https://upload.tiktok/abc").mock(return_value=httpx.Response(201))
    status = respx.post(f"https://open.tiktokapis.com{STATUS_PATH}").mock(
        side_effect=[
            httpx.Response(
                200, json={"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}}
            ),
            httpx.Response(
                200, json={"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}}
            ),
        ]
    )

    url = tiktok_publisher().publish(item)

    body = json.loads(init.calls[0].request.content)
    assert url == "https://www.tiktok.com/@me/video/pub-1"
    assert item.published_urls == {"tiktok": url}
    assert body["source_info"] == {
        "source": "FILE_UPLOAD",
        "video_size": size,
        "chunk_size": size,
        "total_chunk_count": 1,
    }
    assert "matt_word=afiliado123" in body["post_info"]["title"]
    assert body["post_info"]["privacy_level"] == "SELF_ONLY"
    assert init.calls[0].request.headers["Authorization"] == "Bearer token-tt"
    assert upload.calls[0].request.headers["Content-Range"] == f"bytes 0-{size - 1}/{size}"
    assert status.call_count == 2


@respx.mock
def test_tiktok_erro_no_corpo_com_http_200(tmp_path):
    respx.post(f"https://open.tiktokapis.com{INIT_PATH}").mock(
        return_value=httpx.Response(
            200, json={"error": {"code": "spam_risk_too_many_posts", "message": "limite"}}
        )
    )

    with pytest.raises(PublishError, match="spam_risk_too_many_posts"):
        tiktok_publisher().publish(make_item(tmp_path, metadata_for_tiktok()))


@respx.mock
def test_tiktok_falha_terminal_no_status(tmp_path):
    respx.post(f"https://open.tiktokapis.com{INIT_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"publish_id": "pub-1", "upload_url": "https://upload.tiktok/abc"},
                "error": {"code": "ok"},
            },
        )
    )
    respx.put("https://upload.tiktok/abc").mock(return_value=httpx.Response(201))
    respx.post(f"https://open.tiktokapis.com{STATUS_PATH}").mock(
        return_value=httpx.Response(
            200, json={"data": {"status": "FAILED"}, "error": {"code": "ok"}}
        )
    )

    with pytest.raises(PublishError, match="recusou pub-1"):
        tiktok_publisher().publish(make_item(tmp_path, metadata_for_tiktok()))


@respx.mock
def test_tiktok_desiste_se_nunca_completa(tmp_path):
    respx.post(f"https://open.tiktokapis.com{INIT_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"publish_id": "pub-1", "upload_url": "https://upload.tiktok/abc"},
                "error": {"code": "ok"},
            },
        )
    )
    respx.put("https://upload.tiktok/abc").mock(return_value=httpx.Response(201))
    respx.post(f"https://open.tiktokapis.com{STATUS_PATH}").mock(
        return_value=httpx.Response(
            200, json={"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}}
        )
    )

    with pytest.raises(PublishError, match="apos 2 tentativas"):
        tiktok_publisher(status_poll_attempts=2).publish(make_item(tmp_path, metadata_for_tiktok()))


def test_tiktok_sem_token(tmp_path):
    publisher = TikTokPublisher(TikTokConfig(), Secrets(), client=httpx.Client())

    with pytest.raises(PublishError, match="TIKTOK_ACCESS_TOKEN"):
        publisher.publish(make_item(tmp_path, metadata_for_tiktok()))


# ------------------------------------------------------- composicao e fabrica


class FakePublisher:
    def __init__(self, name: str, error: str | None = None) -> None:
        self.name = name
        self.error = error
        self.published: list[str] = []

    def publish(self, item: QueuedPublication) -> str | None:
        if self.error:
            raise PublishError(self.error)
        self.published.append(item.product_id)
        item.published_urls[self.name] = f"https://{self.name}/{item.product_id}"
        return item.published_urls[self.name]


def test_multi_publisher_tolera_uma_rede_fora(tmp_path):
    ok = FakePublisher("youtube")
    falha = FakePublisher("tiktok", error="429 rate limit")
    item = make_item(tmp_path, metadata_for_tiktok())

    MultiPublisher([ok, falha]).publish(item)

    assert ok.published == ["MLB123"]
    assert item.published_urls == {"youtube": "https://youtube/MLB123"}
    assert item.error is not None and "tiktok: 429 rate limit" in item.error


def test_multi_publisher_falha_quando_todas_falham(tmp_path):
    publishers = [FakePublisher("youtube", error="401"), FakePublisher("tiktok", error="500")]

    with pytest.raises(PublishError, match="youtube: 401; tiktok: 500"):
        MultiPublisher(publishers).publish(make_item(tmp_path, metadata_for_tiktok()))


def test_build_publisher_por_plataforma():
    secrets = make_secrets(tiktok_access_token="t")

    assert build_publisher(make_config(platforms=["dry-run"]), secrets).name == "dry-run"
    assert build_publisher(make_config(platforms=["tiktok"]), secrets).name == "tiktok"
    assert (
        build_publisher(make_config(platforms=["youtube", "tiktok"]), secrets).name
        == "youtube+tiktok"
    )
    # --dry-run ignora as redes configuradas
    assert (
        build_publisher(make_config(platforms=["youtube"]), secrets, dry_run=True).name == "dry-run"
    )


def test_build_publisher_recusa_plataforma_desconhecida():
    with pytest.raises(ValueError, match="Instagram"):
        build_publisher(make_config(platforms=["Instagram"]), make_secrets())

    with pytest.raises(ValueError, match="vazio"):
        build_publisher(make_config(platforms=[]), make_secrets())


# ----------------------------------------------- integracao com fila/dashboard


def test_fila_publica_nas_redes_com_metadados(tmp_path):
    from mlshorts.publish import PublicationScheduler
    from mlshorts.publish.store import JsonPublicationStore

    store = JsonPublicationStore(tmp_path / "queue.json")
    youtube = FakePublisher("youtube")
    scheduler = PublicationScheduler(store, make_config(max_per_run=5), publisher=youtube)
    metadata = metadata_for_tiktok()
    media = str(make_item(tmp_path).media_path)

    item = scheduler.submit("MLB123", "Celulares", media, metadata=metadata, now=NOW)

    assert item.status is PublicationStatus.PUBLISHED
    assert store.get(item.id).published_urls == {"youtube": "https://youtube/MLB123"}


def test_fila_exige_aprovacao_antes_de_postar(tmp_path):
    from mlshorts.publish import PublicationScheduler
    from mlshorts.publish.store import JsonPublicationStore

    store = JsonPublicationStore(tmp_path / "queue.json")
    youtube = FakePublisher("youtube")
    config = make_config(require_approval=True, max_per_run=5)
    scheduler = PublicationScheduler(store, config, publisher=youtube)
    media = str(make_item(tmp_path).media_path)

    item = scheduler.submit("MLB123", "Celulares", media, metadata=metadata_for_tiktok(), now=NOW)

    assert item.status is PublicationStatus.PENDING
    assert scheduler.process_due(now=NOW) == []
    assert youtube.published == []
    assert [pending.id for pending in scheduler.awaiting_approval()] == [item.id]

    aprovado = scheduler.approve(item.id, now=NOW)

    assert aprovado.status is PublicationStatus.PUBLISHED
    assert youtube.published == ["MLB123"]
