#!/usr/bin/env bash
# Rodada de producao: coleta -> roteiro -> narracao -> render.
# A publicacao NAO entra aqui: quem decide o ritmo e `mlshorts publish --process-queue`.
set -euo pipefail

cd "$(dirname "$0")/.."
VENV="${VENV:-./.venv/bin}"
NICHE="${NICHE:-}"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

step "Coleta"
"$VENV/mlshorts" collect

step "Roteiro"
"$VENV/mlshorts" script

step "Narracao"
"$VENV/mlshorts" narrate

step "Render 1080x1920"
"$VENV/mlshorts" render

# Enfileira os videos renderizados nesta rodada; o intervalo minimo por nicho e a
# aprovacao no dashboard continuam valendo dentro do `queue-add`.
if [[ -n "$NICHE" ]]; then
  step "Enfileirando em $NICHE"
  for video in data/video/*.mp4; do
    [[ -e "$video" ]] || continue
    product_id="$(basename "$video" .mp4)"
    "$VENV/mlshorts" queue-add --product-id "$product_id" --niche "$NICHE" --media "$video"
  done
else
  echo
  echo "NICHE nao definido: os videos ficaram em data/video/ sem entrar na fila."
  echo "Use: NICHE=Celulares $0"
fi
