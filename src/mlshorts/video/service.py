"""Orquestra a renderizacao: le os manifestos de narracao e grava os MP4 em data/video/."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mlshorts.config import Settings
from mlshorts.models import ScriptAudio
from mlshorts.storage.paths import Paths
from mlshorts.video.renderer import RenderError, VideoRenderer, find_images

logger = logging.getLogger(__name__)


class RenderService:
    """Cada `data/audio/<product_id>/narration.json` vira um `data/video/<product_id>.mp4`."""

    def __init__(
        self,
        settings: Settings,
        paths: Paths | None = None,
        renderer: VideoRenderer | None = None,
    ) -> None:
        self.settings = settings
        self.paths = paths or Paths()
        self.renderer = renderer or VideoRenderer(settings.video)

    def manifests(self, product_id: str | None = None) -> list[Path]:
        """Prefere o manifesto ao lado dos audios: e o que tem os caminhos reais dos arquivos."""
        pattern = f"{product_id}/narration.json" if product_id else "*/narration.json"
        return sorted(self.paths.audio.glob(pattern))

    def load_track(self, manifest: Path) -> ScriptAudio:
        return ScriptAudio.model_validate(json.loads(manifest.read_text(encoding="utf-8")))

    def run(self, product_id: str | None = None) -> list[Path]:
        self.paths.ensure()
        manifests = self.manifests(product_id)
        if not manifests:
            raise FileNotFoundError(
                f"Nenhum narration.json em {self.paths.audio}: rode `mlshorts narrate` antes."
            )

        rendered: list[Path] = []
        for manifest in manifests:
            track = self.load_track(manifest)
            images = find_images(self.paths.images / track.product_id)
            if not images:
                logger.warning(
                    "%s sem imagens em %s: usando fundo solido",
                    track.product_id,
                    self.paths.images / track.product_id,
                )
            output = self.paths.video / f"{track.product_id}.mp4"
            try:
                rendered.append(self.renderer.render(track, images, output))
            except RenderError as exc:  # uma falha nao derruba os outros produtos
                logger.error("Falha ao renderizar %s: %s", track.product_id, exc)
        return rendered
