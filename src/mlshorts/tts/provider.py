"""Cliente da API de text-to-speech da ElevenLabs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from mlshorts.config import Secrets, TTSConfig, get_secrets

logger = logging.getLogger(__name__)

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"


class TTSError(RuntimeError):
    """Falha na sintese de voz."""


class TTSProvider(Protocol):
    """Sintetizador de voz que grava o audio de um trecho em disco."""

    @property
    def voice_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    def synthesize(self, text: str, output_path: Path) -> Path: ...


class ElevenLabsTTSProvider:
    """Gera a narracao de um trecho via `POST /v1/text-to-speech/{voice_id}`."""

    name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        config: TTSConfig | None = None,
        client: httpx.Client | None = None,
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise TTSError("ELEVENLABS_API_KEY ausente no .env")
        if not voice_id:
            raise TTSError("Voice ID ausente: defina tts.voice_id ou ELEVENLABS_VOICE_ID")
        self.api_key = api_key
        self._voice_id = voice_id
        self.config = config or TTSConfig()
        self._client = client or httpx.Client(base_url=ELEVENLABS_BASE_URL, timeout=timeout)
        self._owns_client = client is None

    @property
    def voice_id(self) -> str:
        return self._voice_id

    @property
    def model_id(self) -> str:
        return self.config.model_id

    def __enter__(self) -> ElevenLabsTTSProvider:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _post(self, text: str) -> bytes:
        response = self._client.post(
            f"/v1/text-to-speech/{self.voice_id}",
            params={"output_format": self.config.output_format},
            headers={"xi-api-key": self.api_key, "Accept": "audio/mpeg"},
            json={
                "text": text,
                "model_id": self.config.model_id,
                "voice_settings": {
                    "stability": self.config.stability,
                    "similarity_boost": self.config.similarity_boost,
                    "style": self.config.style,
                    "use_speaker_boost": self.config.use_speaker_boost,
                },
            },
        )
        response.raise_for_status()
        return response.content

    def synthesize(self, text: str, output_path: Path) -> Path:
        if not text.strip():
            raise TTSError("Texto vazio enviado para sintese")
        try:
            audio = self._post(text)
        except httpx.HTTPStatusError as exc:
            raise TTSError(
                f"ElevenLabs respondeu {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.TransportError as exc:
            raise TTSError(f"Falha de rede na ElevenLabs: {exc}") from exc
        if not audio:
            raise TTSError("ElevenLabs devolveu audio vazio")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)
        logger.debug("Audio gravado em %s (%d bytes)", output_path, len(audio))
        return output_path


def build_provider(config: TTSConfig, secrets: Secrets | None = None) -> ElevenLabsTTSProvider:
    secrets = secrets or get_secrets()
    return ElevenLabsTTSProvider(
        api_key=secrets.elevenlabs_api_key or "",
        voice_id=config.voice_id or secrets.elevenlabs_voice_id or "",
        config=config,
    )
