"""Operations Git sur le depot du projet cible.

Toutes les operations Git (commit, push, rollback) sont effectuees
exclusivement dans le repertoire du projet cible, jamais dans
le depot DeBuilder.
"""

import subprocess
from pathlib import Path


def commit_all(repo_dir: Path, message: str) -> bool:
    """Commit tous les changements dans le depot cible.

    Args:
        repo_dir: Chemin du depot Git cible.
        message: Message de commit.

    Returns:
        True si le commit a reussi, False sinon.
    """
    ...


def push(repo_dir: Path) -> bool:
    """Push les commits sur le remote.

    Args:
        repo_dir: Chemin du depot Git cible.

    Returns:
        True si le push a reussi, False sinon.
    """
    ...


def stage_and_commit_all(repo_dir: Path, message: str) -> bool:
    """Stage tous les changements, commit et push.

    Utilise par l'agent apres chaque iteration pour garantir
    la persistance du travail meme en cas d'echec.

    Args:
        repo_dir: Chemin du depot Git cible.
        message: Message de commit.

    Returns:
        True si l'operation complete a reussi.
    """
    ...


def rollback_last(repo_dir: Path) -> bool:
    """Annule le dernier commit (git reset --hard HEAD~1).

    Preserve la stabilite de la session et de DeBuilder.

    Args:
        repo_dir: Chemin du depot Git cible.

    Returns:
        True si le rollback a reussi, False sinon.
    """
    ...


def clone_repo(url: str, target_dir: Path) -> bool:
    """Clone un depot Git dans le repertoire cible.

    Args:
        url: URL du depot a cloner.
        target_dir: Chemin ou cloner le depot.

    Returns:
        True si le clone a reussi, False sinon.
    """
    ...
