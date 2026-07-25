from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mlshorts.config import Settings, TTSConfig
from mlshorts.models import Scene, SceneRole, VideoScript
from mlshorts.storage.paths import Paths
from mlshorts.tts.duration import FFprobeDurationProbe
from mlshorts.tts.service import NarrationGenerator, NarrationService

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg indisponivel",
)

DURATIONS = {"gancho": 3.0, "apresentacao": 12.0, "prova_social": 10.0, "cta": 5.0}


def make_script(product_id: str = "MLB123") -> VideoScript:
    scenes = [
        Scene(bloco=role.value, fala_narrador=f"fala do {role.value}", instrucao_visual="zoom")
        for role in SceneRole
    ]
    return VideoScript(product_id=product_id, scenes=scenes, estimated_duration_seconds=30.0)


class FakeTTSProvider:
    name = "fake"
    voice_id = "voice-abc"
    model_id = "eleven_multilingual_v2"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, Path]] = []

    def synthesize(self, text: str, output_path: Path) -> Path:
        if self.error:
            raise self.error
        self.calls.append((text, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"audio")
        return output_path


def fake_probe(path: Path) -> float:
    role = path.stem.split("-", 1)[1]
    return DURATIONS[role]


def test_gera_um_audio_por_cena_com_offsets(tmp_path):
    provider = FakeTTSProvider()
    generator = NarrationGenerator(
        provider,
        config=TTSConfig(pause_between_scenes_seconds=0.5),
        duration_probe=fake_probe,
    )

    track = generator.generate(make_script(), tmp_path)

    assert [scene.audio_path for scene in track.scenes] == [
        str(tmp_path / "MLB123" / name)
        for name in (
            "00-gancho.mp3",
            "01-apresentacao.mp3",
            "02-prova_social.mp3",
            "03-cta.mp3",
        )
    ]
    assert [scene.duration_seconds for scene in track.scenes] == [3.0, 12.0, 10.0, 5.0]
    # cada cena comeca depois da anterior + pausa de 0.5s
    assert [scene.start_seconds for scene in track.scenes] == [0.0, 3.5, 16.0, 26.5]
    assert track.scenes[-1].end_seconds == 31.5
    assert track.speech_duration_seconds == 30.0
    assert track.total_duration_seconds == 31.5
    assert track.voice_id == "voice-abc"
    assert [text for text, _ in provider.calls] == [
        "fala do gancho",
        "fala do apresentacao",
        "fala do prova_social",
        "fala do cta",
    ]


def test_escreve_manifesto_ao_lado_dos_audios(tmp_path):
    generator = NarrationGenerator(FakeTTSProvider(), duration_probe=fake_probe)

    generator.generate(make_script(), tmp_path)

    manifest = json.loads((tmp_path / "MLB123" / "narration.json").read_text(encoding="utf-8"))
    assert manifest["product_id"] == "MLB123"
    assert manifest["speech_duration_seconds"] == 30.0
    # pausa default de 0.25s entre as quatro cenas
    assert manifest["total_duration_seconds"] == 30.75
    assert manifest["scenes"][1]["start_seconds"] == 3.25
    assert manifest["scenes"][1]["role"] == "apresentacao"


def test_service_le_ultimo_scripts_json_e_persiste_narracoes(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure()
    (paths.out / "scripts-20260101T000000Z.json").write_text(
        json.dumps([make_script().model_dump(mode="json", by_alias=True)]), encoding="utf-8"
    )
    service = NarrationService(
        Settings(), paths=paths, provider=FakeTTSProvider(), duration_probe=fake_probe
    )

    tracks = service.run()

    assert [track.product_id for track in tracks] == ["MLB123"]
    assert (paths.audio / "MLB123" / "00-gancho.mp3").exists()
    saved = json.loads(next(paths.out.glob("narration-*.json")).read_text(encoding="utf-8"))
    assert saved[0]["total_duration_seconds"] == pytest.approx(30.75, abs=0.01)


def test_service_filtra_por_produto(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure()
    scripts = [
        make_script("MLB1").model_dump(mode="json", by_alias=True),
        make_script("MLB2").model_dump(mode="json", by_alias=True),
    ]
    (paths.out / "scripts-20260101T000000Z.json").write_text(json.dumps(scripts), encoding="utf-8")
    service = NarrationService(
        Settings(), paths=paths, provider=FakeTTSProvider(), duration_probe=fake_probe
    )

    tracks = service.run(product_id="MLB2")

    assert [track.product_id for track in tracks] == ["MLB2"]


def test_service_sem_arquivo_de_roteiros(tmp_path):
    service = NarrationService(
        Settings(), paths=Paths(tmp_path), provider=FakeTTSProvider(), duration_probe=fake_probe
    )
    with pytest.raises(FileNotFoundError, match="mlshorts script"):
        service.run()


def test_service_registra_falha_e_continua(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure()
    (paths.out / "scripts-20260101T000000Z.json").write_text(
        json.dumps([make_script().model_dump(mode="json", by_alias=True)]), encoding="utf-8"
    )
    service = NarrationService(
        Settings(),
        paths=paths,
        provider=FakeTTSProvider(error=RuntimeError("quota")),
        duration_probe=fake_probe,
    )

    assert service.run() == []
    assert json.loads(next(paths.out.glob("narration-*.json")).read_text(encoding="utf-8")) == []


@requires_ffmpeg
def test_ffprobe_mede_duracao_real(tmp_path):
    """Sanity check com um MP3 de 1s gerado pelo proprio FFmpeg."""
    audio = tmp_path / "silencio.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "1",
            str(audio),
        ],
        capture_output=True,
        check=True,
    )

    assert FFprobeDurationProbe()(audio) == pytest.approx(1.0, abs=0.1)


@requires_ffmpeg
def test_ffprobe_falha_em_arquivo_invalido(tmp_path):
    invalido = tmp_path / "nao-audio.mp3"
    invalido.write_bytes(b"nao sou audio")
    with pytest.raises(RuntimeError):
        FFprobeDurationProbe()(invalido)
