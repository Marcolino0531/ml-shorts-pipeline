"""Rodada do timer: cada etapa so roda se a anterior entregou o artefato desta execucao."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from mlshorts.config import Settings
from mlshorts.models import (
    Product,
    PublicationStatus,
    QueuedPublication,
    Scene,
    SceneAudio,
    SceneRole,
    ScriptAudio,
    VideoScript,
)
from mlshorts.pipeline import DailyPipeline, PipelineError
from mlshorts.storage.paths import Paths

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def make_script(product_id: str) -> VideoScript:
    return VideoScript(
        product_id=product_id,
        scenes=[
            Scene(
                role=SceneRole.GANCHO,
                narration="Olha o preço disso!",
                visual="Corte seco no produto",
            )
        ],
        estimated_duration_seconds=4.0,
    )


def make_track(product_id: str) -> ScriptAudio:
    return ScriptAudio(
        product_id=product_id,
        voice_id="voz",
        model_id="modelo",
        scenes=[
            SceneAudio(
                index=0,
                role=SceneRole.GANCHO,
                text="Olha o preço disso!",
                audio_path=f"data/audio/{product_id}-gancho.mp3",
                duration_seconds=4.0,
            )
        ],
    )


class Steps:
    """Servicos falsos que registram a ordem das chamadas da rodada."""

    def __init__(
        self,
        products: list[Product],
        scripts: list[VideoScript] | None = None,
        tracks: list[ScriptAudio] | None = None,
        videos: list[Path] | None = None,
        fail: str | None = None,
        publication_status: PublicationStatus = PublicationStatus.PUBLISHED,
    ) -> None:
        self.calls: list[str] = []
        self.products = products
        self.scripts = scripts if scripts is not None else [make_script(p.id) for p in products]
        self.tracks = tracks if tracks is not None else [make_track(p.id) for p in products]
        self.videos = (
            videos if videos is not None else [Path(f"data/video/{p.id}.mp4") for p in products]
        )
        self.fail = fail
        self.publication_status = publication_status

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError(f"boom em {name}")

    # ----------------------------------------------------- dubles dos servicos

    def collect(self) -> list[Product]:
        self._record("collect")
        return self.products

    def run_scripts(self, products_file: Path | None = None) -> list[VideoScript]:
        self._record("script")
        return self.scripts

    def run_narration(
        self, scripts_file: Path | None = None, product_id: str | None = None
    ) -> list[ScriptAudio]:
        self._record("narrate")
        return [t for t in self.tracks if product_id is None or t.product_id == product_id]

    def run_render(self, product_id: str | None = None) -> list[Path]:
        self._record("render")
        return [v for v in self.videos if product_id is None or v.stem == product_id]

    def submit(
        self,
        product_id: str,
        niche: str,
        media_path: str,
        metadata: object | None = None,
        now: datetime | None = None,
    ) -> QueuedPublication:
        self._record("publish")
        return QueuedPublication(
            product_id=product_id,
            niche=niche,
            media_path=media_path,
            status=self.publication_status,
            scheduled_for=NOW,
            published_at=NOW if self.publication_status is PublicationStatus.PUBLISHED else None,
            published_urls=(
                {"youtube": "https://youtu.be/abc"}
                if self.publication_status is PublicationStatus.PUBLISHED
                else {}
            ),
            error="upload recusado"
            if self.publication_status is PublicationStatus.FAILED
            else None,
        )

    def build_for(
        self, product_id: str, niche: str, media_path: str | Path | None = None
    ) -> object | None:
        return None


class ScriptgenStub:
    def __init__(self, steps: Steps) -> None:
        self.run = steps.run_scripts


class NarrationStub:
    def __init__(self, steps: Steps) -> None:
        self.run = steps.run_narration


class RenderStub:
    def __init__(self, steps: Steps) -> None:
        self.run = steps.run_render


def build_pipeline(steps: Steps, tmp_path: Path) -> DailyPipeline:
    return DailyPipeline(
        Settings(),
        paths=Paths(tmp_path),
        collection=steps,  # type: ignore[arg-type]
        scriptgen=ScriptgenStub(steps),  # type: ignore[arg-type]
        narration=NarrationStub(steps),  # type: ignore[arg-type]
        render=RenderStub(steps),  # type: ignore[arg-type]
        scheduler=steps,  # type: ignore[arg-type]
        metadata=steps,  # type: ignore[arg-type]
    )


@pytest.fixture
def product(product_factory):
    return product_factory(id="MLB999", category_id="MLB1618", category_name="Cozinha")


def test_rodada_completa_publica_o_video_desta_execucao(product, tmp_path):
    steps = Steps([product])
    outcomes = build_pipeline(steps, tmp_path).run()

    assert steps.calls == ["collect", "script", "narrate", "render", "publish"]
    assert [o.product_id for o in outcomes] == ["MLB999"]
    assert outcomes[0].niche == "Cozinha"
    assert outcomes[0].video == Path("data/video/MLB999.mp4")
    assert outcomes[0].published


def test_coleta_sem_produtos_aprovados_para_a_rodada(tmp_path):
    steps = Steps([])
    with pytest.raises(PipelineError, match="coleta sem produtos aprovados"):
        build_pipeline(steps, tmp_path).run()
    assert steps.calls == ["collect"]


@pytest.mark.parametrize(
    ("falha", "esperado"),
    [
        ("collect", ["collect"]),
        ("script", ["collect", "script"]),
        ("narrate", ["collect", "script", "narrate"]),
        ("render", ["collect", "script", "narrate", "render"]),
    ],
)
def test_falha_de_etapa_interrompe_antes_de_publicar(product, tmp_path, falha, esperado):
    steps = Steps([product], fail=falha)
    with pytest.raises(PipelineError, match=f"boom em {falha}"):
        build_pipeline(steps, tmp_path).run()
    assert steps.calls == esperado
    assert "publish" not in steps.calls


def test_roteiro_de_outro_produto_nao_vira_video(product, tmp_path):
    # o servico grava o arquivo inteiro: um roteiro de rodada anterior nao autoriza publicar
    steps = Steps([product], scripts=[make_script("MLB_ANTIGO")])
    with pytest.raises(PipelineError, match="nenhum roteiro gerado"):
        build_pipeline(steps, tmp_path).run()
    assert steps.calls == ["collect", "script"]


def test_narracao_vazia_interrompe_antes_do_render(product, tmp_path):
    steps = Steps([product], tracks=[])
    with pytest.raises(PipelineError, match="sem narracao"):
        build_pipeline(steps, tmp_path).run()
    assert steps.calls == ["collect", "script", "narrate"]


def test_render_sem_mp4_do_produto_atual_interrompe_antes_de_publicar(product, tmp_path):
    # video de execucao anterior em data/video/ nao serve como saida desta rodada
    steps = Steps([product], videos=[Path("data/video/MLB_ANTIGO.mp4")])
    with pytest.raises(PipelineError, match="sem MP4"):
        build_pipeline(steps, tmp_path).run()
    assert steps.calls == ["collect", "script", "narrate", "render"]
    assert "publish" not in steps.calls


def test_publicacao_falha_encerra_a_rodada_com_erro(product, tmp_path):
    steps = Steps([product], publication_status=PublicationStatus.FAILED)
    with pytest.raises(PipelineError, match="upload recusado"):
        build_pipeline(steps, tmp_path).run()


def test_item_agendado_nao_conta_como_publicado(product, tmp_path):
    steps = Steps([product], publication_status=PublicationStatus.PENDING)
    outcomes = build_pipeline(steps, tmp_path).run()
    assert outcomes[0].published is False
