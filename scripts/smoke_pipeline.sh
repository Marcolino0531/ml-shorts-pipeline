#!/usr/bin/env bash
# Atalho do health check: valida ambiente + fluxo completo em modo simulado.
#   ./scripts/smoke_pipeline.sh          # artefatos em data/smoke/ (apagados no fim)
#   ./scripts/smoke_pipeline.sh --keep
set -euo pipefail

cd "$(dirname "$0")/.."
VENV="${VENV:-./.venv/bin}"

echo "== ambiente"
"$VENV/python" --version
ffmpeg -version | head -1
"$VENV/mlshorts" --help >/dev/null && echo "CLI mlshorts ok"

echo "== fluxo completo (collect -> script -> tts -> render -> publish simulado)"
exec "$VENV/python" scripts/smoke_pipeline.py "$@"
