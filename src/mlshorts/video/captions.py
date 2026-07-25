"""Legendas dinamicas: quebra a fala de cada cena em blocos curtos com tempo proporcional."""

from __future__ import annotations

from dataclasses import dataclass

from mlshorts.config import VideoConfig
from mlshorts.models import ScriptAudio

# as linhas Format/Style do ASS sao longas por definicao do formato
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Dinamica,{font_name},{font_size},{font_color},{outline_color},&H64000000,-1,0,0,0,100,100,0,0,1,{outline},2,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


@dataclass(frozen=True)
class CaptionCue:
    """Um bloco de legenda com o intervalo exato em que aparece."""

    start_seconds: float
    end_seconds: float
    text: str


def timestamp(seconds: float) -> str:
    """Formato do ASS: h:mm:ss.cc (centesimos)."""
    total = max(seconds, 0.0)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def build_cues(track: ScriptAudio, words_per_chunk: int = 3) -> list[CaptionCue]:
    """Divide cada cena em blocos, dando a cada bloco tempo proporcional ao numero de palavras."""
    chunk_size = max(words_per_chunk, 1)
    cues: list[CaptionCue] = []
    for scene in track.scenes:
        words = scene.text.split()
        if not words:
            continue
        chunks = [words[index : index + chunk_size] for index in range(0, len(words), chunk_size)]
        per_word = scene.duration_seconds / len(words)
        cursor = scene.start_seconds
        for chunk in chunks:
            end = cursor + per_word * len(chunk)
            cues.append(CaptionCue(round(cursor, 3), round(end, 3), " ".join(chunk)))
            cursor = end
    return cues


def build_ass(track: ScriptAudio, config: VideoConfig) -> str:
    """Monta o arquivo ASS que o filtro `subtitles` queima no video."""
    header = ASS_HEADER.format(
        width=config.width,
        height=config.height,
        font_name=config.font_name,
        font_size=config.font_size,
        font_color=config.font_color,
        outline_color=config.outline_color,
        outline=config.outline,
        margin_v=config.caption_margin_bottom,
    )
    lines = [
        f"Dialogue: 0,{timestamp(cue.start_seconds)},{timestamp(cue.end_seconds)},"
        f"Dinamica,,0,0,0,,{_escape(cue.text)}"
        for cue in build_cues(track, config.caption_words_per_chunk)
    ]
    return header + "\n".join(lines) + "\n"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")
