"""Onglet Configuration Initiale et Gestion de Session.

Permet de lancer une nouvelle session en preparant un
environnement vierge ou en clonant un depot Git.
Gerer les secrets (cles API) via variables d'environnement.
"""

import os
import subprocess
from pathlib import Path

import gradio as gr

from src.core.state import init_project_state
from src.core.secrets import inject_secrets
from src.core.git import clone_repo, init_repo
from src.utils.hw_audit import audit_hardware, format_for_agent


def build_config_tab(target_dir_state: gr.State) -> gr.TabItem:
    """Construit l'onglet de configuration."""
    with gr.TabItem("Configuration") as tab:
        gr.Markdown("## Demarrage d'une session")

        with gr.Row():
            with gr.Column(scale=2):
                repo_url = gr.Textbox(
                    label="URL du depot Git (optionnel)",
                    placeholder="https://github.com/user/repo.git",
                )
                workspace_dir = gr.Textbox(
                    label="Repertoire de travail",
                    placeholder="/home/user/projects/mon-projet",
                )
                instructions = gr.Textbox(
                    label="Cahier des charges",
                    placeholder="Decrivez l'objectif du projet...",
                    lines=6,
                )

            with gr.Column(scale=1):
                gr.Markdown("### Secrets (cles API)")
                secret_keys = gr.Textbox(
                    label="Cles (une par ligne)",
                    placeholder="OPENAI_API_KEY\nHUGGINGFACE_TOKEN",
                    lines=3,
                )
                secret_values = gr.Textbox(
                    label="Valeurs (une par ligne, meme ordre)",
                    placeholder="sk-...\nhf_...",
                    lines=3,
                )

        with gr.Row():
            start_btn = gr.Button("Demarrer la session", variant="primary", size="lg")

        status = gr.Markdown("")

        start_btn.click(
            fn=_start_session,
            inputs=[repo_url, workspace_dir, instructions, secret_keys, secret_values],
            outputs=[status, target_dir_state],
        )

    return tab


def _start_session(
    repo_url: str,
    workspace_dir: str,
    instructions: str,
    keys: str,
    values: str,
) -> tuple[str, str]:
    if not workspace_dir.strip():
        return "**Erreur :** Le repertoire de travail est obligatoire.", ""

    target_dir = Path(workspace_dir.strip()).expanduser().resolve()

    try:
        if repo_url.strip():
            if target_dir.exists():
                return f"**Erreur :** `{target_dir}` existe deja.", ""
            if not clone_repo(repo_url.strip(), target_dir):
                return "**Erreur :** Echec du clone du depot.", ""
            msg = f"Depot clone dans `{target_dir}`.\n\n"
        else:
            if not target_dir.exists():
                target_dir.mkdir(parents=True)
            elif any(target_dir.iterdir()):
                return f"**Erreur :** `{target_dir}` n'est pas vide.", ""
            if not init_repo(target_dir):
                return "**Erreur :** Echec de l'initialisation Git.", ""
            msg = f"Nouveau projet initialise dans `{target_dir}`.\n\n"

        hw = audit_hardware()
        hw_text = format_for_agent(hw)

        init_project_state(
            target_dir,
            instructions=instructions.strip(),
            hardware_info=hw_text,
        )
        msg += "Fichiers d'etat crees.\n\n"

        if keys.strip():
            key_list = [k.strip() for k in keys.strip().split("\n") if k.strip()]
            val_list = [v.strip() for v in values.strip().split("\n") if v.strip()]
            if len(key_list) != len(val_list):
                return "**Erreur :** Le nombre de cles et de valeurs ne correspond pas.", ""
            secrets = dict(zip(key_list, val_list))
            inject_secrets(secrets)
            msg += f"{len(secrets)} secret(s) injecte(s).\n\n"

        msg += "### Materiel detecte\n\n" + hw_text + "\n\n"

        agent_script = (
            Path(__file__).parent.parent / "loop" / "agent_loop.sh"
        ).resolve()
        subprocess.Popen(
            ["bash", str(agent_script)],
            env={**os.environ, "DEBUILDER_TARGET_DIR": str(target_dir)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        msg += "**Session lancee !** L'agent tourne en arriere-plan."

        return msg, str(target_dir)

    except Exception as e:
        return f"**Erreur :** {e}", ""
