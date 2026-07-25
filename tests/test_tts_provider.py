from __future__ import annotations

import json

import httpx
import pytest
import respx

from mlshorts.config import Secrets, TTSConfig
from mlshorts.tts.provider import (
    ELEVENLABS_BASE_URL,
    ElevenLabsTTSProvider,
    TTSError,
    build_provider,
)

VOICE = "voice-123"
TTS_URL = f"{ELEVENLABS_BASE_URL}/v1/text-to-speech/{VOICE}"


@pytest.fixture
def provider():
    config = TTSConfig(stability=0.3, similarity_boost=0.9, output_format="mp3_44100_128")
    with httpx.Client(base_url=ELEVENLABS_BASE_URL) as client:
        yield ElevenLabsTTSProvider(
            api_key="sk-eleven", voice_id=VOICE, config=config, client=client
        )


@respx.mock
def test_grava_audio_e_envia_voice_settings(provider, tmp_path):
    route = respx.post(TTS_URL).mock(return_value=httpx.Response(200, content=b"ID3-audio"))

    path = provider.synthesize("Pare de gastar demais", tmp_path / "00-gancho.mp3")

    assert path.read_bytes() == b"ID3-audio"
    request = route.calls.last.request
    body = json.loads(request.content)
    assert body["text"] == "Pare de gastar demais"
    assert body["model_id"] == "eleven_multilingual_v2"
    assert body["voice_settings"] == {
        "stability": 0.3,
        "similarity_boost": 0.9,
        "style": 0.0,
        "use_speaker_boost": True,
    }
    assert request.headers["xi-api-key"] == "sk-eleven"
    assert request.url.params["output_format"] == "mp3_44100_128"


@respx.mock
def test_erro_http_vira_tts_error(provider, tmp_path):
    respx.post(TTS_URL).mock(return_value=httpx.Response(401, text="quota exceeded"))
    with pytest.raises(TTSError, match="401"):
        provider.synthesize("texto", tmp_path / "a.mp3")


@respx.mock
def test_audio_vazio_vira_tts_error(provider, tmp_path):
    respx.post(TTS_URL).mock(return_value=httpx.Response(200, content=b""))
    with pytest.raises(TTSError, match="audio vazio"):
        provider.synthesize("texto", tmp_path / "a.mp3")


def test_texto_vazio_nao_chama_a_api(provider, tmp_path):
    with pytest.raises(TTSError, match="Texto vazio"):
        provider.synthesize("   ", tmp_path / "a.mp3")


def test_credenciais_obrigatorias():
    with pytest.raises(TTSError, match="ELEVENLABS_API_KEY"):
        ElevenLabsTTSProvider(api_key="", voice_id=VOICE)
    with pytest.raises(TTSError, match="Voice ID"):
        ElevenLabsTTSProvider(api_key="sk", voice_id="")


def test_build_provider_usa_voice_do_settings_ou_do_env():
    secrets = Secrets(elevenlabs_api_key="sk", elevenlabs_voice_id="do-env")

    assert build_provider(TTSConfig(), secrets).voice_id == "do-env"
    assert build_provider(TTSConfig(voice_id="do-yaml"), secrets).voice_id == "do-yaml"
