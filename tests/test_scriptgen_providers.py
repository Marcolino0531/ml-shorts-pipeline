from __future__ import annotations

import json

import httpx
import pytest
import respx

from mlshorts.scriptgen.providers import (
    ANTHROPIC_DEFAULT_TEMPERATURE,
    ANTHROPIC_URL,
    OPENAI_URL,
    AnthropicScriptProvider,
    OpenAIScriptProvider,
    ScriptGenerationError,
)
from mlshorts.scriptgen.schema import TOOL_NAME

PAYLOAD = {
    "cenas": [
        {"bloco": "gancho", "fala_narrador": "Pare de gastar demais.", "instrucao_visual": "Zoom"},
        {"bloco": "apresentacao", "fala_narrador": "Fone com ANC.", "instrucao_visual": "Pan"},
        {"bloco": "prova_social", "fala_narrador": "812 avaliacoes.", "instrucao_visual": "Texto"},
        {"bloco": "cta", "fala_narrador": "Link na bio.", "instrucao_visual": "Corte seco"},
    ]
}


@respx.mock
def test_openai_envia_json_schema_estrito_e_parseia_conteudo():
    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(PAYLOAD)}}]}
        )
    )
    provider = OpenAIScriptProvider(api_key="sk-test", model="gpt-4o-mini")

    assert provider.generate("sistema", "usuario") == PAYLOAD

    sent = json.loads(route.calls.last.request.content)
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["messages"][0] == {"role": "system", "content": "sistema"}
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-test"


@respx.mock
def test_openai_recusa_vira_erro_de_geracao():
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"refusal": "nope"}}]})
    )
    with pytest.raises(ScriptGenerationError, match="recusou"):
        OpenAIScriptProvider(api_key="sk-test").generate("s", "u")


@respx.mock
def test_openai_conteudo_nao_json_vira_erro():
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "texto"}}]})
    )
    with pytest.raises(ScriptGenerationError, match="nao e JSON valido"):
        OpenAIScriptProvider(api_key="sk-test").generate("s", "u")


@respx.mock
def test_anthropic_usa_tool_calling_forcado():
    route = respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "vou usar a ferramenta"},
                    {"type": "tool_use", "name": TOOL_NAME, "input": PAYLOAD},
                ]
            },
        )
    )
    provider = AnthropicScriptProvider(api_key="sk-ant", model="claude-3-5-sonnet-latest")

    assert provider.generate("sistema", "usuario") == PAYLOAD

    sent = json.loads(route.calls.last.request.content)
    assert sent["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert sent["system"] == "sistema"
    assert route.calls.last.request.headers["x-api-key"] == "sk-ant"


@respx.mock
def test_anthropic_nao_envia_temperature_customizada():
    """Claude recusa (400) temperature fora do padrao; a do settings.yaml fica de fora."""
    route = respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200, json={"content": [{"type": "tool_use", "name": TOOL_NAME, "input": PAYLOAD}]}
        )
    )

    AnthropicScriptProvider(api_key="sk-ant", temperature=0.8).generate("sistema", "usuario")

    assert "temperature" not in json.loads(route.calls.last.request.content)


@respx.mock
def test_anthropic_envia_temperature_apenas_no_valor_padrao():
    route = respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200, json={"content": [{"type": "tool_use", "name": TOOL_NAME, "input": PAYLOAD}]}
        )
    )

    AnthropicScriptProvider(api_key="sk-ant", temperature=ANTHROPIC_DEFAULT_TEMPERATURE).generate(
        "sistema", "usuario"
    )

    sent = json.loads(route.calls.last.request.content)
    assert sent["temperature"] == ANTHROPIC_DEFAULT_TEMPERATURE


@respx.mock
def test_openai_continua_enviando_temperature():
    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(PAYLOAD)}}]}
        )
    )

    OpenAIScriptProvider(api_key="sk-test", temperature=0.8).generate("sistema", "usuario")

    assert json.loads(route.calls.last.request.content)["temperature"] == 0.8


@respx.mock
def test_anthropic_sem_tool_use_vira_erro():
    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(200, json={"content": [{"type": "text", "text": "oi"}]})
    )
    with pytest.raises(ScriptGenerationError, match="tool_use"):
        AnthropicScriptProvider(api_key="sk-ant").generate("s", "u")


def test_provider_sem_api_key_falha():
    with pytest.raises(ScriptGenerationError, match="API key"):
        OpenAIScriptProvider(api_key="")
