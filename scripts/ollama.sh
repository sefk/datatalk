#!/usr/bin/env bash
#
# Manage a local Ollama server for Datatalk development.
#
# Usage:
#   scripts/ollama.sh start [MODEL]    Start Ollama, optionally pull a model
#   scripts/ollama.sh stop             Stop Ollama
#   scripts/ollama.sh restart [MODEL]  Restart Ollama
#   scripts/ollama.sh status           Show server status and loaded models
#
# If MODEL is omitted on start/restart, presents an fzf picker of locally
# available models (or skips if fzf is not installed).
#
# Models are stored on an external volume at /Volumes/ext1/ollama to keep
# the boot drive free. The OLLAMA_MODELS env var is set automatically.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$REPO_ROOT/.tmp/ollama"
LOG_FILE="$LOG_DIR/ollama.log"
PID_FILE="$LOG_DIR/ollama.pid"

export OLLAMA_MODELS="/Volumes/ext1/ollama"

mkdir -p "$LOG_DIR"

_is_running() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(<"$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$PID_FILE"
    fi
    # Also check if ollama is running outside our control
    if pgrep -x ollama >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

_pick_model() {
    local model="${1:-}"
    if [[ -n "$model" ]]; then
        echo "$model"
        return
    fi

    # List locally available models
    local models
    models=$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}') || true

    if [[ -z "$models" ]]; then
        return
    fi

    if command -v fzf >/dev/null 2>&1; then
        echo "$models" | fzf --prompt="Select model to load> " --height=10 --reverse || true
    else
        echo ""
    fi
}

_wait_for_server() {
    local attempts=0
    while ! curl -sf http://localhost:11434/api/version >/dev/null 2>&1; do
        attempts=$((attempts + 1))
        if [[ $attempts -ge 30 ]]; then
            echo "Error: Ollama server did not start within 30 seconds." >&2
            echo "Check logs: $LOG_FILE" >&2
            exit 1
        fi
        sleep 1
    done
}

cmd_start() {
    local requested_model="${1:-}"

    if _is_running; then
        echo "Ollama is already running."
    else
        echo "Starting Ollama (logging to $LOG_FILE)..."
        ollama serve >> "$LOG_FILE" 2>&1 &
        local pid=$!
        echo "$pid" > "$PID_FILE"
        _wait_for_server
        echo "Ollama started (PID $pid)."
    fi

    # Model selection
    local model
    model=$(_pick_model "$requested_model")
    if [[ -n "$model" ]]; then
        if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$model"; then
            echo "Model $model is already available locally."
        else
            echo "Pulling model $model..."
            ollama pull "$model"
        fi
        echo ""
        echo "To use this model, set in .env:"
        echo "  DATATALK_ENGINE=$model"
    fi
}

cmd_stop() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(<"$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping Ollama (PID $pid)..."
            kill "$pid"
            rm -f "$PID_FILE"
            echo "Stopped."
            return
        fi
        rm -f "$PID_FILE"
    fi

    # Try pkill as fallback
    if pgrep -x ollama >/dev/null 2>&1; then
        echo "Stopping Ollama..."
        pkill -x ollama
        echo "Stopped."
    else
        echo "Ollama is not running."
    fi
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start "$@"
}

cmd_status() {
    if _is_running; then
        local version
        version=$(curl -sf http://localhost:11434/api/version 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','unknown'))" 2>/dev/null || echo "unknown")
        echo "Ollama is running (version $version)."

        # Show configured model from .env or environment
        local engine="${DATATALK_ENGINE:-}"
        if [[ -z "$engine" && -f "$REPO_ROOT/.env" ]]; then
            engine=$(grep -E '^DATATALK_ENGINE=' "$REPO_ROOT/.env" 2>/dev/null | cut -d= -f2- || true)
        fi
        if [[ -n "$engine" ]]; then
            echo "Active model: $engine"
        fi
        echo ""
        echo "Local models:"
        ollama list 2>/dev/null || echo "  (could not list models)"
    else
        echo "Ollama is not running."
    fi
}

usage() {
    echo "Usage: $(basename "$0") {start|stop|restart|status} [MODEL]"
    echo ""
    echo "Commands:"
    echo "  start [MODEL]    Start Ollama server, optionally pull MODEL"
    echo "  stop             Stop Ollama server"
    echo "  restart [MODEL]  Restart Ollama server"
    echo "  status           Show server status and models"
    echo ""
    echo "If MODEL is omitted on start/restart and fzf is installed,"
    echo "an interactive picker of local models is shown."
    exit 1
}

case "${1:-}" in
    start)   shift; cmd_start "$@" ;;
    stop)    cmd_stop ;;
    restart) shift; cmd_restart "$@" ;;
    status)  cmd_status ;;
    *)       usage ;;
esac
