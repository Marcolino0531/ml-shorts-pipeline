"""Etapa 3: transforma os roteiros em narracao, uma cena por arquivo de audio."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from mlshorts.config import Secrets, Settings, TTSConfig, get_secrets
from mlshorts.models import SceneAudio, ScriptAudio, VideoScript
from mlshorts.storage.paths import Paths
from mlshorts.tts.duration import DurationProbe, FFprobeDurationProbe
from mlshorts.tts.provider import TTSProvider, build_provider

logger = logging.getLogger(__name__)


class NarrationGenerator:
    """Gera um audio por cena e calcula a duracao exata de cada um."""

    def __init__(
        self,
        provider: TTSProvider,
        config: TTSConfig | None = None,
        duration_probe: DurationProbe | None = None,
    ) -> None:
        self.provider = provider
        self.config = config or TTSConfig()
        self.duration_probe = duration_probe or FFprobeDurationProbe()

    def generate(self, script: VideoScript, output_dir: Path) -> ScriptAudio:
        target_dir = output_dir / script.product_id
        target_dir.mkdir(parents=True, exist_ok=True)

        scenes: list[SceneAudio] = []
        cursor = 0.0
        for index, scene in enumerate(script.scenes):
            filename = f"{index:02d}-{scene.role.value}{self.config.file_extension}"
            path = self.provider.synthesize(scene.narration, target_dir / filename)
            duration = self.duration_probe(path)
            scenes.append(
                SceneAudio(
                    index=index,
                    role=scene.role,
                    text=scene.narration,
                    audio_path=str(path),
                    duration_seconds=duration,
                    start_seconds=round(cursor, 3),
                )
            )
            cursor += duration + self.config.pause_between_scenes_seconds

        audio = ScriptAudio(
            product_id=script.product_id,
            voice_id=self.provider.voice_id,
            model_id=self.provider.model_id,
            scenes=scenes,
            pause_between_scenes_seconds=self.config.pause_between_scenes_seconds,
        )
        self._write_manifest(audio, target_dir)
        return audio

    def _write_manifest(self, audio: ScriptAudio, target_dir: Path) -> Path:
        """Manifesto ao lado dos audios: o FFmpeg usa os offsets para sincronizar as imagens."""
        path = target_dir / "narration.json"
        payload = audio.model_dump(mode="json")
        payload["speech_duration_seconds"] = round(audio.speech_duration_seconds, 3)
        payload["total_duration_seconds"] = round(audio.total_duration_seconds, 3)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


class NarrationService:
    """Le `data/out/scripts-*.json`, narra cada roteiro e salva os audios em `data/audio/`."""

    def __init__(
        self,
        settings: Settings,
        paths: Paths | None = None,
        secrets: Secrets | None = None,
        provider: TTSProvider | None = None,
        duration_probe: DurationProbe | None = None,
    ) -> None:
        self.settings = settings
        self.paths = paths or Paths()
        self.secrets = secrets or get_secrets()
        self.generator = NarrationGenerator(
            provider or build_provider(settings.tts, self.secrets),
            config=settings.tts,
            duration_probe=duration_probe,
        )

    def latest_scripts_file(self) -> Path:
        files = sorted(self.paths.out.glob("scripts-*.json"))
        if not files:
            raise FileNotFoundError(
                f"Nenhum scripts-*.json em {self.paths.out}: rode `mlshorts script` antes."
            )
        return files[-1]

    def load_scripts(self, path: Path | None = None) -> list[VideoScript]:
        source = path or self.latest_scripts_file()
        raw = json.loads(source.read_text(encoding="utf-8"))
        return [VideoScript.model_validate(entry) for entry in raw]

    def run(
        self, scripts_file: Path | None = None, product_id: str | None = None
    ) -> list[ScriptAudio]:
        self.paths.ensure()
        tracks: list[ScriptAudio] = []
        for script in self.load_scripts(scripts_file):
            if product_id and script.product_id != product_id:
                continue
            try:
                track = self.generator.generate(script, self.paths.audio)
            except Exception as exc:  # noqa: BLE001 - uma falha nao derruba os outros produtos
                logger.error("Falha ao narrar %s: %s", script.product_id, exc)
                continue
            logger.info(
                "%s narrado em %d cenas, %.1fs no total",
                track.product_id,
                len(track.scenes),
                track.total_duration_seconds,
            )
            tracks.append(track)
        self._persist(tracks)
        return tracks

    def _persist(self, tracks: list[ScriptAudio]) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.paths.out / f"narration-{stamp}.json"
        payload = [
            {
                **track.model_dump(mode="json"),
                "total_duration_seconds": round(track.total_duration_seconds, 3),
            }
            for track in tracks
        ]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Narracoes salvas em %s", path)
        return path
