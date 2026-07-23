"""Onglet Tableau de Bord (lecture seule).

Affichage en temps reel (polling) de:
- L'etat d'avancement depuis PROGRESS.md
- Les metriques depuis BENCHMARKS.md
- Les alertes watchdog
"""

import gradio as gr

from pathlib import Path


def build_dashboard_tab(target_dir: Path) -> gr.TabItem:
    """Construit l'onglet tableau de bord.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Le composant gr.TabItem configure.
    """
    ...


def refresh_dashboard(target_dir: Path) -> tuple[str, str, str]:
    """Rafraichit les donnees du tableau de bord par polling.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Tuple (progression, benchmarks, alertes) pour mise a jour GUI.
    """
    ...
