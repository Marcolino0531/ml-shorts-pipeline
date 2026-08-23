#!/usr/bin/env bash
# Rodada de producao completa: coleta -> roteiro -> narracao -> render -> publicacao.
# Mesmo caminho que o timer usa (`mlshorts run`): para na primeira falha e so publica o
# video renderizado nesta execucao, nunca sobra de uma rodada anterior.
set -Eeuo pipefail

cd "$(dirname "$0")/.."
VENV="${VENV:-./.venv/bin}"
LOG_FILE="${LOG_FILE:-data/out/execucao.log}"

exec "$VENV/mlshorts" run --log-file "$LOG_FILE" "$@"
