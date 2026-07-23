"""Utilitaires de parsing Markdown.

Extraction de donnees structurees depuis les fichiers d'etat
Markdown (PROGRESS.md, BENCHMARKS.md).
"""


def parse_progress(content: str) -> dict:
    """Parse le contenu de PROGRESS.md en structure exploitable.

    Args:
        content: Contenu brut de PROGRESS.md.

    Returns:
        Dictionnaire avec les sections cles de progression.
    """
    ...


def parse_benchmarks(content: str) -> list[dict]:
    """Extrait les tableaux de metriques depuis BENCHMARKS.md.

    Args:
        content: Contenu brut de BENCHMARKS.md.

    Returns:
        Liste de dictionnaires representant les lignes de benchmark.
    """
    ...


def parse_alerts(content: str) -> list[dict]:
    """Extrait les alertes watchdog depuis PROGRESS.md.

    Args:
        content: Contenu brut de PROGRESS.md.

    Returns:
        Liste des alertes detectees.
    """
    ...
