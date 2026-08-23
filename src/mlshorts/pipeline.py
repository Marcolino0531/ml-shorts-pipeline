"""Rodada de ponta a ponta para o timer: coleta -> roteiro -> narracao -> render -> publicacao.

Diferente dos comandos soltos, esta rodada para na primeira etapa que falhar e so publica o
video que ela mesma acabou de renderizar, para nunca subir sobra de uma execucao anterior.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from mlshorts.collectors.service import CollectionService
from mlshorts.config import Settings
from mlshorts.models import (
    Product,
    PublicationStatus,
    QueuedPublication,
    ScriptAudio,
    VideoScript,
)
from mlshorts.publish import MetadataService, PublicationScheduler, build_publisher
from mlshorts.publish.metadata import niche_for
from mlshorts.scriptgen import ScriptGenerationService
from mlshorts.storage.paths import Paths
from mlshorts.tts import NarrationService
from mlshorts.video import RenderService

logger = logging.getLogger(__name__)

T = TypeVar("T")


class PipelineError(RuntimeError):
    """Uma etapa falhou: a rodada para aqui, sem publicar nada do que veio depois."""


@dataclass(frozen=True)
class PipelineOutcome:
    """Resultado de um produto que atravessou a rodada inteira."""

    product_id: str
    niche: str
    video: Path
    publication: QueuedPublication

    @property
    def published(self) -> bool:
        return self.publication.status is PublicationStatus.PUBLISHED


class DailyPipeline:
    """Encadeia os servicos das etapas 1 a 5 em uma execucao unica e sequencial."""

    def __init__(
        self,
        settings: Settings,
        paths: Paths | None = None,
        dry_run: bool = False,
        collection: CollectionService | None = None,
        scriptgen: ScriptGenerationService | None = None,
        narration: NarrationService | None = None,
        render: RenderService | None = None,
        scheduler: PublicationScheduler | None = None,
        metadata: MetadataService | None = None,
    ) -> None:
        self.settings = settings
        self.paths = paths or Paths()
        self.collection = collection or CollectionService(settings, paths=self.paths)
        self.scriptgen = scriptgen or ScriptGenerationService(settings, paths=self.paths)
        self.narration = narration or NarrationService(settings, paths=self.paths)
        self.render = render or RenderService(settings, paths=self.paths)
        self.scheduler = scheduler or PublicationScheduler.from_settings(
            settings, publisher=build_publisher(settings.publishing, dry_run=dry_run)
        )
        self.metadata = metadata or MetadataService(settings.publishing, paths=self.paths)

    # -------------------------------------------------------------------- etapas

    def _step(self, name: str, action: Callable[[], T]) -> T:
        logger.info("== %s", name)
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - qualquer falha encerra a rodada
            raise PipelineError(f"{name}: {exc}") from exc

    def collect(self) -> list[Product]:
        products = self._step("coleta", lambda: self.collection.collect())
        if not products:
            raise PipelineError(
                "coleta sem produtos novos aprovados (ofertas ja processadas sao descartadas): "
                "nada a renderizar nem publicar"
            )
        logger.info("%d produtos aprovados: %s", len(products), ", ".join(p.id for p in products))
        return products

    def write_scripts(self, products: list[Product]) -> list[VideoScript]:
        wanted = {product.id for product in products}
        scripts = self._step("roteiro", lambda: self.scriptgen.run(product_ids=wanted))
        current = [script for script in scripts if script.product_id in wanted]
        if not current:
            raise PipelineError("nenhum roteiro gerado para os produtos desta coleta")
        return current

    def narrate(self, product_id: str) -> ScriptAudio:
        tracks = self._step(
            f"narracao {product_id}", lambda: self.narration.run(product_id=product_id)
        )
        for track in tracks:
            if track.product_id == product_id:
                return track
        raise PipelineError(f"{product_id} sem narracao: ElevenLabs nao gerou os audios das cenas")

    def render_video(self, product_id: str) -> Path:
        videos = self._step(f"render {product_id}", lambda: self.render.run(product_id=product_id))
        for video in videos:
            if video.stem == product_id:
                return video
        raise PipelineError(f"{product_id} sem MP4: o render nao produziu o video desta rodada")

    def niche_for(self, product: Product) -> str:
        return niche_for(product)

    def publish(self, product: Product, video: Path) -> QueuedPublication:
        niche = self.niche_for(product)
        item = self._step(
            f"publicacao {product.id}",
            lambda: self.scheduler.submit(
                product_id=product.id,
                niche=niche,
                media_path=str(video),
                metadata=self.metadata.build_for(product.id, niche, media_path=video),
            ),
        )
        if item.status is PublicationStatus.FAILED:
            raise PipelineError(f"publicacao de {product.id} falhou: {item.error}")
        if item.status is not PublicationStatus.PUBLISHED:
            logger.warning(
                "%s nao foi publicado agora (status %s, agendado para %s): intervalo do nicho %s "
                "ou aprovacao manual pendente",
                product.id,
                item.status.value,
                item.scheduled_for.isoformat(timespec="minutes"),
                niche,
            )
        return item

    # -------------------------------------------------------------------- rodada

    def run(self) -> list[PipelineOutcome]:
        """Coleta, roteiriza, narra, renderiza e publica; levanta PipelineError na 1a falha."""
        self.paths.ensure()
        products = self.collect()
        scripts = self.write_scripts(products)
        by_id = {product.id: product for product in products}

        outcomes: list[PipelineOutcome] = []
        for script in scripts:
            product = by_id[script.product_id]
            self.narrate(product.id)
            video = self.render_video(product.id)
            item = self.publish(product, video)
            outcomes.append(
                PipelineOutcome(
                    product_id=product.id,
                    niche=item.niche,
                    video=video,
                    publication=item,
                )
            )
        return outcomes
