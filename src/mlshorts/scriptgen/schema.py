"""Schema da resposta estruturada exigida do LLM (JSON mode / tool calling)."""

from __future__ import annotations

from typing import Any

from mlshorts.models import SceneRole

TOOL_NAME = "gerar_roteiro"
TOOL_DESCRIPTION = "Devolve o roteiro do video curto dividido nas cenas do formato Viral Hook."
SCENE_ROLES: list[str] = [role.value for role in SceneRole]

SCRIPT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cenas"],
    "properties": {
        "cenas": {
            "type": "array",
            "minItems": len(SCENE_ROLES),
            "maxItems": len(SCENE_ROLES),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["bloco", "fala_narrador", "instrucao_visual"],
                "properties": {
                    "bloco": {
                        "type": "string",
                        "enum": SCENE_ROLES,
                        "description": "Bloco do formato Viral Hook, na ordem do enum.",
                    },
                    "fala_narrador": {
                        "type": "string",
                        "description": "Texto exato narrado nesta cena, em portugues do Brasil.",
                    },
                    "instrucao_visual": {
                        "type": "string",
                        "description": "Instrucao de edicao vertical 1080x1920 para esta cena.",
                    },
                },
            },
        }
    },
}
