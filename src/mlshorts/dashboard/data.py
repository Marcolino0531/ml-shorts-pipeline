"""Leitura dos artefatos de `data/` para o dashboard (sem depender do Streamlit)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from mlshorts.config import Settings, load_settings
from mlshorts.dashboard.youtube_stats import (
    VideoStatistics,
    YouTubeStatsClient,
    video_id_from_url,
)
from mlshorts.models import (
    Product,
    PublicationStatus,
    QueuedPublication,
    ScriptAudio,
    VideoScript,
)
from mlshorts.publish.metadata import MetadataBuilder
from mlshorts.publish.scheduler import PublicationScheduler
from mlshorts.storage.paths import Paths

logger = logging.getLogger(__name__)

# o CLI passa o settings.yaml escolhido para o processo do Streamlit por variavel de ambiente
CONFIG_ENV_VAR = "MLSHORTS_DASHBOARD_CONFIG"

# usado em qualquer metrica indisponivel (video nao publicado ou API fora do ar)
PLACEHOLDER = "--"
YOUTUBE_PLATFORM = "youtube"

STATUS_LABELS = {
    PublicationStatus.PENDING: "na fila de publicacao",
    PublicationStatus.PUBLISHED: "publicado",
    PublicationStatus.FAILED: "falha na publicacao",
    PublicationStatus.CANCELLED: "cancelado",
}
DRAFT_LABEL = "rascunho"

_ModelT = TypeVar("_ModelT", Product, VideoScript, ScriptAudio)


@dataclass(frozen=True)
class Artifact:
    """Um arquivo de saida de alguma etapa, do mais recente para o mais antigo."""

    path: Path

    @property
    def label(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class VideoRow:
    """Um video processado pelo pipeline, com o que se sabe da publicacao dele."""

    product_id: str
    product_title: str
    status: str
    image: Path | None = None
    video: Path | None = None
    caption: str | None = None
    affiliate_link: str | None = None
    published_at: datetime | None = None
    published_urls: dict[str, str] | None = None
    youtube_video_id: str | None = None
    statistics: VideoStatistics | None = None

    @property
    def published_at_display(self) -> str:
        if self.published_at is None:
            return PLACEHOLDER
        return self.published_at.isoformat(timespec="minutes")

    @property
    def views_display(self) -> str:
        return _metric(self.statistics.views if self.statistics else None)

    @property
    def likes_display(self) -> str:
        return _metric(self.statistics.likes if self.statistics else None)

    @property
    def comments_display(self) -> str:
        return _metric(self.statistics.comments if self.statistics else None)

    def as_table_row(self) -> dict[str, str]:
        return {
            "Produto": self.product_title,
            "ID": self.product_id,
            "Status": self.status,
            "Legenda": self.caption or PLACEHOLDER,
            "Link de afiliado": self.affiliate_link or PLACEHOLDER,
            "Publicado em": self.published_at_display,
            "Views (YouTube)": self.views_display,
            "Curtidas": self.likes_display,
            "Comentarios": self.comments_display,
        }


def _metric(value: int | None) -> str:
    return PLACEHOLDER if value is None else f"{value:,}".replace(",", ".")


def load_dashboard_data() -> DashboardData:
    config = os.environ.get(CONFIG_ENV_VAR)
    return DashboardData(load_settings(Path(config) if config else None))


class DashboardData:
    """Fonte unica de dados do painel: produtos, roteiros, narracao, video e fila."""

    def __init__(
        self,
        settings: Settings,
        paths: Paths | None = None,
        scheduler: PublicationScheduler | None = None,
        stats_client: YouTubeStatsClient | None = None,
        metadata_builder: MetadataBuilder | None = None,
    ) -> None:
        self.settings = settings
        self.paths = paths or Paths()
        self.scheduler = scheduler or PublicationScheduler.from_settings(settings)
        self._stats_client = stats_client
        self._metadata_builder = metadata_builder

    @property
    def stats_client(self) -> YouTubeStatsClient:
        if self._stats_client is None:
            self._stats_client = YouTubeStatsClient()
        return self._stats_client

    @property
    def metadata_builder(self) -> MetadataBuilder:
        if self._metadata_builder is None:
            self._metadata_builder = MetadataBuilder(self.settings.publishing)
        return self._metadata_builder

    # -------------------------------------------------------------- arquivos

    def _artifacts(self, directory: Path, pattern: str) -> list[Artifact]:
        if not directory.exists():
            return []
        return [Artifact(path) for path in sorted(directory.glob(pattern), reverse=True)]

    def product_files(self) -> list[Artifact]:
        return self._artifacts(self.paths.raw, "products-*.json")

    def script_files(self) -> list[Artifact]:
        return self._artifacts(self.paths.out, "scripts-*.json")

    def narration_files(self) -> list[Artifact]:
        return self._artifacts(self.paths.out, "narration-*.json")

    # ----------------------------------------------------------------- dados

    def load_products(self, path: Path | None = None) -> list[Product]:
        source = path or self._latest(self.product_files())
        return self._load(source, Product)

    def load_scripts(self, path: Path | None = None) -> list[VideoScript]:
        source = path or self._latest(self.script_files())
        return self._load(source, VideoScript)

    def load_narrations(self, path: Path | None = None) -> list[ScriptAudio]:
        source = path or self._latest(self.narration_files())
        return self._load(source, ScriptAudio)

    @staticmethod
    def _latest(artifacts: list[Artifact]) -> Path | None:
        return artifacts[0].path if artifacts else None

    @staticmethod
    def _load(source: Path | None, model: type[_ModelT]) -> list[_ModelT]:
        if source is None or not source.exists():
            return []
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.error("JSON invalido em %s: %s", source, exc)
            return []
        return [model.model_validate(entry) for entry in raw]

    # ------------------------------------------------------------ midia/fila

    def images_for(self, product_id: str) -> list[Path]:
        directory = self.paths.images / product_id
        if not directory.exists():
            return []
        return sorted(path for path in directory.iterdir() if path.is_file())

    def audio_for(self, product_id: str) -> ScriptAudio | None:
        """Manifesto gravado ao lado dos audios; e o que permite dar play cena por cena."""
        manifest = self.paths.audio / product_id / "narration.json"
        if not manifest.exists():
            return None
        return ScriptAudio.model_validate(json.loads(manifest.read_text(encoding="utf-8")))

    def video_for(self, product_id: str) -> Path | None:
        """Video renderizado da etapa do FFmpeg, quando ja existir."""
        candidates = sorted(self.paths.video.glob(f"{product_id}*.mp4"))
        return candidates[-1] if candidates else None

    def queue(self) -> list[QueuedPublication]:
        items = self.scheduler.store.list_all()
        items.sort(key=lambda item: item.scheduled_for, reverse=True)
        return items

    # -------------------------------------------------------- visao por video

    def video_rows(self, fetch_statistics: bool = True) -> list[VideoRow]:
        """Uma linha por video processado: produto, status editorial e dados da publicacao.

        Videos renderizados que ainda nao entraram na fila aparecem como rascunho. Metricas
        do YouTube so existem para o que ja foi publicado la; o resto fica com `--`.
        """
        products = {product.id: product for product in self.load_products()}
        rows: list[VideoRow] = []
        queued_products: set[str] = set()

        for item in self.queue():
            queued_products.add(item.product_id)
            rows.append(self._row_from_queue(item, products.get(item.product_id)))

        for product_id in self._rendered_product_ids():
            if product_id in queued_products:
                continue
            rows.append(self._draft_row(product_id, products.get(product_id)))

        return self._with_statistics(rows) if fetch_statistics else rows

    def _rendered_product_ids(self) -> list[str]:
        if not self.paths.video.exists():
            return []
        return sorted({path.stem.split("-")[0] for path in self.paths.video.glob("*.mp4")})

    def _row_from_queue(self, item: QueuedPublication, product: Product | None) -> VideoRow:
        metadata = item.metadata
        youtube_url = item.published_urls.get(YOUTUBE_PLATFORM)
        return VideoRow(
            product_id=item.product_id,
            product_title=product.title if product else item.product_id,
            status=STATUS_LABELS.get(item.status, item.status.value),
            image=self._main_image(item.product_id),
            video=Path(item.media_path) if item.media_path else None,
            caption=metadata.description if metadata else None,
            affiliate_link=self._affiliate_link(
                metadata.affiliate_link if metadata else None, product
            ),
            published_at=item.published_at,
            published_urls=dict(item.published_urls),
            youtube_video_id=video_id_from_url(youtube_url) if youtube_url else None,
        )

    def _draft_row(self, product_id: str, product: Product | None) -> VideoRow:
        return VideoRow(
            product_id=product_id,
            product_title=product.title if product else product_id,
            status=DRAFT_LABEL,
            image=self._main_image(product_id),
            video=self.video_for(product_id),
            affiliate_link=self._affiliate_link(None, product),
        )

    def _affiliate_link(self, link: str | None, product: Product | None) -> str | None:
        """Sem publicacao ainda, mostra o link que o `publish` vai usar para o produto."""
        if link:
            return link
        if product is None:
            return None
        return self.metadata_builder.affiliate_link(str(product.permalink))

    def _main_image(self, product_id: str) -> Path | None:
        images = self.images_for(product_id)
        return images[0] if images else None

    def _with_statistics(self, rows: list[VideoRow]) -> list[VideoRow]:
        """Uma chamada em lote; se ela falhar as linhas seguem inteiras, so sem as metricas."""
        video_ids = [row.youtube_video_id for row in rows if row.youtube_video_id]
        if not video_ids:
            return rows
        try:
            stats = self.stats_client.fetch(video_ids)
        except Exception as exc:  # nunca derrubar o painel por causa das metricas
            logger.warning("Falha ao consultar as estatisticas do YouTube: %s", exc)
            return rows
        return [
            replace(row, statistics=stats.get(row.youtube_video_id))
            if row.youtube_video_id
            else row
            for row in rows
        ]

    def pipeline_status(self, product_id: str) -> dict[str, bool]:
        """Marca as etapas ja concluidas para o produto, usada nos indicadores do painel."""
        scripts = {script.product_id for script in self.load_scripts()}
        queued = {item.product_id for item in self.queue()}
        published = {item.product_id for item in self.queue() if item.published_at is not None}
        return {
            "imagens": bool(self.images_for(product_id)),
            "roteiro": product_id in scripts,
            "audio": self.audio_for(product_id) is not None,
            "video": self.video_for(product_id) is not None,
            "na_fila": product_id in queued,
            "publicado": product_id in published,
        }
