"""Leitura dos artefatos de `data/` para o dashboard (sem depender do Streamlit)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from mlshorts.config import Settings, load_settings
from mlshorts.models import Product, QueuedPublication, ScriptAudio, VideoScript
from mlshorts.publish.scheduler import PublicationScheduler
from mlshorts.storage.paths import Paths

logger = logging.getLogger(__name__)

# o CLI passa o settings.yaml escolhido para o processo do Streamlit por variavel de ambiente
CONFIG_ENV_VAR = "MLSHORTS_DASHBOARD_CONFIG"

_ModelT = TypeVar("_ModelT", Product, VideoScript, ScriptAudio)


@dataclass(frozen=True)
class Artifact:
    """Um arquivo de saida de alguma etapa, do mais recente para o mais antigo."""

    path: Path

    @property
    def label(self) -> str:
        return self.path.name


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
    ) -> None:
        self.settings = settings
        self.paths = paths or Paths()
        self.scheduler = scheduler or PublicationScheduler.from_settings(settings)

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
