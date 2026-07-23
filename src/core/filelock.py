"""Mecanisme de verrouillage de fichiers.

Empeche les race conditions entre les ecritures de l'agent
et le polling de l'interface GUI. Utilise un fichier .lock
par fichier d'etat.
"""

import contextlib
import fcntl
import os
import time
from pathlib import Path


@contextlib.contextmanager
def file_lock(filepath: Path):
    """Context manager pour un verrou exclusif sur un fichier.

    Usage:
        with file_lock(Path("/tmp/AGENTS.md")):
            ...

    Le fichier de verrou n'est jamais supprime apres usage : le
    supprimer creerait une fenetre de race (TOCTOU) ou un second
    processus verrouille l'inode existant juste avant qu'un troisieme,
    arrivant apres la suppression, en cree un nouveau et le verrouille
    aussi. Les deux croiraient alors detenir un verrou exclusif.

    Args:
        filepath: Chemin du fichier a verrouiller.
    """
    lock_path = Path(str(filepath) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = acquire_lock(lock_path)
    try:
        yield
    finally:
        release_lock(fd)
        try:
            os.close(fd)
        except OSError:
            pass


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
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT | os.O_TRUNC)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise TimeoutError(
                    f"Impossible d'acquerir le verrou dans le delai de {timeout}s"
                )
            time.sleep(0.05)


def release_lock(fd: int) -> None:
    """Relache un verrou precedemment acquis.

    Args:
        fd: Descripteur de fichier du verrou.
    """
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
