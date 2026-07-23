"""Mecanisme de verrouillage de fichiers.

Empeche les race conditions entre les ecritures de l'agent
et le polling de l'interface GUI. Utilise un fichier .lock
par fichier d'etat.
"""

import contextlib
import fcntl
import os
from pathlib import Path


@contextlib.contextmanager
def file_lock(filepath: Path):
    """Context manager pour un verrou exclusif sur un fichier.

    Usage:
        with file_lock(Path("/tmp/AGENTS.md")):
            ...

    Args:
        filepath: Chemin du fichier a verrouiller.
    """
    ...


def acquire_lock(lock_path: Path, timeout: float = 5.0) -> int:
    """Acquiert un verrou sur un fichier dans un delai donne.

    Args:
        lock_path: Chemin du fichier de verrou.
        timeout: Delai maximum en secondes.

    Returns:
        Descripteur de fichier du verrou.

    Raises:
        TimeoutError: Si le verrou n'a pas pu etre acquis.
    """
    ...


def release_lock(fd: int) -> None:
    """Relache un verrou precedemment acquis.

    Args:
        fd: Descripteur de fichier du verrou.
    """
    ...
