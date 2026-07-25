"""Etapa 2: geracao do roteiro de ate 45s (formato Viral Hook) via OpenAI/Claude."""

from mlshorts.scriptgen.generator import (
    ScriptGenerationService,
    ScriptGenerator,
    build_provider,
)
from mlshorts.scriptgen.prompts import build_system_prompt, build_user_prompt
from mlshorts.scriptgen.providers import (
    AnthropicScriptProvider,
    LLMProvider,
    OpenAIScriptProvider,
    ScriptGenerationError,
)
from mlshorts.scriptgen.schema import SCRIPT_JSON_SCHEMA, TOOL_NAME

__all__ = [
    "SCRIPT_JSON_SCHEMA",
    "TOOL_NAME",
    "AnthropicScriptProvider",
    "LLMProvider",
    "OpenAIScriptProvider",
    "ScriptGenerationError",
    "ScriptGenerationService",
    "ScriptGenerator",
    "build_provider",
    "build_system_prompt",
    "build_user_prompt",
]
