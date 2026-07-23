"""Utilitaires de traitement de texte.

Nettoyage des sorties terminal (codes ANSI) avant affichage
dans la GUI ou ecriture dans les fichiers d'etat et logs.
"""

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Supprime les codes d'echappement ANSI d'un texte.

    Args:
        text: Texte potentiellement colore (sortie terminal).

    Returns:
        Texte sans codes ANSI.
    """
    return _ANSI_RE.sub("", text)
