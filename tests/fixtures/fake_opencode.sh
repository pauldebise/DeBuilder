#!/usr/bin/env bash
# Faux `opencode` pour les tests bout-en-bout du pipeline DeBuilder.
#
# Pilote par scenario via FAKE_SCENARIO. Le type de session (Plan /
# Implement / Review) est reconnu par les marqueurs presents dans le
# prompt (fichier passe par --file), pas par l'ordre des appels.
#
# Scenarios :
#   full    : iteration 1 -> creation de main.py ; iteration 2 ->
#             declaration de fin de mission (FINISHED_REPORT.md coche)
#             et verdict de review ACCEPTE. La boucle cree DONE.
#   timeout : bloque sans produire de sortie (watchdog => timeout).
#   api     : echoue avec une erreur de cle API (circuit breaker).
#   noop    : coche les cases et met a jour PROGRESS.md sans jamais
#             produire de code (iterations no-op).
set -u

PROMPT_FILE=""
TARGET_DIR="${PWD:-.}"
while [ $# -gt 0 ]; do
    case "$1" in
        --file)
            PROMPT_FILE="$2"
            shift 2
            ;;
        --dir)
            TARGET_DIR="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

PROMPT="$(cat "${PROMPT_FILE}" 2>/dev/null || true)"
SCENARIO="${FAKE_SCENARIO:-full}"

is_plan() {
    case "${PROMPT}" in
        *"Etat des Gates de l'Iteration Precedente"*) return 0 ;;
        *) return 1 ;;
    esac
}

is_review() {
    case "${PROMPT}" in
        *"Rapport de Fin de Mission (FINISHED_REPORT.md)"*) return 0 ;;
        *) return 1 ;;
    esac
}

check_boxes() {
    sed -i 's/- \[ \]/- [x]/g' "${TARGET_DIR}/TASK.md"
}

append_progress() {
    cat >> "${TARGET_DIR}/PROGRESS.md" <<'EOF'
- **Action realisee** : travail de l'iteration (session Implement simulee)
- **Resultat** : OK
- **Problemes rencontres** : Aucun
- **Solutions envisagees** : Suite au prochain contrat
EOF
}

case "${SCENARIO}" in
    timeout)
        # Aucune sortie, aucune fin : le watchdog de la boucle tue ce
        # processus (typologie d'echec "timeout").
        sleep 30
        exit 0
        ;;
    api)
        echo "invalid_api_key: cle API invalide (simulee)" >&2
        exit 1
        ;;
esac

if is_plan; then
    if [ -f "${TARGET_DIR}/main.py" ]; then
        cat <<'EOF'
Voici le contrat de tache.

```TASK
# Tache de l'Iteration

## Objectif

Declarer la fin de mission.

## Criteres d'Acceptation

- [ ] checklist de FINISHED_REPORT.md completee et cochee

## Commande de Test

true

## Sous-Taches

- [ ] completer FINISHED_REPORT.md et cocher toutes les cases
```

```PLAN
# Plan de Developpement

## Backlog (par priorite)

## Terminees

- [x] creer main.py
```

```SPEC
# Couverture du Cahier des Charges

| Item du cahier des charges | Implemente dans | Test associe |
|---|---|---|
| main.py | main.py | true |
```
EOF
    else
        cat <<'EOF'
Voici le contrat de tache.

```TASK
# Tache de l'Iteration

## Objectif

Creer main.py.

## Criteres d'Acceptation

- [ ] main.py existe

## Commande de Test

true

## Sous-Taches

- [ ] creer main.py
```

```PLAN
# Plan de Developpement

## Backlog (par priorite)

- [ ] declarer la fin de mission

## Terminees
```

```SPEC
# Couverture du Cahier des Charges

| Item du cahier des charges | Implemente dans | Test associe |
|---|---|---|
| main.py | main.py | true |
```
EOF
    fi
    exit 0
fi

if is_review; then
    echo '```VERDICT'
    echo 'ACCEPTE'
    echo '```'
    exit 0
fi

# Session Implement.
if [ "${SCENARIO}" = "noop" ]; then
    # Aucun code produit : seuls les fichiers d'etat changent.
    check_boxes
    append_progress
    echo "travail simule : aucun code produit (scenario noop)"
    exit 0
fi

if [ ! -f "${TARGET_DIR}/main.py" ]; then
    cat > "${TARGET_DIR}/main.py" <<'EOF'
def main():
    print("hello from debuilder e2e")


if __name__ == "__main__":
    main()
EOF
else
    # Deuxieme iteration : la mission est declaree finie.
    cat > "${TARGET_DIR}/FINISHED_REPORT.md" <<'EOF'
# Rapport de Fin de Mission

## Checklist du Cahier des Charges

- [x] main.py implemente et teste

## Validation par les Tests

true : OK
EOF
fi

check_boxes
append_progress
echo "travail implemente (scenario ${SCENARIO})"
exit 0
