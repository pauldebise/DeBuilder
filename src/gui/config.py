"""Onglet Configuration Initiale et Gestion de Session.

Permet de lancer une nouvelle session en preparant un
environnement vierge ou en clonant un depot Git.
Gerer les secrets (cles API) via variables d'environnement.
"""

import gradio as gr


def build_config_tab() -> gr.TabItem:
    """Construit l'onglet de configuration.

    Returns:
        Le composant gr.TabItem configure.
    """
    ...


def start_session(repo_url: str, workspace_dir: str, instructions: str,
                  secrets: dict[str, str]) -> None:
    """Lance une nouvelle session de l'agent.

    Args:
        repo_url: URL du depot Git a cloner (vide pour nouveau projet).
        workspace_dir: Repertoire de travail du projet cible.
        instructions: Cahier des charges transcrit dans AGENTS.md.
        secrets: Cles API a injecter comme variables d'environnement.
    """
    ...
