#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_NAME="debuilder"

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c "import sys; print(sys.version_info[:2])" 2>/dev/null || true)
        if [[ "$ver" == "(3, 10)" || "$ver" == "(3, 11)" || "$ver" == "(3, 12)" || "$ver" == "(3, 13)" || "$ver" == "(3, 14)" ]]; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "${PYTHON_BIN}" ]; then
    echo "[DeBuilder] ERREUR: Aucun Python >= 3.10 trouve." >&2
    exit 1
fi

echo "[DeBuilder] Python detecte: ${PYTHON_BIN} ($(${PYTHON_BIN} --version))" >&2
export DEBUILDER_PYTHON="${PYTHON_BIN}"

DEBUILDER_PORT="${DEBUILDER_PORT:-7680}"
export DEBUILDER_PORT

if ! ${PYTHON_BIN} -c "import gradio" 2>/dev/null; then
    echo "[DeBuilder] Gradio non installe. Installation..." >&2
    ${PYTHON_BIN} -m pip install gradio --break-system-packages 2>/dev/null || \
        ${PYTHON_BIN} -m pip install gradio
fi

if ! command -v opencode &>/dev/null && [ ! -x /usr/local/bin/opencode ]; then
    echo "[DeBuilder] OpenCode non trouve. Installation..." >&2

    _install_opencode() {
        local _log="/tmp/debuilder_opencode_install.log"
        echo "=== DeBuilder install log $(date) ===" > "$_log"

        # Methode 1: script officiel (pas de dependances, binaire standalone)
        echo "[DeBuilder] Methode 1/3: script officiel..." >&2
        if curl -fsSL https://opencode.ai/install | bash >>"$_log" 2>&1; then
            return 0
        fi

        # Methode 2: npm (avec Node.js recent via NodeSource si besoin)
        echo "[DeBuilder] Methode 2/3: npm..." >&2
        if ! command -v node &>/dev/null || [ "$(node -v 2>/dev/null | sed 's/v\([0-9]*\).*/\1/')" -lt 18 ]; then
            echo "[DeBuilder]  -> installation Node.js..." >&2
            if command -v apt-get &>/dev/null; then
                curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >>"$_log" 2>&1 || true
                apt-get update -qq >>"$_log" 2>&1 || true
                apt-get install -y -qq nodejs >>"$_log" 2>&1 || true
            elif command -v apk &>/dev/null; then
                apk add --no-cache nodejs npm >>"$_log" 2>&1 || true
            fi
        fi
        if command -v npm &>/dev/null; then
            npm install -g opencode-ai@latest >>"$_log" 2>&1 || true
            if [ -x /usr/local/bin/opencode ]; then
                return 0
            fi
        fi

        # Methode 3: npx direct (si npm dispo)
        echo "[DeBuilder] Methode 3/3: npx..." >&2
        if command -v npx &>/dev/null; then
            npx --yes opencode-ai@latest --version >>"$_log" 2>&1 || true
        fi

        return 1
    }

    _install_opencode || true

    # Verification post-install
    if [ -x /usr/local/bin/opencode ]; then
        export PATH="/usr/local/bin:$PATH"
        echo "[DeBuilder] OpenCode trouve dans /usr/local/bin." >&2
    elif [ -x "$HOME/.opencode/bin/opencode" ]; then
        export PATH="$HOME/.opencode/bin:$PATH"
    fi

    if command -v opencode &>/dev/null; then
        _ver=$(opencode --version 2>/dev/null || echo "?")
        echo "[DeBuilder] OpenCode installe: ${_ver}" >&2
    else
        echo "[DeBuilder] OpenCode non installe." >&2
        echo "[DeBuilder] Log: /tmp/debuilder_opencode_install.log" >&2
    fi
fi

if tmux has-session -t "${SESSION_NAME}" 2>/dev/null; then
    echo "[DeBuilder] La session '${SESSION_NAME}' tourne deja." >&2
    echo "[DeBuilder] Pour attacher: tmux attach-session -t ${SESSION_NAME}" >&2
    exit 0
fi

tmux new-session -d -s "${SESSION_NAME}" -c "${SCRIPT_DIR}" \
    "${PYTHON_BIN} -m src.app"

echo "[DeBuilder] Demarre en arriere-plan (port ${DEBUILDER_PORT})." >&2
echo "[DeBuilder] Pour attacher:  tmux attach-session -t ${SESSION_NAME}" >&2
echo "[DeBuilder] Pour arreter:   tmux kill-session -t ${SESSION_NAME}" >&2
