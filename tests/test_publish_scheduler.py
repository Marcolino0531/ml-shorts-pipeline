from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mlshorts.config import PublishingConfig
from mlshorts.models import PublicationStatus, QueuedPublication
from mlshorts.publish.scheduler import PublicationScheduler
from mlshorts.publish.store import JsonPublicationStore, SqlitePublicationStore, build_store

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
NICHE = "Celulares"


class RecordingPublisher:
    name = "recording"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.published: list[QueuedPublication] = []

    def publish(self, item: QueuedPublication) -> None:
        if self.error:
            raise self.error
        self.published.append(item)


@pytest.fixture(params=["json", "sqlite"])
def store(request, tmp_path):
    suffix = "json" if request.param == "json" else "sqlite3"
    return build_store(request.param, tmp_path / f"publications.{suffix}")


def make_scheduler(store, publisher=None, **config: object) -> PublicationScheduler:
    return PublicationScheduler(
        store,
        config=PublishingConfig(**config),  # type: ignore[arg-type]
        publisher=publisher or RecordingPublisher(),
    )


def test_primeiro_video_do_nicho_publica_na_hora(store):
    publisher = RecordingPublisher()
    scheduler = make_scheduler(store, publisher, min_interval_hours=24)

    item = scheduler.submit("MLB1", NICHE, "video.mp4", now=NOW)

    assert item.status is PublicationStatus.PUBLISHED
    assert item.published_at == NOW
    assert [published.product_id for published in publisher.published] == ["MLB1"]


def test_segundo_video_dentro_do_intervalo_fica_pendente(store):
    publisher = RecordingPublisher()
    scheduler = make_scheduler(store, publisher, min_interval_hours=24)
    scheduler.submit("MLB1", NICHE, "a.mp4", now=NOW)

    segundo = scheduler.submit("MLB2", NICHE, "b.mp4", now=NOW + timedelta(hours=5))

    assert segundo.status is PublicationStatus.PENDING
    assert segundo.scheduled_for == NOW + timedelta(hours=24)
    assert len(publisher.published) == 1
    assert scheduler.is_blocked(NICHE, NOW + timedelta(hours=5)) is True


def test_nichos_diferentes_nao_se_bloqueiam(store):
    scheduler = make_scheduler(store, min_interval_hours=24)
    scheduler.submit("MLB1", NICHE, "a.mp4", now=NOW)

    outro = scheduler.submit("MLB2", "Informatica", "b.mp4", now=NOW)

    assert outro.status is PublicationStatus.PUBLISHED


def test_intervalo_especifico_por_nicho(store):
    scheduler = make_scheduler(
        store, min_interval_hours=24, interval_hours_by_niche={"Informatica": 12}
    )
    scheduler.submit("MLB1", "Informatica", "a.mp4", now=NOW)

    segundo = scheduler.submit("MLB2", "Informatica", "b.mp4", now=NOW + timedelta(hours=1))

    assert segundo.scheduled_for == NOW + timedelta(hours=12)


def test_process_due_nao_libera_antes_da_hora(store):
    publisher = RecordingPublisher()
    scheduler = make_scheduler(store, publisher, min_interval_hours=24)
    scheduler.submit("MLB1", NICHE, "a.mp4", now=NOW)
    scheduler.submit("MLB2", NICHE, "b.mp4", now=NOW)

    assert scheduler.process_due(now=NOW + timedelta(hours=23, minutes=59)) == []
    assert len(publisher.published) == 1
    assert store.pending()[0].product_id == "MLB2"


def test_process_due_libera_quando_o_intervalo_vence(store):
    publisher = RecordingPublisher()
    scheduler = make_scheduler(store, publisher, min_interval_hours=24)
    scheduler.submit("MLB1", NICHE, "a.mp4", now=NOW)
    scheduler.submit("MLB2", NICHE, "b.mp4", now=NOW)

    published = scheduler.process_due(now=NOW + timedelta(hours=24))

    assert [item.product_id for item in published] == ["MLB2"]
    assert published[0].status is PublicationStatus.PUBLISHED
    assert store.pending() == []
    assert store.last_published_at(NICHE) == NOW + timedelta(hours=24)


def test_process_due_respeita_max_por_rodada_e_reagenda_o_resto(store):
    publisher = RecordingPublisher()
    scheduler = make_scheduler(store, publisher, min_interval_hours=24, max_per_run=1)
    scheduler.submit("MLB1", NICHE, "a.mp4", now=NOW)
    scheduler.submit("MLB2", NICHE, "b.mp4", now=NOW)
    scheduler.submit("MLB3", NICHE, "c.mp4", now=NOW)

    # tres dias depois, os dois pendentes ja venceram, mas so um pode ir ao ar
    published = scheduler.process_due(now=NOW + timedelta(days=3))

    assert [item.product_id for item in published] == ["MLB2"]
    restante = store.pending()
    assert [item.product_id for item in restante] == ["MLB3"]

    # e o proximo so sai 24h depois desta publicacao
    assert scheduler.process_due(now=NOW + timedelta(days=3, hours=1)) == []
    reagendado = store.pending()[0]
    assert reagendado.scheduled_for == NOW + timedelta(days=4)

    liberado = scheduler.process_due(now=NOW + timedelta(days=4))
    assert [item.product_id for item in liberado] == ["MLB3"]


def test_fila_espaca_agendamentos_do_mesmo_nicho(store):
    scheduler = make_scheduler(store, min_interval_hours=24)
    scheduler.submit("MLB1", NICHE, "a.mp4", now=NOW)
    segundo = scheduler.submit("MLB2", NICHE, "b.mp4", now=NOW)
    terceiro = scheduler.submit("MLB3", NICHE, "c.mp4", now=NOW)

    assert segundo.scheduled_for == NOW + timedelta(hours=24)
    assert terceiro.scheduled_for == NOW + timedelta(hours=48)


def test_falha_na_publicacao_marca_status_failed_e_nao_bloqueia_o_nicho(store):
    scheduler = make_scheduler(
        store, RecordingPublisher(error=RuntimeError("API fora do ar")), min_interval_hours=24
    )

    item = scheduler.submit("MLB1", NICHE, "a.mp4", now=NOW)

    assert item.status is PublicationStatus.FAILED
    assert item.error == "API fora do ar"
    assert store.last_published_at(NICHE) is None
    assert scheduler.is_blocked(NICHE, NOW) is False


def test_estado_sobrevive_a_novo_scheduler_no_mesmo_arquivo(store):
    make_scheduler(store, min_interval_hours=24).submit("MLB1", NICHE, "a.mp4", now=NOW)

    outro_processo = make_scheduler(store, min_interval_hours=24)

    assert outro_processo.store.last_published_at(NICHE) == NOW
    assert outro_processo.next_slot(NICHE, NOW) == NOW + timedelta(hours=24)


def test_build_store_escolhe_backend(tmp_path):
    assert isinstance(build_store("json", tmp_path / "q.json"), JsonPublicationStore)
    assert isinstance(build_store("sqlite", tmp_path / "q.sqlite3"), SqlitePublicationStore)
    with pytest.raises(ValueError, match="desconhecido"):
        build_store("redis", tmp_path / "q")


def test_update_de_item_inexistente_falha(store):
    item = QueuedPublication(product_id="MLB9", niche=NICHE, media_path="x.mp4", scheduled_for=NOW)
    with pytest.raises(KeyError):
        store.update(item)
