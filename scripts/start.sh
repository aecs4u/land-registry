#!/bin/bash
# Lighthouse - Start script

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Parse command line arguments
CUSTOM_PORT=""
CUSTOM_HOST=""

usage() {
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  -p, --port PORT    Specify the port to run the server on (default: 8001)"
    echo "  -h, --host HOST    Specify the host to bind to (default: 0.0.0.0)"
    echo "  --help             Display this help message"
    echo ""
    exit 0
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--port)
            CUSTOM_PORT="$2"
            shift 2
            ;;
        -h|--host)
            CUSTOM_HOST="$2"
            shift 2
            ;;
        --help)
            usage
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            usage
            ;;
    esac
done

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║         Lighthouse Control Panel       ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  No .env file found. Creating from .env.example...${NC}"
    if [ -f .env.example ]; then
        cp .env.example .env
        echo -e "${GREEN}✓ Created .env file${NC}"
        echo -e "${YELLOW}⚠️  Please edit .env with your configuration before starting${NC}"
        exit 0
    else
        echo -e "${RED}✗ .env.example not found${NC}"
        exit 1
    fi
fi

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}⚠️  No virtual environment found. Creating...${NC}"
    uv venv .venv
    echo -e "${GREEN}✓ Created virtual environment${NC}"
fi

# Activate virtual environment
echo -e "${BLUE}Activating virtual environment...${NC}"
source .venv/bin/activate

# Check if dependencies are installed
if [ -f "requirements.txt" ]; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    uv pip install -r requirements.txt
    echo -e "${GREEN}✓ Dependencies installed${NC}"
fi

# Get host and port from command line, then .env, or use defaults
if [ -n "$CUSTOM_HOST" ]; then
    HOST="$CUSTOM_HOST"
else
    HOST=${HOST:-0.0.0.0}
fi

if [ -n "$CUSTOM_PORT" ]; then
    PORT="$CUSTOM_PORT"
else
    PORT=${PORT:-8001}
fi

echo ""
echo -e "${GREEN}Starting Lighthouse...${NC}"
echo -e "${BLUE}Host: ${HOST}${NC}"
echo -e "${BLUE}Port: ${PORT}${NC}"
echo ""

# Ensure sitecustomize can be discovered for runtime patches
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"

clear

# Start the application via Python for proper Ctrl+C signal handling
# (uv run + uvicorn --reload creates nested processes that swallow SIGINT)
exec python -c "
import signal, sys, uvicorn
signal.signal(signal.SIGINT, lambda *_: (print('\nShutting down...'), sys.exit(0)))
uvicorn.run('land_registry.main:app', host='$HOST', port=int('$PORT'),
            reload=True, reload_delay=0.25, timeout_graceful_shutdown=3,
            timeout_keep_alive=2, log_level='info')
"
