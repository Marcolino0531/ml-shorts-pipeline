from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from mlshorts.config import PublishingConfig, Settings
from mlshorts.dashboard.data import CONFIG_ENV_VAR, DashboardData, load_dashboard_data
from mlshorts.models import (
    Product,
    PublicationStatus,
    Scene,
    SceneAudio,
    SceneRole,
    ScriptAudio,
    VideoScript,
)
from mlshorts.publish.scheduler import PublicationScheduler
from mlshorts.publish.store import JsonPublicationStore
from mlshorts.storage.paths import Paths

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def make_product(product_id: str = "MLB1") -> Product:
    return Product(
        id=product_id,
        title="Fone bluetooth",
        permalink="https://produto.mercadolivre.com.br/MLB1",
        price=199.9,
        rating=4.8,
        reviews_total=120,
        sold_quantity=900,
        category_id="MLB1051",
    )


def make_script(product_id: str = "MLB1") -> VideoScript:
    scenes = [
        Scene(bloco=role.value, fala_narrador=f"fala {role.value}", instrucao_visual="zoom")
        for role in SceneRole
    ]
    return VideoScript(product_id=product_id, scenes=scenes, estimated_duration_seconds=30.0)


def make_track(product_id: str = "MLB1", audio_path: str = "a.mp3") -> ScriptAudio:
    return ScriptAudio(
        product_id=product_id,
        voice_id="voice",
        model_id="eleven_multilingual_v2",
        scenes=[
            SceneAudio(
                index=0,
                role=SceneRole.GANCHO,
                text="fala gancho",
                audio_path=audio_path,
                duration_seconds=3.0,
            )
        ],
    )


@pytest.fixture
def data(tmp_path):
    paths = Paths(tmp_path / "data")
    paths.ensure()
    scheduler = PublicationScheduler(
        JsonPublicationStore(tmp_path / "queue.json"),
        config=PublishingConfig(min_interval_hours=24, require_approval=True),
    )
    return DashboardData(Settings(), paths=paths, scheduler=scheduler)


def write_json(path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_sem_artefatos_retorna_listas_vazias(data):
    assert data.product_files() == []
    assert data.load_products() == []
    assert data.load_scripts() == []
    assert data.load_narrations() == []
    assert data.queue() == []


def test_usa_o_arquivo_mais_recente_de_cada_etapa(data):
    write_json(
        data.paths.raw / "products-20260101T000000Z.json",
        [make_product("MLB_ANTIGO").model_dump(mode="json")],
    )
    write_json(
        data.paths.raw / "products-20260201T000000Z.json",
        [make_product("MLB_NOVO").model_dump(mode="json")],
    )

    assert [artifact.label for artifact in data.product_files()] == [
        "products-20260201T000000Z.json",
        "products-20260101T000000Z.json",
    ]
    assert [product.id for product in data.load_products()] == ["MLB_NOVO"]


def test_carrega_arquivo_especifico(data):
    antigo = data.paths.raw / "products-20260101T000000Z.json"
    write_json(antigo, [make_product("MLB_ANTIGO").model_dump(mode="json")])
    write_json(
        data.paths.raw / "products-20260201T000000Z.json",
        [make_product("MLB_NOVO").model_dump(mode="json")],
    )

    assert [product.id for product in data.load_products(antigo)] == ["MLB_ANTIGO"]


def test_json_invalido_nao_derruba_o_painel(data, caplog):
    (data.paths.out / "scripts-20260101T000000Z.json").write_text("{quebrado", encoding="utf-8")

    assert data.load_scripts() == []
    assert "JSON invalido" in caplog.text


def test_audio_e_video_do_produto(data):
    audio_dir = data.paths.audio / "MLB1"
    audio_dir.mkdir(parents=True)
    audio = audio_dir / "00-gancho.mp3"
    audio.write_bytes(b"audio")
    write_json(
        audio_dir / "narration.json", make_track(audio_path=str(audio)).model_dump(mode="json")
    )
    (data.paths.video / "MLB1-final.mp4").write_bytes(b"video")

    track = data.audio_for("MLB1")
    assert track is not None
    assert track.scenes[0].audio_path == str(audio)
    assert data.video_for("MLB1") is not None
    assert data.audio_for("MLB2") is None
    assert data.video_for("MLB2") is None


def test_pipeline_status_por_etapa(data):
    write_json(
        data.paths.out / "scripts-20260101T000000Z.json",
        [make_script().model_dump(mode="json", by_alias=True)],
    )
    (data.paths.images / "MLB1").mkdir(parents=True)
    (data.paths.images / "MLB1" / "0.jpg").write_bytes(b"img")
    data.scheduler.submit("MLB1", "Celulares", "MLB1.mp4", now=NOW)

    status = data.pipeline_status("MLB1")

    assert status == {
        "imagens": True,
        "roteiro": True,
        "audio": False,
        "video": False,
        "na_fila": True,
        "publicado": False,
    }
    assert data.pipeline_status("MLB999")["roteiro"] is False


def test_fila_vem_ordenada_do_mais_recente(data):
    data.scheduler.submit("MLB1", "Celulares", "a.mp4", now=NOW)
    data.scheduler.submit("MLB2", "Celulares", "b.mp4", now=NOW)

    fila = data.queue()

    assert [item.product_id for item in fila] == ["MLB2", "MLB1"]
    assert fila[0].scheduled_for > fila[1].scheduled_for


def test_botao_aprovar_publica_item_vencido(data):
    """require_approval=True: nada sai sem aprovacao, mesmo com horario livre."""
    item = data.scheduler.submit("MLB1", "Celulares", "a.mp4", now=NOW)

    assert item.status is PublicationStatus.PENDING
    assert data.scheduler.process_due(now=NOW) == []
    assert data.scheduler.awaiting_approval()[0].id == item.id

    aprovado = data.scheduler.approve(item.id, now=NOW)

    assert aprovado.status is PublicationStatus.PUBLISHED
    assert aprovado.approved_at == NOW
    assert data.scheduler.awaiting_approval() == []


def test_aprovar_item_futuro_apenas_libera_para_a_fila(data):
    data.scheduler.submit("MLB1", "Celulares", "a.mp4", now=NOW)
    primeiro = data.scheduler.approve(data.scheduler.store.pending()[0].id, now=NOW)
    assert primeiro.status is PublicationStatus.PUBLISHED

    segundo = data.scheduler.submit("MLB2", "Celulares", "b.mp4", now=NOW)
    aprovado = data.scheduler.approve(segundo.id, now=NOW)

    assert aprovado.status is PublicationStatus.PENDING
    assert aprovado.approved_at == NOW
    # ja aprovado: o cron publica sozinho quando o intervalo vencer
    publicados = data.scheduler.process_due(now=NOW + timedelta(hours=24))
    assert [item.product_id for item in publicados] == ["MLB2"]


def test_botao_cancelar_tira_da_fila(data):
    item = data.scheduler.submit("MLB1", "Celulares", "a.mp4", now=NOW)

    cancelado = data.scheduler.cancel(item.id)

    assert cancelado.status is PublicationStatus.CANCELLED
    assert data.scheduler.store.pending() == []
    with pytest.raises(ValueError, match="nao esta pendente"):
        data.scheduler.approve(item.id, now=NOW)


def test_aprovar_item_inexistente(data):
    with pytest.raises(KeyError):
        data.scheduler.approve("nao-existe")


def test_load_dashboard_data_usa_config_do_env(tmp_path, monkeypatch):
    config = tmp_path / "settings.yaml"
    config.write_text("publishing:\n  min_interval_hours: 6\n", encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config))

    assert load_dashboard_data().settings.publishing.min_interval_hours == 6
