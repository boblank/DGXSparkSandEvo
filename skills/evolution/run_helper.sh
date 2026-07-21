#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSHOP_DIR="${WORKSHOP_DIR:-/home/Developer/build_a_claw_workshop-bundle}"
APP="$WORKSHOP_DIR/comfyui-app"
VENV="$APP/comfyui-env"

if [ ! -f "$VENV/bin/activate" ]; then
  echo "ERROR: ComfyUI environment not found at $VENV" >&2
  exit 1
fi

cd "$APP"
# shellcheck source=/dev/null
source "$VENV/bin/activate"

export WORKSHOP_DIR
export OPENCLAW_HOME="${OPENCLAW_HOME:-$WORKSHOP_DIR/openclaw-home}"
export COMFYUI_URL="${COMFYUI_URL:-http://127.0.0.1:${COMFYUI_PORT:-7000}}"
export OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
export HF_HOME="${HF_HOME:-$WORKSHOP_DIR/hf-cache}"

exec python3 "$SCRIPT_DIR/evolution_helper.py" "$@"
