#!/usr/bin/env bash
# `set -e` volontairement absent : un echec d'une iteration (OpenCode,
# git, python) ne doit jamais tuer la boucle autonome. Chaque echec est
# deja consigne par agent.py (PROGRESS.md, ITERATIONS.jsonl) et la
# boucle ne s'arrete que sur fichier DONE ou cap dur.
#
# Contrat de sortie de la fonction run_iteration (Python) :
#   0  : iteration terminee avec succes
#   10 : arret demande (fichier DONE)
#   11 : iteration terminee en echec (backoff avant la suivante)
#   autre : crash Python inattendu -> traite comme un echec, la boucle
#           continue (backoff) au lieu de mourir.
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

MAX_ITERATIONS="${DEBUILDER_MAX_ITERATIONS:-0}"

# Sortie propre : commit d'urgence de l'etat. Si la boucle est tuee
# (SIGTERM du pod, cap dur, DONE), la derniere mise a jour de
# PROGRESS.md ne doit pas etre perdue en memoire vive uniquement.
_emergency_commit() {
    echo "[agent_loop] Commit d'urgence de l'etat..." >&2
    ${PYTHON_BIN} -c "
import sys
sys.path.insert(0, '${DEBUILDER_DIR}')
from pathlib import Path
from src.core.git import stage_and_commit_all
stage_and_commit_all(Path('${TARGET_DIR}'), 'chore: commit d urgence - sortie de la boucle')
" >/dev/null 2>&1 || true
}
trap _emergency_commit EXIT

SLEEP=2
ITERATION=0
while true; do
    if [ "${MAX_ITERATIONS}" -gt 0 ] && [ "${ITERATION}" -ge "${MAX_ITERATIONS}" ]; then
        echo "[agent_loop] Cap dur atteint (${MAX_ITERATIONS} iterations), arret." >&2
        break
    fi

    ITERATION=$((ITERATION + 1))
    export DEBUILDER_ITERATION="${ITERATION}"
    echo "[agent_loop] ========================================" >&2
    echo "[agent_loop] Iteration #${ITERATION} - $(date)" >&2

    cd "${DEBUILDER_DIR}" || {
        echo "[agent_loop] ERREUR: repertoire DeBuilder inaccessible, arret." >&2
        exit 1
    }

    ${PYTHON_BIN} -c "
import sys
sys.path.insert(0, '${DEBUILDER_DIR}')
from src.loop.agent import run_iteration
from pathlib import Path
result = run_iteration(Path('${TARGET_DIR}'), iteration_number=${ITERATION})
if not result.continue_loop:
    sys.exit(10)
sys.exit(11 if result.failure_type else 0)
"
    ITER_STATUS=$?

    if [ "${ITER_STATUS}" -eq 10 ]; then
        echo "[agent_loop] Arret demande (fichier DONE)." >&2
        break
    fi

    if [ "${ITER_STATUS}" -eq 0 ]; then
        SLEEP=2
        echo "[agent_loop] Iteration #${ITERATION} terminee." >&2
    else
        # Backoff exponentiel plafonne entre iterations en echec
        # (fonction pure Python, source de verite unique) : evite de
        # marteler une API morte et laisse le temps au provider de
        # se retablir. Un crash Python inattendu (code different de
        # 10/11) suit le meme chemin : la boucle ne meurt pas.
        SLEEP=$(${PYTHON_BIN} -c "
import sys
sys.path.insert(0, '${DEBUILDER_DIR}')
from src.loop.agent import compute_backoff
print(int(compute_backoff(${SLEEP}, True)))
" || echo 300)
        echo "[agent_loop] Iteration #${ITERATION} en echec (code ${ITER_STATUS}) : nouvelle tentative dans ${SLEEP}s." >&2
    fi

    sleep "${SLEEP}"
done

echo "[agent_loop] Boucle terminee (${ITERATION} iterations)." >&2
