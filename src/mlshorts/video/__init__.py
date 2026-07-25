"""Etapa 4: montagem 1080x1920 com FFmpeg e legendas dinamicas sincronizadas."""

from mlshorts.video.captions import CaptionCue, build_ass, build_cues
from mlshorts.video.renderer import RenderError, VideoRenderer, find_images
from mlshorts.video.service import RenderService

__all__ = [
    "CaptionCue",
    "RenderError",
    "RenderService",
    "VideoRenderer",
    "build_ass",
    "build_cues",
    "find_images",
]
