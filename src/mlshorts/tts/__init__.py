"""Etapa 3: narracao das cenas via ElevenLabs, com duracao exata por arquivo."""

from mlshorts.tts.duration import DurationProbe, FFprobeDurationProbe
from mlshorts.tts.provider import (
    ELEVENLABS_BASE_URL,
    ElevenLabsTTSProvider,
    TTSError,
    TTSProvider,
    build_provider,
)
from mlshorts.tts.service import NarrationGenerator, NarrationService

__all__ = [
    "ELEVENLABS_BASE_URL",
    "DurationProbe",
    "ElevenLabsTTSProvider",
    "FFprobeDurationProbe",
    "NarrationGenerator",
    "NarrationService",
    "TTSError",
    "TTSProvider",
    "build_provider",
]
