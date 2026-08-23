"""Etapa 4: imagem gerada por cena, montagem 1080x1920 com FFmpeg e legendas sincronizadas."""

from mlshorts.video.captions import CaptionCue, build_ass, build_cues
from mlshorts.video.renderer import RenderError, VideoRenderer, find_images
from mlshorts.video.scene_images import (
    SceneImageError,
    SceneImageGenerator,
    build_image_prompt,
    build_scene_image_generator,
)
from mlshorts.video.service import RenderService

__all__ = [
    "CaptionCue",
    "RenderError",
    "RenderService",
    "SceneImageError",
    "SceneImageGenerator",
    "VideoRenderer",
    "build_ass",
    "build_cues",
    "build_image_prompt",
    "build_scene_image_generator",
    "find_images",
]
