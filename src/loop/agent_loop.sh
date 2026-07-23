#!/usr/bin/env bash
set -euo pipefail

AGENT_LOOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEBUILDER_DIR="$(dirname "$(dirname "${AGENT_LOOP_DIR}")")"

echo "[agent_loop] DeBuilder Agent Loop started" >&2
echo "[agent_loop] DeBuilder root: ${DEBUILDER_DIR}" >&2

# Boucle principale
while true; do
    echo "[agent_loop] Starting iteration..." >&2

    # TODO: Implementer l'iteration OpenCode:
    # 1. Lire AGENTS.md, PROGRESS.md, SUGGESTIONS.md, RESOURCES_NEEDED.md
    # 2. Verrouiller les fichiers d'etat
    # 3. Executer OpenCode avec le prompt construit
    # 4. Mettre a jour PROGRESS.md, BENCHMARKS.md
    # 5. Commit et push sur le depot cible
    # 6. Verifier DONE -> sortie propre

    # Simulation temporaire
    sleep 10
done
