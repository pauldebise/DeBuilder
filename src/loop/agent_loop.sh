#!/usr/bin/env bash
set -euo pipefail

DEBUILDER_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)"
TARGET_DIR="${DEBUILDER_TARGET_DIR:-}"
PYTHON_BIN="${DEBUILDER_PYTHON:-python3}"

if [ -z "${TARGET_DIR}" ]; then
    echo "[agent_loop] ERREUR: DEBUILDER_TARGET_DIR non definie." >&2
    exit 1
fi

if [ ! -d "${TARGET_DIR}" ]; then
    echo "[agent_loop] ERREUR: Le repertoire cible ${TARGET_DIR} n'existe pas." >&2
    exit 1
fi

echo "[agent_loop] Demarrage de la boucle agent" >&2
echo "[agent_loop] Python    : ${PYTHON_BIN}" >&2
echo "[agent_loop] DeBuilder : ${DEBUILDER_DIR}" >&2
echo "[agent_loop] Cible     : ${TARGET_DIR}" >&2

export DEBUILDER_DIR
export DEBUILDER_TARGET_DIR="${TARGET_DIR}"

ITERATION=0
while true; do
    ITERATION=$((ITERATION + 1))
    echo "[agent_loop] ========================================" >&2
    echo "[agent_loop] Iteration #${ITERATION} - $(date)" >&2

    cd "${DEBUILDER_DIR}"
    if ! ${PYTHON_BIN} -c "
import sys
sys.path.insert(0, '${DEBUILDER_DIR}')
from src.loop.agent import run_iteration
from pathlib import Path
cont = run_iteration(Path('${TARGET_DIR}'))
sys.exit(0 if cont else 1)
"; then
        echo "[agent_loop] Arret demande (fichier DONE ou erreur)." >&2
        break
    fi

    echo "[agent_loop] Iteration #${ITERATION} terminee." >&2
    sleep 2
done

echo "[agent_loop] Boucle terminee (${ITERATION} iterations)." >&2
