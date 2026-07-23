#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_NAME="debuilder"

cleanup() {
    echo "[DeBuilder] Shutting down..."
    tmux kill-session -t "${SESSION_NAME}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

tmux has-session -t "${SESSION_NAME}" 2>/dev/null && {
    echo "[DeBuilder] Attaching to existing session '${SESSION_NAME}'..."
    tmux attach-session -t "${SESSION_NAME}"
    exit 0
}

tmux new-session -d -s "${SESSION_NAME}" -c "${SCRIPT_DIR}" \
    "python -m src.app"

echo "[DeBuilder] Started in tmux session '${SESSION_NAME}'"
echo "[DeBuilder] To attach: tmux attach-session -t ${SESSION_NAME}"
echo "[DeBuilder] To detach: Ctrl+B, D"

tmux attach-session -t "${SESSION_NAME}"
