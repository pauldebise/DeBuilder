"""Point d'entree principal de l'interface web DeBuilder.

Lance l'application Gradio sur le port 7680 avec les onglets:
- Configuration
- Tableau de bord
- Centre de controle
- Requetes agent
- Logs systemes
"""

import gradio as gr

from pathlib import Path

from src.gui.config import build_config_tab
from src.gui.dashboard import build_dashboard_tab
from src.gui.control import build_control_tab
from src.gui.agents import build_agents_tab
from src.gui.logs import build_logs_tab

PORT = 7680
TITLE = "DeBuilder - Orchestrateur OpenCode"


def build_interface(target_dir: Path | None = None) -> gr.Blocks:
    """Construit l'interface Gradio complete.

    Args:
        target_dir: Repertoire du projet cible (None = pas encore initialise).

    Returns:
        L'application Gradio configuree.
    """
    ...


def main():
    """Fonction principale. Lance le serveur Gradio."""
    ...


if __name__ == "__main__":
    main()
