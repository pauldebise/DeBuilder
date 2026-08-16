#!/usr/bin/env bash
# `set -e` volontairement absent : un echec d'une iteration (OpenCode,
# git, python) ne doit jamais tuer la boucle autonome. Chaque echec est
# deja consigne par agent.py (PROGRESS.md, ITERATIONS.jsonl) et la
# boucle ne s'arrete que sur fichier DONE ou cap dur (phase 4/6).
set -uo pipefail

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
echo "[agent_loop] Modele    : ${DEBUILDER_MODEL:-}" >&2
echo "[agent_loop] DeBuilder : ${DEBUILDER_DIR}" >&2
echo "[agent_loop] Cible     : ${TARGET_DIR}" >&2

export DEBUILDER_DIR
export DEBUILDER_TARGET_DIR="${TARGET_DIR}"
export DEBUILDER_MODEL="${DEBUILDER_MODEL:-}"

ITERATION=0
while true; do
    ITERATION=$((ITERATION + 1))
    export DEBUILDER_ITERATION="${ITERATION}"
    echo "[agent_loop] ========================================" >&2
    echo "[agent_loop] Iteration #${ITERATION} - $(date)" >&2

    cd "${DEBUILDER_DIR}" || {
        echo "[agent_loop] ERREUR: repertoire DeBuilder inaccessible, arret." >&2
        exit 1
    }
    if ! ${PYTHON_BIN} -c "
import sys
sys.path.insert(0, '${DEBUILDER_DIR}')
from src.loop.agent import run_iteration
from pathlib import Path
result = run_iteration(Path('${TARGET_DIR}'), iteration_number=${ITERATION})
sys.exit(0 if result.continue_loop else 1)
"; then
        echo "[agent_loop] Arret demande (fichier DONE ou erreur)." >&2
        break
    fi

    echo "[agent_loop] Iteration #${ITERATION} terminee." >&2
    sleep 2
done

echo "[agent_loop] Boucle terminee (${ITERATION} iterations)." >&2
