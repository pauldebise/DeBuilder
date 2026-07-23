"""Gestion des fichiers d'etat du projet cible.

Les fichiers d'etat (AGENTS.md, PROGRESS.md, BENCHMARKS.md,
SUGGESTIONS.md, RESOURCES_NEEDED.md, DONE) sont stockes
dans le repertoire du projet cible et synchronisent l'agent
et l'interface GUI via le systeme de fichiers.

Toute lecture/ecriture doit passer par le mecanisme de
verrouillage (filelock) pour eviter les race conditions.
"""

from pathlib import Path

STATE_FILES = [
    "AGENTS.md",
    "PROGRESS.md",
    "BENCHMARKS.md",
    "SUGGESTIONS.md",
    "RESOURCES_NEEDED.md",
    "DONE",
]


def init_project_state(target_dir: Path) -> None:
    """Initialise les fichiers d'etat dans le repertoire cible.

    Args:
        target_dir: Chemin du repertoire du projet cible.
    """
    ...


def read_state(target_dir: Path, filename: str) -> str:
    """Lit un fichier d'etat avec verrouillage.

    Args:
        target_dir: Chemin du repertoire du projet cible.
        filename: Nom du fichier d'etat a lire.

    Returns:
        Contenu du fichier.
    """
    ...


def write_state(target_dir: Path, filename: str, content: str) -> None:
    """Ecrit un fichier d'etat avec verrouillage.

    Args:
        target_dir: Chemin du repertoire du projet cible.
        filename: Nom du fichier d'etat a ecrire.
        content: Contenu a ecrire.
    """
    ...


def append_state(target_dir: Path, filename: str, content: str) -> None:
    """Ajoute du contenu a un fichier d'etat avec verrouillage.

    Args:
        target_dir: Chemin du repertoire du projet cible.
        filename: Nom du fichier d'etat.
        content: Contenu a ajouter.
    """
    ...
