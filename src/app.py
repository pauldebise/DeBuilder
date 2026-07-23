"""Point d'entree principal de l'interface web DeBuilder.

Lance l'application Gradio sur le port 7680 (ou DEBUILDER_PORT)
avec les onglets:
- Configuration
- Tableau de bord
- Centre de controle
- Requetes agent
- Logs systemes
"""

import os

import gradio as gr

from src.gui.config import build_config_tab
from src.gui.dashboard import build_dashboard_tab
from src.gui.control import build_control_tab
from src.gui.agents import build_agents_tab
from src.gui.logs import build_logs_tab

PORT = int(os.environ.get("DEBUILDER_PORT", "7680"))
TITLE = "DeBuilder - Orchestrateur OpenCode"


def build_interface() -> gr.Blocks:
    """Construit l'interface Gradio complete.

    Returns:
        L'application Gradio configuree.
    """
    with gr.Blocks(title=TITLE) as app:
        gr.Markdown("# DeBuilder - Orchestrateur OpenCode")
        gr.Markdown(
            "Agent IA autonome pour le developpement et le machine learning. "
            "Configuration, supervision et controle asynchrone."
        )

        target_dir_state = gr.State("")

        with gr.Tabs():
            config = build_config_tab(target_dir_state)
            dashboard = build_dashboard_tab(target_dir_state)
            control = build_control_tab(target_dir_state)
            agents = build_agents_tab(target_dir_state)
            logs = build_logs_tab(target_dir_state)

    return app


def main():
    """Fonction principale. Lance le serveur Gradio."""
    app = build_interface()
    app.queue()
    app.launch(
        server_port=PORT,
        server_name="0.0.0.0",
        share=False,
    )


if __name__ == "__main__":
    main()
