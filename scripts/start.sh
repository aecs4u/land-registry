#!/bin/bash
# land-registry — start script
# Registry: /data/aecs4u.it/apps.json

set -e

# ── App configuration ──────────────────────────────────────────────────────────
APP_NAME="Land Registry"
MODULE="land_registry.main:app"
DEFAULT_PORT=8011              # must match /data/aecs4u.it/apps.json
DEFAULT_HOST="0.0.0.0"
# ──────────────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Options:"
    echo "  -p, --port PORT   Port to listen on  (default: $DEFAULT_PORT)"
    echo "  -H, --host HOST   Host to bind to    (default: $DEFAULT_HOST)"
    echo "  --no-reload       Disable auto-reload"
    echo "  --help            Show this help"
    exit 0
}

RELOAD=true
HOST=""
PORT=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--port)   PORT="$2"; shift 2 ;;
        -H|--host)   HOST="$2"; shift 2 ;;
        --no-reload) RELOAD=false; shift ;;
        --help)      usage ;;
        *) echo -e "${RED}Unknown option: $1${NC}"; usage ;;
    esac
done

HOST="${HOST:-$DEFAULT_HOST}"
PORT="${PORT:-$DEFAULT_PORT}"

cd "$(dirname "$0")/.."

[ ! -f .env ] && [ -f .env.example ] && {
    echo -e "${YELLOW}Creating .env from .env.example — edit it before restarting if needed${NC}"
    cp .env.example .env
}

export PYTHONPATH="$(pwd):${PYTHONPATH:-}"

echo -e "${BLUE}Starting ${APP_NAME} → http://${HOST}:${PORT}${NC}"

# Use python runner directly for proper Ctrl+C signal handling
# (uv run + uvicorn --reload creates nested processes that swallow SIGINT)
exec python -c "
import signal, sys, uvicorn
signal.signal(signal.SIGINT, lambda *_: (print('\nShutting down...'), sys.exit(0)))
uvicorn.run('$MODULE', host='$HOST', port=int('$PORT'),
            reload=$RELOAD, reload_delay=0.25, timeout_graceful_shutdown=3,
            timeout_keep_alive=2, log_level='info')
"
