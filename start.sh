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

if ! command -v opencode &>/dev/null; then
    echo "[DeBuilder] OpenCode non trouve. Installation en cours..." >&2

    _install_opencode() {
        local _log="/tmp/debuilder_opencode_install.log"

        # Methode 1: pip
        if ${PYTHON_BIN} -m pip install opencode --break-system-packages >>"$_log" 2>&1; then
            return 0
        fi

        # Methode 2: npm (avec installation de Node.js si necessaire)
        if ! command -v npm &>/dev/null && ! command -v node &>/dev/null; then
            echo "[DeBuilder] Installation de Node.js..." >&2
            if command -v apt-get &>/dev/null; then
                apt-get update -qq >>"$_log" 2>&1 && apt-get install -y -qq nodejs npm >>"$_log" 2>&1 || true
            elif command -v apk &>/dev/null; then
                apk add --no-cache nodejs npm >>"$_log" 2>&1 || true
            elif command -v yum &>/dev/null; then
                yum install -y nodejs npm >>"$_log" 2>&1 || true
            elif command -v brew &>/dev/null; then
                brew install node >>"$_log" 2>&1 || true
            fi
        fi

        if command -v npm &>/dev/null; then
            npm install -g opencode >>"$_log" 2>&1 && return 0
        fi

        # Methode 3: curl + binaire
        local os_arch
        os_arch="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
        local bin_url="https://github.com/anomalyco/opencode/releases/latest/download/opencode-${os_arch}"
        local bin_path="/usr/local/bin/opencode"
        if curl -fsSL "$bin_url" -o "$bin_path" 2>>"$_log"; then
            chmod +x "$bin_path"
            return 0
        fi

        return 1
    }

    _install_opencode || true
    if command -v opencode &>/dev/null; then
        echo "[DeBuilder] OpenCode installe avec succes." >&2
    else
        echo "[DeBuilder] ATTENTION: OpenCode n'a pas pu etre installe." >&2
        echo "[DeBuilder] Details: /tmp/debuilder_opencode_install.log" >&2
        echo "[DeBuilder] Installez-le manuellement: npm install -g opencode" >&2
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
