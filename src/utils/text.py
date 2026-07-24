"""Utilitaires de traitement de texte.

Nettoyage des sorties terminal (codes ANSI) avant affichage
dans la GUI ou ecriture dans les fichiers d'etat et logs.
"""

import re
from pathlib import Path

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Supprime les codes d'echappement ANSI d'un texte.

    Args:
        text: Texte potentiellement colore (sortie terminal).

    Returns:
        Texte sans codes ANSI.
    """
    return _ANSI_RE.sub("", text)


def read_log_tail(target_dir: Path, filename: str, n: int = 200) -> str:
    """Lit les n dernieres lignes d'un fichier de log.

    Le fichier de log (ex: OPENCODE_LOG.txt) est ecrit en continu par
    l'agent hors du mecanisme de verrouillage des fichiers d'etat : une
    simple lecture best-effort suffit ici.

    Args:
        target_dir: Repertoire du projet cible.
        filename: Nom du fichier de log.
        n: Nombre de lignes a conserver depuis la fin.

    Returns:
        Les dernieres lignes du fichier, ou "" si absent/illisible.
    """
    log_file = target_dir / filename
    if not log_file.exists():
        return ""
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").split("\n")
        return "\n".join(lines[-n:])
    except OSError:
        return ""
