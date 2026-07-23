"""Onglet Logs Systemes.

Affichage direct des fichiers de logs bruts generes
par l'agent et le script de boucle.
"""

import gradio as gr

from pathlib import Path


def build_logs_tab(target_dir: Path) -> gr.TabItem:
    """Construit l'onglet des logs systemes.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Le composant gr.TabItem configure.
    """
    ...


def get_agent_logs(target_dir: Path) -> str:
    """Recupere les logs bruts de l'agent.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Contenu des logs.
    """
    ...


def get_system_logs() -> str:
    """Recupere les logs du script de boucle DeBuilder.

    Returns:
        Contenu des logs systeme.
    """
    ...
