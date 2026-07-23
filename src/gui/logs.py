"""Onglet Logs Systemes.

Affichage direct des fichiers de logs bruts generes
par l'agent et le script de boucle.
"""

from pathlib import Path

import gradio as gr

from src.core.state import read_state
from src.core.secrets import sanitize_text


def build_logs_tab(target_dir_state: gr.State) -> gr.TabItem:
    """Construit l'onglet des logs systemes."""
    with gr.TabItem("Logs Systemes") as tab:
        gr.Markdown("## Logs Systemes")

        gr.Markdown("### Logs Agent")
        agent_logs = gr.Textbox(
            label="Logs de l'agent",
            lines=20,
            max_lines=200,
            interactive=False,
        )

        gr.Markdown("### Logs DeBuilder")
        system_logs = gr.Textbox(
            label="Logs systeme",
            lines=10,
            max_lines=100,
            interactive=False,
        )

        refresh_logs_btn = gr.Button("Rafraichir les logs", variant="secondary")

        refresh_logs_btn.click(
            fn=lambda td: (sanitize_text(_get_agent_logs(td)), sanitize_text(_get_system_logs())),
            inputs=[target_dir_state],
            outputs=[agent_logs, system_logs],
        )

    return tab


def _get_agent_logs(target_dir_str: str) -> str:
    if not target_dir_str:
        return "*Aucune session active.*"
    target_dir = Path(target_dir_str)
    progress = read_state(target_dir, "PROGRESS.md")
    return progress or "*En attente...*"


def _get_system_logs() -> str:
    log_files = sorted(Path("/tmp").glob("debuilder_*.log"))
    if not log_files:
        return "*Aucun log systeme.*"
    parts = []
    for lf in log_files[-3:]:
        try:
            parts.append(lf.read_text()[-2000:])
        except OSError:
            pass
    return "\n---\n".join(parts) or "*Aucun log.*"
