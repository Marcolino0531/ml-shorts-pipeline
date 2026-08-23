#!/usr/bin/env bash
# Rodada de producao completa: coleta -> roteiro -> narracao -> render -> publicacao.
# Cada etapa recebe o id do produto desta coleta (`mlshorts scope` le o products-*.json mais
# recente), entao nenhum roteiro, audio ou MP4 de rodada anterior e reprocessado ou reenfileirado.
# Para na primeira falha; com PIPELINE_RUN=1 delega tudo ao `mlshorts run` (o que o timer usa).
set -Eeuo pipefail

cd "$(dirname "$0")/.."
VENV="${VENV:-./.venv/bin}"
LOG_FILE="${LOG_FILE:-data/out/execucao.log}"
MLSHORTS="$VENV/mlshorts"

if [[ "${PIPELINE_RUN:-0}" == "1" ]]; then
  exec "$MLSHORTS" run --log-file "$LOG_FILE" "$@"
fi

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_FILE"
}

mkdir -p "$(dirname "$LOG_FILE")"

log "== coleta"
"$MLSHORTS" collect "$@"

# sem coleta nesta execucao o comando falha: a mensagem abaixo explica e a rodada para
ids=$("$MLSHORTS" scope) || ids=""
if [[ -z "$ids" ]]; then
  log "coleta sem produtos novos aprovados: nada a renderizar nem publicar"
  exit 1
fi
log "escopo desta execucao: $(echo "$ids" | tr '\n' ' ')"

while read -r product_id; do
  [[ -n "$product_id" ]] || continue
  log "== roteiro $product_id"
  "$MLSHORTS" script --product-id "$product_id"
  log "== narracao $product_id"
  "$MLSHORTS" narrate --product-id "$product_id"
  log "== render $product_id"
  "$MLSHORTS" render --product-id "$product_id"
  log "== publicacao $product_id"
  "$MLSHORTS" queue-add --product-id "$product_id" --media "data/video/$product_id.mp4"
done <<<"$ids"

log "rodada concluida"
