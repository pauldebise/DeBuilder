"""Onglet Requetes de l'Agent (Escalade Non-Bloquante).

Affiche les demandes de ressources emises par l'agent
et permet a l'utilisateur d'y repondre.

L'agent ne se bloque jamais: il continue sans la ressource
si l'utilisateur ne repond pas.
"""

import gradio as gr

from pathlib import Path


def build_agents_tab(target_dir: Path) -> gr.TabItem:
    """Construit l'onglet des requetes agent.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Le composant gr.TabItem configure.
    """
    ...


def get_agent_requests(target_dir: Path) -> str:
    """Lit les demandes de ressources de l'agent.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Contenu formate des requetes.
    """
    ...


def respond_to_request(target_dir: Path, response: str) -> str:
    """Repond a une demande de l'agent (bonus).

    Args:
        target_dir: Repertoire du projet cible.
        response: Reponse de l'utilisateur.

    Returns:
        Message de confirmation.
    """
    ...
