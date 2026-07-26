#!/usr/bin/env bash
# Provisiona o pipeline em uma VPS Ubuntu (testado no 22.04/24.04 da Hetzner).
#
#   curl -fsSL https://raw.githubusercontent.com/Marcolino0531/ml-shorts-pipeline/main/deploy/setup_linux.sh | bash
#   ou: sudo -v && ./deploy/setup_linux.sh
#
# Idempotente: pode rodar de novo para atualizar o repositorio e as dependencias.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Marcolino0531/ml-shorts-pipeline.git}"
APP_DIR="${APP_DIR:-$HOME/ml-shorts-pipeline}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
# instala o Chromium do Playwright (só necessário para o coletor de fallback)
WITH_PLAYWRIGHT="${WITH_PLAYWRIGHT:-1}"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

if [[ $EUID -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

log "Pacotes de sistema (Python, FFmpeg, git)"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -y
$SUDO apt-get install -y --no-install-recommends \
  ca-certificates curl git tzdata \
  python3 python3-venv python3-pip \
  ffmpeg fonts-dejavu-core

# as legendas .ass sao renderizadas com a DejaVu Sans; sem a fonte o texto sai vazio
log "Versoes instaladas"
"$PYTHON_BIN" --version
ffmpeg -version | head -1
ffprobe -version | head -1

log "Repositorio em $APP_DIR"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

log "Ambiente virtual e dependencias"
[[ -d .venv ]] || "$PYTHON_BIN" -m venv .venv
./.venv/bin/pip install --upgrade pip wheel
./.venv/bin/pip install -e ".[dev]"

if [[ "$WITH_PLAYWRIGHT" == "1" ]]; then
  log "Chromium do Playwright (coletor de fallback)"
  ./.venv/bin/playwright install --with-deps chromium
fi

log "Arquivos de dados e .env"
mkdir -p data/{raw,images,audio,video,out}
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "Criado .env a partir do .env.example - preencha as credenciais antes de rodar."
fi

log "Verificacao rapida"
./.venv/bin/mlshorts --help >/dev/null && echo "CLI ok"
./.venv/bin/python -m pytest -q

cat <<EOF

Pronto. Proximos passos:

  1. nano $APP_DIR/.env                       # preencha as credenciais (veja o README)
  2. $APP_DIR/scripts/smoke_pipeline.sh       # valida o fluxo completo em modo simulado
  3. Agendamento (escolha um):
       crontab -e   # e cole o conteudo de deploy/crontab.example
       $SUDO cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
       $SUDO systemctl daemon-reload
       $SUDO systemctl enable --now mlshorts-collect.timer mlshorts-publish.timer
  4. Dashboard:
       $SUDO cp deploy/systemd/mlshorts-dashboard.service /etc/systemd/system/
       $SUDO systemctl enable --now mlshorts-dashboard
EOF
