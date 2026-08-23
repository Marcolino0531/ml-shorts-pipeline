"""Orquestra a renderizacao: gera as imagens das cenas e grava os MP4 em data/video/."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mlshorts.config import Secrets, Settings, get_secrets
from mlshorts.models import SceneRole, ScriptAudio, VideoScript
from mlshorts.storage.paths import Paths
from mlshorts.video.renderer import RenderError, VideoRenderer, find_images
from mlshorts.video.scene_images import (
    SceneImageError,
    SceneImageProvider,
    build_scene_image_generator,
)

logger = logging.getLogger(__name__)

GENERATED_DIRNAME = "gerado"


class RenderService:
    """Cada `data/audio/<product_id>/narration.json` vira um `data/video/<product_id>.mp4`."""

    def __init__(
        self,
        settings: Settings,
        paths: Paths | None = None,
        renderer: VideoRenderer | None = None,
        secrets: Secrets | None = None,
        image_generator: SceneImageProvider | None = None,
    ) -> None:
        self.settings = settings
        self.paths = paths or Paths()
        self.renderer = renderer or VideoRenderer(settings.video)
        self.image_generator = image_generator or build_scene_image_generator(
            settings.imagegen, secrets or get_secrets()
        )

    def manifests(self, product_id: str | None = None) -> list[Path]:
        """Prefere o manifesto ao lado dos audios: e o que tem os caminhos reais dos arquivos."""
        pattern = f"{product_id}/narration.json" if product_id else "*/narration.json"
        return sorted(self.paths.audio.glob(pattern))

    def load_track(self, manifest: Path) -> ScriptAudio:
        return ScriptAudio.model_validate(json.loads(manifest.read_text(encoding="utf-8")))

    def generated_dir(self, product_id: str) -> Path:
        return self.paths.images / GENERATED_DIRNAME / product_id

    def scene_images(self, track: ScriptAudio, photos: list[Path]) -> list[Path | None]:
        """Uma imagem gerada por bloco; o bloco que falhar cai na foto real do produto."""
        visuals = self.scene_visuals(track)
        images: list[Path | None] = []
        for index, scene in enumerate(track.scenes):
            photo = photos[index % len(photos)] if photos else None
            visual = visuals.get(scene.role)
            if self.image_generator is None or not visual:
                images.append(photo)
                continue
            target = self.generated_dir(track.product_id) / f"{scene.role.value}.png"
            try:
                images.append(self.image_generator.generate(visual, target))
            except SceneImageError as exc:
                logger.warning(
                    "%s bloco %s sem imagem gerada (%s): usando a foto real do produto",
                    track.product_id,
                    scene.role.value,
                    exc,
                )
                images.append(photo)
        return images

    def scene_visuals(self, track: ScriptAudio) -> dict[SceneRole, str]:
        """O manifesto ja traz o visual; manifestos antigos caem no scripts-*.json mais recente."""
        visuals: dict[SceneRole, str] = {}
        for scene in track.scenes:
            if scene.visual:
                visuals[scene.role] = scene.visual
        if len(visuals) == len(track.scenes):
            return visuals
        for script in self._scripts_for(track.product_id):
            for script_scene in script.scenes:
                visuals.setdefault(script_scene.role, script_scene.visual)
            break
        return visuals

    def _scripts_for(self, product_id: str) -> list[VideoScript]:
        for path in sorted(self.paths.out.glob("scripts-*.json"), reverse=True):
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
                scripts = [VideoScript.model_validate(entry) for entry in entries]
            except Exception as exc:  # noqa: BLE001 - roteiro invalido nao impede o render
                logger.debug("Ignorando %s ao buscar o visual das cenas: %s", path, exc)
                continue
            found = [script for script in scripts if script.product_id == product_id]
            if found:
                return found
        return []

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
            photos = find_images(self.paths.images / track.product_id)
            if not photos:
                logger.warning(
                    "%s sem fotos em %s: sem fallback se a geracao de imagem falhar",
                    track.product_id,
                    self.paths.images / track.product_id,
                )
            images = self.scene_images(track, photos)
            output = self.paths.video / f"{track.product_id}.mp4"
            try:
                rendered.append(self.renderer.render(track, images, output))
            except RenderError as exc:  # uma falha nao derruba os outros produtos
                logger.error("Falha ao renderizar %s: %s", track.product_id, exc)
        return rendered
