from __future__ import annotations

import json
from typing import Any

import pytest

from mlshorts.config import CategoryConfig, ScriptGenConfig, Secrets, Settings
from mlshorts.models import SceneRole
from mlshorts.scriptgen.generator import ScriptGenerationService, ScriptGenerator, build_provider
from mlshorts.scriptgen.providers import (
    AnthropicScriptProvider,
    OpenAIScriptProvider,
    ScriptGenerationError,
)
from mlshorts.storage.paths import Paths


def scene(bloco: str, fala: str = "fala curta", visual: str = "zoom in") -> dict[str, str]:
    return {"bloco": bloco, "fala_narrador": fala, "instrucao_visual": visual}


def full_payload(**falas: str) -> dict[str, Any]:
    return {
        "cenas": [
            scene(bloco, falas.get(bloco, "fala curta de teste"))
            for bloco in ("gancho", "apresentacao", "prova_social", "cta")
        ]
    }


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, *payloads: dict[str, Any]) -> None:
        self._payloads = list(payloads)
        self.prompts: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        self.prompts.append((system_prompt, user_prompt))
        return self._payloads[min(len(self.prompts) - 1, len(self._payloads) - 1)]


def test_gera_roteiro_com_os_quatro_blocos_e_duracao_estimada(product_factory):
    provider = FakeProvider(full_payload())
    generator = ScriptGenerator(provider, max_duration_seconds=45)

    script = generator.generate(product_factory())

    assert [scene.role for scene in script.scenes] == list(SceneRole)
    assert script.provider == "fake" and script.model == "fake-1"
    # 4 cenas x 4 palavras / 2.6 palavras por segundo
    assert script.estimated_duration_seconds == pytest.approx(6.2, abs=0.1)


def test_ordena_cenas_fora_de_ordem(product_factory):
    payload = {
        "cenas": [scene("cta"), scene("gancho"), scene("prova_social"), scene("apresentacao")]
    }
    script = ScriptGenerator(FakeProvider(payload)).generate(product_factory())

    assert [scene.role for scene in script.scenes] == list(SceneRole)
    assert script.scene_for(SceneRole.GANCHO) is not None


def test_bloco_faltando_gera_erro(product_factory):
    payload = {"cenas": [scene("gancho"), scene("apresentacao"), scene("cta")]}
    with pytest.raises(ScriptGenerationError, match="blocos obrigatorios"):
        ScriptGenerator(FakeProvider(payload)).generate(product_factory())


def test_payload_sem_cenas_gera_erro(product_factory):
    with pytest.raises(ScriptGenerationError, match="sem a lista 'cenas'"):
        ScriptGenerator(FakeProvider({"roteiro": []})).generate(product_factory())


def test_cena_com_campo_vazio_gera_erro(product_factory):
    payload = {
        "cenas": [
            scene("gancho", fala=""),
            scene("apresentacao"),
            scene("prova_social"),
            scene("cta"),
        ]
    }
    with pytest.raises(ScriptGenerationError, match="Cena invalida"):
        ScriptGenerator(FakeProvider(payload)).generate(product_factory())


def test_roteiro_longo_dispara_nova_tentativa_mais_curta(product_factory):
    longa = " ".join(["palavra"] * 200)  # ~77s narrados
    provider = FakeProvider(full_payload(gancho=longa), full_payload())
    generator = ScriptGenerator(provider, max_duration_seconds=45)

    script = generator.generate(product_factory())

    assert len(provider.prompts) == 2
    assert "Reescreva o roteiro inteiro" in provider.prompts[1][1]
    assert script.estimated_duration_seconds <= 45


def test_prompt_do_usuario_traz_dados_reais_do_produto(product_factory):
    provider = FakeProvider(full_payload())
    product = product_factory(attributes={"Marca": "JBL"}, sold_quantity=1500)

    ScriptGenerator(provider).generate(product)

    _, user_prompt = provider.prompts[0]
    assert "Fone Bluetooth" in user_prompt
    assert "4.8" in user_prompt and "320 avaliacoes" in user_prompt
    assert "Unidades vendidas: 1500" in user_prompt
    assert "Marca: JBL" in user_prompt


def test_service_le_ultimo_json_e_persiste_roteiros(tmp_path, product_factory):
    paths = Paths(tmp_path)
    paths.ensure()
    products = [product_factory().model_dump(mode="json")]
    (paths.raw / "products-20260101T000000Z.json").write_text(
        json.dumps(products), encoding="utf-8"
    )

    settings = Settings()
    settings.categories = [CategoryConfig(id="MLB1051")]
    service = ScriptGenerationService(
        settings, paths=paths, secrets=Secrets(), provider=FakeProvider(full_payload())
    )

    scripts = service.run()

    assert [script.product_id for script in scripts] == ["MLB123"]
    saved = json.loads(next(paths.out.glob("scripts-*.json")).read_text(encoding="utf-8"))
    assert saved[0]["scenes"][0]["fala_narrador"] == "fala curta de teste"


def test_service_sem_arquivo_de_produtos(tmp_path):
    service = ScriptGenerationService(
        Settings(), paths=Paths(tmp_path), secrets=Secrets(), provider=FakeProvider(full_payload())
    )
    with pytest.raises(FileNotFoundError, match="mlshorts collect"):
        service.run()


def test_build_provider_escolhe_openai_ou_anthropic():
    openai_provider = build_provider(
        ScriptGenConfig(provider="openai"), Secrets(openai_api_key="sk-test")
    )
    assert isinstance(openai_provider, OpenAIScriptProvider)

    claude_provider = build_provider(
        ScriptGenConfig(provider="claude"), Secrets(anthropic_api_key="sk-ant")
    )
    assert isinstance(claude_provider, AnthropicScriptProvider)

    with pytest.raises(ScriptGenerationError, match="OPENAI_API_KEY"):
        build_provider(ScriptGenConfig(provider="openai"), Secrets())

    with pytest.raises(ScriptGenerationError, match="desconhecido"):
        build_provider(ScriptGenConfig(provider="llama"), Secrets())
