#!/bin/bash
# land-registry — start script
# Registry: /data/aecs4u.it/apps.json

set -e
umask 077

APP_NAME="Land Registry"
MODULE="land_registry.main:app"
DEFAULT_PORT=8011              # must match /data/aecs4u.it/apps.json
DEFAULT_HOST="0.0.0.0"

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Options:"
    echo "  -e, --env ENV     Environment: dev, test, staging, production (default: dev)"
    echo "  -p, --port PORT   Port to listen on  (default: $DEFAULT_PORT)"
    echo "  -H, --host HOST   Host to bind to    (default: $DEFAULT_HOST)"
    echo "  --no-reload       Disable auto-reload"
    echo "  --help            Show this help"
}

fail() {
    echo -e "${RED}$1${NC}" >&2
    usage >&2
    exit 2
}

normalize_environment() {
    case "${1,,}" in
        dev|development) echo "development" ;;
        test|testing) echo "testing" ;;
        stage|staging) echo "staging" ;;
        prod|production) echo "production" ;;
        *) fail "Unknown environment: $1" ;;
    esac
}

ENVIRONMENT="${ENVIRONMENT:-dev}"
HOST=""
PORT=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -e|--env|--environment)
            [[ $# -ge 2 && -n "$2" ]] || fail "Missing value for $1"
            ENVIRONMENT="$2"; shift 2 ;;
        --env=*|--environment=*) ENVIRONMENT="${1#*=}"; shift ;;
        -p|--port)
            [[ $# -ge 2 && -n "$2" ]] || fail "Missing value for $1"
            PORT="$2"; shift 2 ;;
        -H|--host)
            [[ $# -ge 2 && -n "$2" ]] || fail "Missing value for $1"
            HOST="$2"; shift 2 ;;
        --no-reload) export LAND_REGISTRY_DISABLE_RELOAD=true; shift ;;
        --help) usage; exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
done

ENVIRONMENT="$(normalize_environment "$ENVIRONMENT")"
export ENVIRONMENT
HOST="${HOST:-$DEFAULT_HOST}"
PORT="${PORT:-$DEFAULT_PORT}"

cd "$(dirname "$0")/.."

[ ! -f .env ] && [ -f .env.example ] && {
    echo -e "${YELLOW}Creating .env from .env.example — edit it before restarting if needed${NC}"
    cp .env.example .env
    chmod 600 .env
}

export PYTHONPATH="$(pwd):${PYTHONPATH:-}"

echo -e "${BLUE}Starting ${APP_NAME} (${ENVIRONMENT}) → http://${HOST}:${PORT}${NC}"

# Keep direct Python startup for Ctrl+C handling with and without reload.
PYTHON_BIN="$(pwd)/.venv/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="python"
exec "$PYTHON_BIN" -c "
import os, signal, sys, uvicorn
from land_registry.config import app_settings
signal.signal(signal.SIGINT, lambda *_: (print('\\nShutting down...'), sys.exit(0)))
reload_disabled = os.getenv('LAND_REGISTRY_DISABLE_RELOAD', '').lower() in {'1', 'true', 'yes', 'on'}
reload_enabled = app_settings.reload and not reload_disabled
uvicorn.run('$MODULE', host='$HOST', port=int('$PORT'),
            reload=reload_enabled, reload_delay=0.25, timeout_graceful_shutdown=3,
            timeout_keep_alive=2, log_level=os.getenv('LOG_LEVEL', 'INFO').lower())
"
