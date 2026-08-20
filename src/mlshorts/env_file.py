"""Escrita pontual no `.env`.

Necessario porque alguns segredos rotacionam sozinhos: o Mercado Livre invalida o
`refresh_token` a cada troca e devolve um novo, que precisa sobreviver ao fim do processo
para a proxima execucao (cron) nao exigir login manual.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from mlshorts.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
_NEEDS_QUOTES = re.compile(r"[\s#\"']")


def _format_value(value: str) -> str:
    if _NEEDS_QUOTES.search(value):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def set_env_value(name: str, value: str, env_file: Path | None = None) -> Path:
    """Grava `name=value` no `.env`, preservando comentarios e as demais variaveis."""
    path = env_file or DEFAULT_ENV_FILE
    line = f"{name}={_format_value(value)}"

    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    assignment = re.compile(rf"^\s*(export\s+)?{re.escape(name)}\s*=")
    lines = [line if assignment.match(current) else current for current in existing]
    if not any(assignment.match(current) for current in existing):
        lines.append(line)

    path.parent.mkdir(parents=True, exist_ok=True)
    # escrita atomica: um cron interrompido no meio nao pode deixar o .env truncado
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)

    if name in os.environ and os.environ[name] != value:
        # variavel exportada no ambiente vence o .env na proxima leitura
        logger.warning(
            "%s tambem esta definido no ambiente: atualize-o (ou remova) para o novo valor valer",
            name,
        )
    return path
