from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from mlshorts.config import Settings, VideoConfig
from mlshorts.models import SceneAudio, SceneRole, ScriptAudio
from mlshorts.storage.paths import Paths
from mlshorts.video import RenderError, RenderService, VideoRenderer, build_ass, build_cues
from mlshorts.video.captions import timestamp
from mlshorts.video.renderer import find_images

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg indisponivel",
)


def make_track(tmp_path, durations=(3.0, 4.0), pause: float = 0.25) -> ScriptAudio:
    scenes: list[SceneAudio] = []
    cursor = 0.0
    roles = list(SceneRole)
    for index, duration in enumerate(durations):
        path = tmp_path / f"{index:02d}.mp3"
        path.write_bytes(b"fake")
        scenes.append(
            SceneAudio(
                index=index,
                role=roles[index],
                text=f"palavra{index} outra{index} terceira{index} quarta{index}",
                audio_path=str(path),
                duration_seconds=duration,
                start_seconds=round(cursor, 3),
            )
        )
        cursor += duration + pause
    return ScriptAudio(
        product_id="MLB1",
        voice_id="voice",
        model_id="eleven_multilingual_v2",
        scenes=scenes,
        pause_between_scenes_seconds=pause,
    )


def test_cues_seguem_a_minutagem_do_manifesto(tmp_path):
    track = make_track(tmp_path)

    cues = build_cues(track, words_per_chunk=2)

    # 4 palavras por cena / 2 = 2 blocos por cena
    assert len(cues) == 4
    assert cues[0].start_seconds == 0.0
    assert cues[0].end_seconds == 1.5
    assert cues[1].end_seconds == 3.0
    # a cena 1 comeca depois da fala + pausa, nunca antes do audio dela
    assert cues[2].start_seconds == track.scenes[1].start_seconds == 3.25
    assert cues[-1].end_seconds == pytest.approx(7.25)


def test_cue_nao_passa_do_fim_da_cena(tmp_path):
    track = make_track(tmp_path, durations=(3.0,), pause=0.0)

    cues = build_cues(track, words_per_chunk=3)

    assert cues[-1].end_seconds == pytest.approx(3.0)


def test_cena_sem_texto_e_ignorada(tmp_path):
    track = make_track(tmp_path, durations=(3.0, 4.0))
    track.scenes[0].text = "   "

    cues = build_cues(track)

    assert all(cue.start_seconds >= track.scenes[1].start_seconds for cue in cues)


def test_timestamp_no_formato_do_ass():
    assert timestamp(0) == "0:00:00.00"
    assert timestamp(75.456) == "0:01:15.46"
    assert timestamp(-1) == "0:00:00.00"


def test_ass_traz_estilo_e_resolucao_vertical(tmp_path):
    track = make_track(tmp_path)

    content = build_ass(track, VideoConfig())

    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "Style: Dinamica,DejaVu Sans,64" in content
    assert content.count("Dialogue: ") == len(build_cues(track))


def test_chaves_no_texto_nao_viram_tag_do_ass(tmp_path):
    track = make_track(tmp_path, durations=(3.0,))
    track.scenes[0].text = "leve {isso} agora"

    content = build_ass(track, VideoConfig())

    assert "(isso)" in content
    assert "{isso}" not in content


def test_imagem_da_cena_cobre_a_pausa_seguinte(tmp_path):
    track = make_track(tmp_path)

    durations = VideoRenderer().scene_durations(track)

    assert durations == [3.25, 4.0]
    assert sum(durations) == pytest.approx(track.total_duration_seconds)


def test_comando_ffmpeg_sincroniza_audio_e_forca_vertical(tmp_path):
    track = make_track(tmp_path)
    image = tmp_path / "produto.jpg"
    image.write_bytes(b"img")
    output = tmp_path / "MLB1.mp4"

    command = VideoRenderer().build_command(track, [image], output, tmp_path / "MLB1.ass")
    joined = " ".join(command)
    filters = command[command.index("-filter_complex") + 1]

    # uma imagem e um audio por cena, na ordem esperada
    assert command.count("-loop") == 2
    assert joined.count(str(image)) == 2
    assert "adelay=delays=0:all=1" in filters
    assert "adelay=delays=3250:all=1" in filters
    assert "amix=inputs=2:normalize=0" in filters
    assert "scale=1080:1920" in filters
    assert "concat=n=2:v=1:a=0" in filters
    assert "subtitles=" in filters
    assert command[-1] == str(output)


def test_sem_imagens_usa_fundo_solido(tmp_path):
    track = make_track(tmp_path, durations=(3.0,))

    command = VideoRenderer().build_command(track, [], tmp_path / "a.mp4", tmp_path / "a.ass")

    assert "-loop" not in command
    assert any(entry.startswith("color=c=black:s=1080x1920") for entry in command)


def test_imagens_ciclam_quando_ha_menos_que_cenas(tmp_path):
    track = make_track(tmp_path, durations=(3.0, 4.0, 5.0))
    image = tmp_path / "unica.png"
    image.write_bytes(b"img")

    command = VideoRenderer().build_command(track, [image], tmp_path / "a.mp4", tmp_path / "a.ass")

    assert command.count(str(image)) == 3


def test_render_recusa_audio_ausente(tmp_path):
    track = make_track(tmp_path, durations=(3.0,))
    track.scenes[0].audio_path = str(tmp_path / "nao-existe.mp3")

    with pytest.raises(RenderError, match="audio ausente"):
        VideoRenderer().render(track, [], tmp_path / "a.mp4")


def test_render_sem_cenas(tmp_path):
    track = ScriptAudio(product_id="MLB1", voice_id="v", model_id="m", scenes=[])

    with pytest.raises(RenderError, match="sem cenas"):
        VideoRenderer().build_command(track, [], tmp_path / "a.mp4", tmp_path / "a.ass")


def test_avisa_quando_passa_do_limite_de_duracao(tmp_path, caplog):
    track = make_track(tmp_path, durations=(30.0, 30.0))

    with pytest.raises(RenderError):  # audio falso: o ffmpeg nao roda de verdade aqui
        VideoRenderer(VideoConfig(max_duration_seconds=45)).render(track, [], tmp_path / "a.mp4")

    assert "acima do limite de 45s" in caplog.text


def test_find_images_ignora_outros_arquivos(tmp_path):
    (tmp_path / "b.jpg").write_bytes(b"x")
    (tmp_path / "a.PNG").write_bytes(b"x")
    (tmp_path / "narration.json").write_text("{}", encoding="utf-8")

    assert [path.name for path in find_images(tmp_path)] == ["a.PNG", "b.jpg"]
    assert find_images(tmp_path / "vazio") == []


class FakeRenderer:
    """Substitui o FFmpeg nos testes do servico."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def render(self, track, images, output):
        self.calls.append((track.product_id, len(images)))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mp4")
        return output


def write_manifest(paths: Paths, product_id: str, track: ScriptAudio) -> None:
    directory = paths.audio / product_id
    directory.mkdir(parents=True, exist_ok=True)
    payload = track.model_dump(mode="json")
    payload["product_id"] = product_id
    (directory / "narration.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_service_renderiza_todos_os_manifestos(tmp_path):
    paths = Paths(tmp_path / "data")
    paths.ensure()
    track = make_track(tmp_path)
    write_manifest(paths, "MLB1", track)
    write_manifest(paths, "MLB2", track)
    (paths.images / "MLB1").mkdir(parents=True)
    (paths.images / "MLB1" / "0.jpg").write_bytes(b"img")
    renderer = FakeRenderer()

    videos = RenderService(Settings(), paths=paths, renderer=renderer).run()

    assert [path.name for path in videos] == ["MLB1.mp4", "MLB2.mp4"]
    assert renderer.calls == [("MLB1", 1), ("MLB2", 0)]


def test_service_filtra_por_produto(tmp_path):
    paths = Paths(tmp_path / "data")
    paths.ensure()
    write_manifest(paths, "MLB1", make_track(tmp_path))
    write_manifest(paths, "MLB2", make_track(tmp_path))
    renderer = FakeRenderer()

    videos = RenderService(Settings(), paths=paths, renderer=renderer).run(product_id="MLB2")

    assert [path.name for path in videos] == ["MLB2.mp4"]


def test_service_sem_narracao(tmp_path):
    paths = Paths(tmp_path / "data")
    paths.ensure()

    with pytest.raises(FileNotFoundError, match="mlshorts narrate"):
        RenderService(Settings(), paths=paths, renderer=FakeRenderer()).run()


@requires_ffmpeg
def test_render_real_gera_mp4_1080x1920(tmp_path):
    """Render de ponta a ponta com audio de silencio: valida resolucao e duracao reais."""
    scenes: list[SceneAudio] = []
    cursor = 0.0
    for index, duration in enumerate((2.0, 3.0)):
        audio = tmp_path / f"{index}.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                str(duration),
                str(audio),
            ],
            check=True,
        )
        scenes.append(
            SceneAudio(
                index=index,
                role=list(SceneRole)[index],
                text="fala curta da cena",
                audio_path=str(audio),
                duration_seconds=duration,
                start_seconds=round(cursor, 3),
            )
        )
        cursor += duration + 0.25
    track = ScriptAudio(
        product_id="MLB_REAL",
        voice_id="v",
        model_id="m",
        scenes=scenes,
        pause_between_scenes_seconds=0.25,
    )
    image = tmp_path / "produto.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=800x800",
            "-frames:v",
            "1",
            str(image),
        ],
        check=True,
    )
    output = tmp_path / "MLB_REAL.mp4"

    VideoRenderer().render(track, [image], output)

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=width,height,codec_type:format=duration",
            "-of",
            "json",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(probe.stdout)
    video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")

    assert (video_stream["width"], video_stream["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert float(info["format"]["duration"]) == pytest.approx(track.total_duration_seconds, abs=0.3)
    assert output.with_suffix(".ass").exists()
