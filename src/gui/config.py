"""Onglet Configuration Initiale et Gestion de Session.

Permet de lancer une nouvelle session en preparant un
environnement vierge ou en clonant un depot Git.
Configuration simplifiee de l'API et du modele OpenCode.
"""

import os
import shutil
import subprocess
from pathlib import Path

import gradio as gr

from src.core.state import init_project_state
from src.core.secrets import inject_secrets
from src.core.git import clone_repo, init_repo
from src.utils.hw_audit import audit_hardware, format_for_agent

PROVIDERS = {
    "DeepSeek": {
        "env_keys": ["DEEPSEEK_API_KEY"],
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    "OpenAI": {
        "env_keys": ["OPENAI_API_KEY"],
        "base_url": "",
        "default_model": "gpt-4o",
    },
    "Anthropic": {
        "env_keys": ["ANTHROPIC_API_KEY"],
        "base_url": "",
        "default_model": "claude-sonnet-4-20250514",
    },
    "Autre (custom)": {
        "env_keys": ["OPENAI_API_KEY"],
        "base_url": "",
        "default_model": "",
    },
}


def build_config_tab(target_dir_state: gr.State) -> gr.TabItem:
    """Construit l'onglet de configuration."""
    with gr.TabItem("Configuration") as tab:
        gr.Markdown("## Demarrage d'une session")

        with gr.Row():
            with gr.Column(scale=2):
                gr.Markdown("### Projet")
                repo_url = gr.Textbox(
                    label="URL du depot Git (optionnel)",
                    placeholder="https://github.com/user/repo.git",
                )
                workspace_dir = gr.Textbox(
                    label="Repertoire de travail",
                    placeholder="/workspace/mon-projet",
                )
                instructions = gr.Textbox(
                    label="Cahier des charges",
                    placeholder="Decrivez l'objectif du projet...",
                    lines=5,
                )

            with gr.Column(scale=1):
                gr.Markdown("### Modele IA")
                provider = gr.Dropdown(
                    label="Fournisseur",
                    choices=list(PROVIDERS.keys()),
                    value="DeepSeek",
                )
                model = gr.Textbox(
                    label="Modele",
                    value="deepseek-chat",
                    placeholder="deepseek-chat",
                    info="Nom du modele pour OpenCode.",
                )
                api_key = gr.Textbox(
                    label="Cle API",
                    placeholder="sk-...",
                    type="password",
                    info="Jamais ecrite sur le disque.",
                )

        with gr.Row():
            start_btn = gr.Button("Demarrer la session", variant="primary", size="lg")

        status = gr.Markdown("")

        provider.change(
            fn=lambda p: PROVIDERS.get(p, {}).get("default_model", ""),
            inputs=[provider],
            outputs=[model],
        )

        start_btn.click(
            fn=_start_session,
            inputs=[repo_url, workspace_dir, instructions, provider, model, api_key],
            outputs=[status, target_dir_state],
        )

    return tab


def _start_session(
    repo_url: str,
    workspace_dir: str,
    instructions: str,
    provider: str,
    model: str,
    api_key: str,
) -> tuple[str, str]:
    if not workspace_dir.strip():
        return "**Erreur :** Le repertoire de travail est obligatoire.", ""

    if not shutil.which("opencode"):
        return (
            "**Erreur :** `opencode` n'est pas installe sur ce systeme.\n\n"
            "Installez-le : `npm install -g opencode` ou via pip.",
            "",
        )

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

        provider_cfg = PROVIDERS.get(provider, PROVIDERS["Autre (custom)"])
        secrets = {}
        base_url = provider_cfg["base_url"]

        if api_key.strip():
            for key_name in provider_cfg["env_keys"]:
                secrets[key_name] = api_key.strip()
            if base_url:
                secrets["OPENAI_BASE_URL"] = base_url

        if secrets:
            inject_secrets(secrets)
            msg += f"Cles injectees pour **{provider}**.\n"
            msg += f"Modele : `{model or provider_cfg['default_model']}`\n\n"
        else:
            msg += "**Attention :** Aucune cle API fournie. OpenCode risque de ne pas fonctionner.\n\n"

        msg += "### Materiel detecte\n\n" + hw_text + "\n\n"

        agent_script = (
            Path(__file__).parent.parent / "loop" / "agent_loop.sh"
        ).resolve()
        python_bin = os.environ.get("DEBUILDER_PYTHON", "python3")

        subprocess.Popen(
            ["bash", str(agent_script)],
            env={
                **os.environ,
                "DEBUILDER_TARGET_DIR": str(target_dir),
                "DEBUILDER_PYTHON": python_bin,
                "DEBUILDER_MODEL": model or provider_cfg["default_model"],
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        msg += "**Session lancee !** L'agent tourne en arriere-plan."

        return msg, str(target_dir)

    except Exception as e:
        return f"**Erreur :** {e}", ""
