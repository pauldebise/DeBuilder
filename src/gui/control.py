"""Onglet Centre de Controle (Intervention Asynchrone).

Permet a l'utilisateur de:
- Envoyer des suggestions via SUGGESTIONS.md
- Activer le kill-switch (creation du fichier DONE)
- Effectuer un rollback (git reset --hard HEAD~1)
- Configurer des barrieres (Human-in-the-Loop)
"""

import gradio as gr

from pathlib import Path


def build_control_tab(target_dir: Path) -> gr.TabItem:
    """Construit l'onglet centre de controle.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Le composant gr.TabItem configure.
    """
    ...


def send_suggestion(target_dir: Path, message: str) -> str:
    """Ecrit un message de guidage humain dans SUGGESTIONS.md.

    Args:
        target_dir: Repertoire du projet cible.
        message: Message de l'utilisateur.

    Returns:
        Message de confirmation.
    """
    ...


def trigger_kill_switch(target_dir: Path) -> str:
    """Cree le fichier temoin DONE pour arreter l'agent.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Message de confirmation.
    """
    ...


def perform_rollback(target_dir: Path) -> str:
    """Execute un git reset --hard HEAD~1 sur le depot cible.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Message de confirmation ou d'erreur.
    """
    ...


def set_barrier(target_dir: Path, op_type: str, enabled: bool) -> str:
    """Configure une barriere Human-in-the-Loop.

    Args:
        target_dir: Repertoire du projet cible.
        op_type: Type d'operation necessitant validation.
        enabled: Activer ou desactiver la barriere.

    Returns:
        Message de confirmation.
    """
    ...
