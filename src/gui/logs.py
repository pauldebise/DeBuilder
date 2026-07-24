"""Onglet Logs Systemes.

Affichage direct des logs opencode et de la boucle agent.
"""

from pathlib import Path

import gradio as gr

from src.core.state import read_state
from src.core.secrets import sanitize_text
from src.utils.text import read_log_tail


def build_logs_tab(target_dir_state: gr.State) -> gr.TabItem:
    """Construit l'onglet des logs systemes."""
    with gr.TabItem("Logs Systemes") as tab:
        gr.Markdown("## Logs Systemes")

        gr.Markdown("### Logs OpenCode (temps reel)")
        gr.Markdown("*Affichage des 200 dernieres lignes du fichier `OPENCODE_LOG.txt`.*")
        opencode_logs_display = gr.Textbox(
            label="Sortie brute d'OpenCode",
            lines=20,
            max_lines=200,
            interactive=False,
        )

        gr.Markdown("### Logs Agent (PROGRESS.md)")
        agent_logs = gr.Textbox(
            label="Progression de l'agent",
            lines=10,
            max_lines=100,
            interactive=False,
        )

        refresh_logs_btn = gr.Button("Rafraichir les logs", variant="secondary")

        refresh_logs_btn.click(
            fn=lambda td: (
                sanitize_text(_get_opencode_logs(td)),
                sanitize_text(_get_agent_logs(td)),
            ),
            inputs=[target_dir_state],
            outputs=[opencode_logs_display, agent_logs],
        )

    return tab


def _get_target(target_dir_str: str) -> Path | None:
    if not target_dir_str:
        return None
    return Path(target_dir_str)


def _get_opencode_logs(target_dir_str: str) -> str:
    target_dir = _get_target(target_dir_str)
    if not target_dir:
        return "*Aucune session active.*"
    tail = read_log_tail(target_dir, "OPENCODE_LOG.txt", 200)
    return tail or "*Aucun log opencode pour le moment.*"


def _get_agent_logs(target_dir_str: str) -> str:
    target_dir = _get_target(target_dir_str)
    if not target_dir:
        return "*Aucune session active.*"
    progress = read_state(target_dir, "PROGRESS.md")
    return progress or "*En attente...*"
