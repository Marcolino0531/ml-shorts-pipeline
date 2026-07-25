"""Etapa 4: monta o video vertical 1080x1920 com FFmpeg, sincronizado pelo narration.json."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from mlshorts.config import VideoConfig
from mlshorts.models import ScriptAudio
from mlshorts.video.captions import build_ass

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


class RenderError(RuntimeError):
    """Falha na montagem do video."""


class VideoRenderer:
    """Um unico comando FFmpeg: imagem por cena, audios nos offsets do manifesto e legendas."""

    def __init__(self, config: VideoConfig | None = None, binary: str = "ffmpeg") -> None:
        self.config = config or VideoConfig()
        self.binary = binary

    def scene_durations(self, track: ScriptAudio) -> list[float]:
        """A imagem de cada cena cobre a fala e a pausa seguinte, para nao sobrar tela preta."""
        pause = track.pause_between_scenes_seconds
        last = len(track.scenes) - 1
        return [
            round(scene.duration_seconds + (0.0 if index == last else pause), 3)
            for index, scene in enumerate(track.scenes)
        ]

    def build_command(
        self,
        track: ScriptAudio,
        images: list[Path],
        output: Path,
        subtitles: Path,
    ) -> list[str]:
        if not track.scenes:
            raise RenderError(f"{track.product_id}: manifesto de narracao sem cenas")

        config = self.config
        durations = self.scene_durations(track)
        command = [self.binary, "-y", "-v", "error"]

        for index, duration in enumerate(durations):
            image = images[index % len(images)] if images else None
            if image is None:
                command += ["-f", "lavfi", "-t", str(duration)]
                command += [
                    "-i",
                    f"color=c={config.background_color}:s={config.width}x{config.height}:r={config.fps}",
                ]
            else:
                command += ["-loop", "1", "-t", str(duration), "-i", str(image)]

        for scene in track.scenes:
            command += ["-i", scene.audio_path]

        filters = [self._scene_filter(index, duration) for index, duration in enumerate(durations)]
        concat_inputs = "".join(f"[v{index}]" for index in range(len(durations)))
        filters.append(f"{concat_inputs}concat=n={len(durations)}:v=1:a=0[vcat]")
        filters.append(f"[vcat]subtitles={_escape_path(subtitles)}[vout]")

        offset = len(durations)
        for index, scene in enumerate(track.scenes):
            delay = int(round(scene.start_seconds * 1000))
            filters.append(f"[{offset + index}:a]adelay=delays={delay}:all=1[a{index}]")
        mix_inputs = "".join(f"[a{index}]" for index in range(len(track.scenes)))
        filters.append(f"{mix_inputs}amix=inputs={len(track.scenes)}:normalize=0[aout]")

        command += [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-r",
            str(config.fps),
            "-c:v",
            "libx264",
            "-preset",
            config.preset,
            "-crf",
            str(config.crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            config.audio_bitrate,
            "-movflags",
            "+faststart",
            str(output),
        ]
        return command

    def _scene_filter(self, index: int, duration: float) -> str:
        """Enquadra a imagem no vertical (com fundo) e aplica um zoom lento durante a cena."""
        config = self.config
        frames = max(int(round(duration * config.fps)), 1)
        zoom = 1.0 + config.zoom_per_scene
        return (
            f"[{index}:v]scale={config.width}:{config.height}"
            ":force_original_aspect_ratio=decrease"
            f",pad={config.width}:{config.height}:(ow-iw)/2:(oh-ih)/2:{config.background_color}"
            f",setsar=1,fps={config.fps}"
            f",zoompan=z='min(1+{config.zoom_per_scene}*on/{frames},{zoom})'"
            ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={config.width}x{config.height}:fps={config.fps}[v{index}]"
        )

    def render(self, track: ScriptAudio, images: list[Path], output: Path) -> Path:
        if shutil.which(self.binary) is None:
            raise RenderError(f"{self.binary} nao encontrado no PATH")

        missing = [
            scene.audio_path for scene in track.scenes if not Path(scene.audio_path).exists()
        ]
        if missing:
            raise RenderError(f"{track.product_id}: audio ausente {missing}")
        if track.total_duration_seconds > self.config.max_duration_seconds:
            logger.warning(
                "%s tem %.1fs, acima do limite de %ds",
                track.product_id,
                track.total_duration_seconds,
                self.config.max_duration_seconds,
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        subtitles = output.with_suffix(".ass")
        subtitles.write_text(build_ass(track, self.config), encoding="utf-8")

        command = self.build_command(track, images, output, subtitles)
        logger.debug("ffmpeg: %s", " ".join(command))
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RenderError(f"FFmpeg falhou em {track.product_id}: {result.stderr.strip()}")
        logger.info(
            "%s renderizado em %s (%.1fs, %dx%d)",
            track.product_id,
            output,
            track.total_duration_seconds,
            self.config.width,
            self.config.height,
        )
        return output


def find_images(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(path for path in directory.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)


def _escape_path(path: Path) -> str:
    """O filtro subtitles usa `:` como separador de opcoes."""
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
