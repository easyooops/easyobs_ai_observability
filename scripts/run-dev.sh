#!/usr/bin/env bash
# EasyObs: API + Next dev server in one terminal (Linux / macOS)
# Usage:  chmod +x scripts/run-dev.sh && ./scripts/run-dev.sh
#         ./scripts/run-dev.sh --skip-install
set -euo pipefail

API_PORT="${API_PORT:-8787}"
WEB_PORT="${WEB_PORT:-3000}"
SKIP_INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --skip-install) SKIP_INSTALL=1 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env && -f .env.sample ]]; then
  cp .env.sample .env
  echo "[easyobs] Created .env from .env.sample"
fi

if [[ "$SKIP_INSTALL" -eq 0 ]]; then
  if command -v uv >/dev/null 2>&1; then
    echo "[easyobs] uv sync --extra agent"
    uv sync --extra agent
  else
    if [[ ! -d .venv ]]; then
      echo "[easyobs] python3 -m venv .venv"
      python3 -m venv .venv
    fi
    echo "[easyobs] pip install -e '.[agent]'"
    .venv/bin/pip install -e ".[agent]"
  fi
fi

PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "No .venv/bin/python. Run without --skip-install once, or install uv." >&2
  exit 1
fi

export EASYOBS_API_PORT="$API_PORT"
export NEXT_PUBLIC_API_URL="http://127.0.0.1:${API_PORT}"
# Force local source tree precedence over any stale editable/site-packages.
export PYTHONPATH="$ROOT/src"

cleanup() {
  if [[ -n "${API_PID:-}" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
    echo "[easyobs] API process stopped."
  fi
}
trap cleanup EXIT INT TERM

echo "[easyobs] Starting API on http://127.0.0.1:${API_PORT} ..."
"$PY" -m uvicorn easyobs.http_app:create_app --factory --host 127.0.0.1 --port "$API_PORT" \
  --reload --reload-dir "$ROOT/src" &
API_PID=$!

ready=0
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${API_PORT}/healthz" >/dev/null; then
    ready=1
    break
  fi
  sleep 0.4
done
if [[ "$ready" -ne 1 ]]; then
  echo "API did not become ready on http://127.0.0.1:${API_PORT}/healthz" >&2
  exit 1
fi
echo "[easyobs] API OK — docs: http://127.0.0.1:${API_PORT}/docs"

WEB_DIR="$ROOT/apps/web"
if [[ ! -d "$WEB_DIR/node_modules" ]]; then
  echo "[easyobs] npm install (apps/web)"
  (cd "$WEB_DIR" && npm install)
fi
if [[ ! -f "$WEB_DIR/.env.local" && -f "$WEB_DIR/.env.sample" ]]; then
  cp "$WEB_DIR/.env.sample" "$WEB_DIR/.env.local"
  echo "[easyobs] Created apps/web/.env.local from .env.sample"
fi

echo "[easyobs] UI — http://localhost:${WEB_PORT} (Ctrl+C stops API and exits)"
cd "$WEB_DIR"
npm run dev -- -p "$WEB_PORT"
