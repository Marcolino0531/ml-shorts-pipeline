"""Cada etapa processa so os produtos da execucao atual, sem reprocessar rodadas antigas."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mlshorts.cli import app
from mlshorts.config import Settings
from mlshorts.models import (
    PublicationStatus,
    QueuedPublication,
    Scene,
    SceneAudio,
    SceneRole,
    ScriptAudio,
    VideoScript,
)
from mlshorts.publish.store import build_store
from mlshorts.storage.paths import Paths
from mlshorts.storage.scope import current_product_ids
from mlshorts.tts import NarrationService
from mlshorts.video import RenderService

runner = CliRunner()


class FakeRenderer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def render(self, track: ScriptAudio, images: list[Path | None], output: Path) -> Path:
        self.calls.append(track.product_id)
        output.write_bytes(b"mp4")
        return output


class FakeTTSProvider:
    name = "fake"
    voice_id = "voice-abc"
    model_id = "eleven_multilingual_v2"

    def synthesize(self, text: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"audio")
        return target


def fake_probe(path: Path) -> float:
    return 3.0


def make_script(product_id: str) -> VideoScript:
    scenes = [
        Scene(role=role, narration=f"fala do {product_id}", visual="cena") for role in SceneRole
    ]
    return VideoScript(product_id=product_id, scenes=scenes, estimated_duration_seconds=12.0)


def make_track(tmp_path: Path, product_id: str) -> ScriptAudio:
    audio = tmp_path / f"{product_id}.mp3"
    audio.write_bytes(b"fake")
    scenes = [
        SceneAudio(
            index=index,
            role=role,
            text="uma duas tres quatro",
            audio_path=str(audio),
            duration_seconds=3.0,
            start_seconds=index * 3.25,
        )
        for index, role in enumerate(SceneRole)
    ]
    return ScriptAudio(product_id=product_id, voice_id="v", model_id="m", scenes=scenes)


def write_products(paths: Paths, stamp: str, *product_ids: str) -> Path:
    payload = [
        {
            "id": product_id,
            "title": f"Produto {product_id}",
            "permalink": f"https://produto.mercadolivre.com.br/{product_id}",
            "category_id": "MLB1618",
            "price": 99.9,
        }
        for product_id in product_ids
    ]
    path = paths.raw / f"products-{stamp}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_manifest(paths: Paths, tmp_path: Path, product_id: str) -> None:
    target = paths.audio / product_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "narration.json").write_text(
        json.dumps(make_track(tmp_path, product_id).model_dump(mode="json")), encoding="utf-8"
    )


@pytest.fixture
def paths(tmp_path) -> Paths:
    p = Paths(tmp_path / "data")
    p.ensure()
    return p


def test_escopo_vem_do_products_json_mais_recente(paths):
    write_products(paths, "20260101T000000Z", "MLB_ANTIGO")
    write_products(paths, "20260102T000000Z", "MLB_ATUAL")

    assert current_product_ids(paths) == {"MLB_ATUAL"}


def test_escopo_aceita_arquivo_explicito(paths):
    antigo = write_products(paths, "20260101T000000Z", "MLB_ANTIGO")
    write_products(paths, "20260102T000000Z", "MLB_ATUAL")

    assert current_product_ids(paths, antigo) == {"MLB_ANTIGO"}


def test_escopo_sem_coleta_falha_em_vez_de_processar_tudo(paths):
    with pytest.raises(FileNotFoundError, match="mlshorts collect"):
        current_product_ids(paths)


def test_render_ignora_narracoes_de_execucoes_anteriores(paths, tmp_path):
    write_manifest(paths, tmp_path, "MLB_ANTIGO")
    write_manifest(paths, tmp_path, "MLB_ATUAL")
    write_products(paths, "20260102T000000Z", "MLB_ATUAL")
    renderer = FakeRenderer()

    service = RenderService(Settings(), paths=paths, renderer=renderer)
    videos = service.run(product_ids=current_product_ids(paths))

    assert renderer.calls == ["MLB_ATUAL"]
    assert [path.name for path in videos] == ["MLB_ATUAL.mp4"]
    assert not (paths.video / "MLB_ANTIGO.mp4").exists()


def test_render_sem_escopo_reprocessa_tudo(paths, tmp_path):
    """Comportamento antigo, agora so no `--all`."""
    write_manifest(paths, tmp_path, "MLB_ANTIGO")
    write_manifest(paths, tmp_path, "MLB_ATUAL")
    renderer = FakeRenderer()

    RenderService(Settings(), paths=paths, renderer=renderer).run()

    assert renderer.calls == ["MLB_ANTIGO", "MLB_ATUAL"]


def test_render_avisa_quando_o_escopo_nao_tem_narracao(paths, tmp_path):
    write_manifest(paths, tmp_path, "MLB_ANTIGO")

    with pytest.raises(FileNotFoundError, match="MLB_ATUAL"):
        RenderService(Settings(), paths=paths, renderer=FakeRenderer()).run(
            product_ids={"MLB_ATUAL"}
        )


def test_narrate_ignora_roteiros_fora_do_escopo(paths):
    scripts = [
        make_script("MLB_ANTIGO").model_dump(mode="json", by_alias=True),
        make_script("MLB_ATUAL").model_dump(mode="json", by_alias=True),
    ]
    (paths.out / "scripts-20260102T000000Z.json").write_text(json.dumps(scripts), encoding="utf-8")
    write_products(paths, "20260102T000000Z", "MLB_ATUAL")
    service = NarrationService(
        Settings(), paths=paths, provider=FakeTTSProvider(), duration_probe=fake_probe
    )

    tracks = service.run(product_ids=current_product_ids(paths))

    assert [track.product_id for track in tracks] == ["MLB_ATUAL"]
    assert not (paths.audio / "MLB_ANTIGO").exists()


def test_queue_add_nao_reenfileira_produto_que_ja_passou_pela_fila(tmp_path):
    queue = tmp_path / "publications.sqlite3"
    store = build_store("sqlite", queue)
    store.add(
        QueuedPublication(
            product_id="MLB_ANTIGO",
            niche="Cozinha",
            media_path="data/video/MLB_ANTIGO.mp4",
            status=PublicationStatus.PUBLISHED,
            scheduled_for=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
    )
    config = tmp_path / "settings.yaml"
    config.write_text(
        yaml.safe_dump({"publishing": {"backend": "sqlite", "queue_path": str(queue)}}),
        encoding="utf-8",
    )
    video = tmp_path / "MLB_ANTIGO.mp4"
    video.write_bytes(b"mp4")

    result = runner.invoke(
        app,
        [
            "queue-add",
            "--product-id",
            "MLB_ANTIGO",
            "--niche",
            "Cozinha",
            "--media",
            str(video),
            "-c",
            str(config),
        ],
    )

    assert result.exit_code != 0
    assert "ja esta na fila" in result.output
    assert len(store.list_all()) == 1
