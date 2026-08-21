"""Acentuacao do portugues preservada do roteiro ate a narracao e a legenda."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from mlshorts.config import ScriptGenConfig, Settings, TTSConfig, VideoConfig
from mlshorts.models import Product, ProductImage, SceneRole
from mlshorts.scriptgen.generator import ScriptGenerationService, ScriptGenerator
from mlshorts.scriptgen.prompts import build_system_prompt, build_user_prompt
from mlshorts.storage.paths import Paths
from mlshorts.tts.provider import ElevenLabsTTSProvider
from mlshorts.tts.service import NarrationGenerator
from mlshorts.video.captions import build_ass, build_cues

FALAS = {
    SceneRole.GANCHO: "Esse suporte de aço não custa nem duzentos reais.",
    SceneRole.APRESENTACAO: "A organização da bancada muda com três divisórias.",
    SceneRole.PROVA_SOCIAL: "Mais de novecentas vendas e cento e vinte avaliações.",
    SceneRole.CTA: "O link está na descrição deste vídeo.",
}
ACENTOS = ("aço", "não", "organização", "avaliações", "descrição", "vídeo")


def make_product() -> Product:
    return Product(
        id="MLB123",
        title="Organizador de Bancada em Aço Inox",
        permalink="https://produto.mercadolivre.com.br/MLB123",
        category_id="MLB1618",
        category_name="Cozinha",
        price=189.9,
        sold_quantity=940,
        rating=4.8,
        reviews_total=127,
        images=[ProductImage(id="img-0", url="https://http2.mlstatic.com/D_0-F.jpg", width=1200)],
    )


class StubLLM:
    """Devolve o payload estruturado que a OpenAI/Anthropic devolveria."""

    name = "stub"
    model = "stub-1"

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> dict[str, object]:
        self.prompts.append((system_prompt, user_prompt))
        return {
            "cenas": [
                {
                    "bloco": role.value,
                    "fala_narrador": fala,
                    "instrucao_visual": "zoom lento na imagem principal",
                }
                for role, fala in FALAS.items()
            ]
        }


class RecordingTTS:
    name = "fake"
    voice_id = "voice-abc"
    model_id = "eleven_multilingual_v2"

    def __init__(self) -> None:
        self.textos: list[str] = []

    def synthesize(self, text: str, output_path: Path) -> Path:
        self.textos.append(text)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"audio")
        return output_path


def fake_probe(path: Path) -> float:
    return 4.0


def test_prompt_pede_acentuacao_e_e_escrito_com_acento():
    system = build_system_prompt(45, 110)

    assert "acentuação" in system
    assert "português do Brasil" in system
    # o LLM imita o estilo do prompt: nada de bloco de instrucao sem acento
    assert "portugues do Brasil" not in system
    assert "instrucao" not in system

    user = build_user_prompt(make_product())
    assert "Título:" in user and "Preço:" in user and "Nota média:" in user


def test_roteiro_gerado_e_salvo_preserva_a_acentuacao(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure()
    (paths.raw / "products-20260101T000000Z.json").write_text(
        json.dumps([make_product().model_dump(mode="json")], ensure_ascii=False),
        encoding="utf-8",
    )
    service = ScriptGenerationService(Settings(), paths=paths, provider=StubLLM())

    scripts = service.run()

    (script,) = scripts
    gancho = script.scene_for(SceneRole.GANCHO)
    assert gancho is not None
    assert gancho.narration == FALAS[SceneRole.GANCHO]

    salvo = next(paths.out.glob("scripts-*.json"))
    bruto = salvo.read_text(encoding="utf-8")
    for palavra in ACENTOS:
        # gravado como texto, nao como escape \uXXXX
        assert palavra in bruto
    recarregado = json.loads(bruto)
    assert recarregado[0]["scenes"][0]["fala_narrador"] == FALAS[SceneRole.GANCHO]


def test_narracao_recebe_o_texto_acentuado_e_grava_no_manifesto(tmp_path):
    script = ScriptGenerator(StubLLM(), ScriptGenConfig()).generate(make_product())
    provider = RecordingTTS()

    generator = NarrationGenerator(provider, config=TTSConfig(), duration_probe=fake_probe)
    track = generator.generate(script, tmp_path)

    assert provider.textos == list(FALAS.values())
    assert [scene.text for scene in track.scenes] == list(FALAS.values())

    manifesto = (tmp_path / "MLB123" / "narration.json").read_text(encoding="utf-8")
    for palavra in ACENTOS:
        assert palavra in manifesto


@respx.mock
def test_payload_da_elevenlabs_chega_com_o_texto_acentuado(tmp_path):
    route = respx.post("https://api.elevenlabs.io/v1/text-to-speech/voice-abc").mock(
        return_value=httpx.Response(200, content=b"mp3")
    )
    provider = ElevenLabsTTSProvider(api_key="k", voice_id="voice-abc")

    provider.synthesize("Panela de aço: não enferruja.", tmp_path / "0.mp3")

    enviado = json.loads(route.calls.last.request.content.decode("utf-8"))
    assert enviado["text"] == "Panela de aço: não enferruja."


def test_legenda_queimada_mantem_a_acentuacao(tmp_path):
    script = ScriptGenerator(StubLLM(), ScriptGenConfig()).generate(make_product())
    track = NarrationGenerator(
        RecordingTTS(), config=TTSConfig(), duration_probe=fake_probe
    ).generate(script, tmp_path)

    cues = build_cues(track, words_per_chunk=3)
    assert "aço" in " ".join(cue.text for cue in cues)

    ass_path = tmp_path / "MLB123.ass"
    ass_path.write_text(build_ass(track, VideoConfig()), encoding="utf-8")
    conteudo = ass_path.read_text(encoding="utf-8")
    for palavra in ACENTOS:
        assert palavra in conteudo
