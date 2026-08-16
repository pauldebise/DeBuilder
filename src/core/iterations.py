"""Journal machine-readable des iterations (ITERATIONS.jsonl).

Une ligne JSON par iteration, append-only, protegee par le mecanisme
de verrouillage (filelock) : le journal est ecrit par la boucle et lu
par le tableau de bord, jamais reecrit.

Squelette minimal (phase 0 du plan pipeline) : horodatage, numero
d'iteration, code de sortie, type d'echec, duree, diff, no-op. Les
champs modele/session/tests seront enrichis en phase 8.

Le fichier vit dans le repertoire du projet cible (comme
OPENCODE_LOG.txt) et est exclu du suivi Git via
``_DEBUILDER_IGNORE_PATTERNS`` dans ``src/core/git.py``.
"""

import json
import time
from pathlib import Path

from src.core.filelock import file_lock

JOURNAL_FILENAME = "ITERATIONS.jsonl"


def journal_path(target_dir: Path) -> Path:
    """Chemin du journal d'iterations pour un projet cible."""
    return target_dir / JOURNAL_FILENAME


def append_entry(target_dir: Path, entry: dict) -> None:
    """Ajoute une ligne JSON au journal, sous verrou.

    Args:
        target_dir: Repertoire du projet cible.
        entry: Dictionnaire de la ligne (l'horodatage est ajoute ici
            s'il manque).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    if "timestamp" not in entry:
        entry["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    path = journal_path(target_dir)
    with file_lock(path):
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_entries(target_dir: Path, limit: int | None = None) -> list[dict]:
    """Lit les entrees du journal, de la plus ancienne a la plus recente.

    Args:
        target_dir: Repertoire du projet cible.
        limit: Nombre maximum d'entrees retournees (les plus recentes
            si borne).

    Returns:
        Liste de dictionnaires ; les lignes corrompues sont ignorees.
    """
    path = journal_path(target_dir)
    with file_lock(path):
        if not path.exists():
            return []
        content = path.read_text(encoding="utf-8")

    entries: list[dict] = []
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    if limit is not None:
        return entries[-limit:]
    return entries
