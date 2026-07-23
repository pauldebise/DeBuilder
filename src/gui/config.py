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
from src.core.git import clone_repo, init_repo, configure_git
from src.utils.hw_audit import audit_hardware, format_for_agent

PROVIDERS = {
    "DeepSeek": {
        "env_keys": ["DEEPSEEK_API_KEY"],
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-v4-pro",
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
                    lines=4,
                )

                gr.Markdown("### Git (optionnel)")
                gh_token = gr.Textbox(
                    label="Token GitHub",
                    placeholder="ghp_... ou github_pat_...",
                    type="password",
                    info="Pour push automatique sur le depot.",
                )
                with gr.Row():
                    git_name = gr.Textbox(
                        label="Nom Git",
                        placeholder="DeBuilder Agent",
                        info="Auteur des commits.",
                    )
                    git_email = gr.Textbox(
                        label="Email Git",
                        placeholder="agent@debuilder.local",
                        info="Email de l'auteur.",
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
                    value="deepseek-v4-pro",
                    placeholder="deepseek-v4-pro",
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
            inputs=[repo_url, workspace_dir, instructions, provider, model, api_key, gh_token, git_name, git_email],
            outputs=[status, target_dir_state],
        )

    return tab


def _inject_token(url: str, token: str) -> str:
    """Injecte un token GitHub dans l'URL pour l'authentification.

    Args:
        url: URL du depot (ex: https://github.com/user/repo.git).
        token: Token GitHub (ghp_...).

    Returns:
        URL avec token injecte.
    """
    if not token:
        return url
    if url.startswith("https://"):
        return url.replace("https://", f"https://{token}@")
    return url


def _find_opencode() -> str | None:
    """Trouve opencode dans le PATH ou les emplacements connus."""
    path = shutil.which("opencode")
    if path:
        return path
    for candidate in [
        "/usr/local/bin/opencode",
        Path.home() / ".opencode/bin/opencode",
        Path.home() / "bin/opencode",
    ]:
        if Path(str(candidate)).exists():
            return str(candidate)
    return None


def _start_session(
    repo_url: str,
    workspace_dir: str,
    instructions: str,
    provider: str,
    model: str,
    api_key: str,
    gh_token: str = "",
    git_name: str = "",
    git_email: str = "",
) -> tuple[str, str]:
    if not workspace_dir.strip():
        return "**Erreur :** Le repertoire de travail est obligatoire.", ""

    if not _find_opencode():
        return (
            "**Erreur :** `opencode` n'est pas installe sur ce systeme.\n\n"
            "Installez-le : `curl -fsSL https://opencode.ai/install | bash`\n"
            "ou : `npm install -g opencode-ai@latest`",
            "",
        )

    target_dir = Path(workspace_dir.strip()).expanduser().resolve()

    try:
        if repo_url.strip():
            if target_dir.exists():
                return f"**Erreur :** `{target_dir}` existe deja.", ""
            clone_url = _inject_token(repo_url.strip(), gh_token.strip() if gh_token else "")
            if not clone_repo(clone_url, target_dir):
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

        configure_git(
            target_dir,
            user_name=git_name.strip() or "DeBuilder Agent",
            user_email=git_email.strip() or "agent@debuilder.local",
            token=gh_token.strip() if gh_token else "",
            remote_url=repo_url.strip() if repo_url else "",
        )

        provider_cfg = PROVIDERS.get(provider, PROVIDERS["Autre (custom)"])
        secrets = {}
        base_url = provider_cfg["base_url"]
        actual_model = model or provider_cfg["default_model"]

        if api_key.strip():
            for key_name in provider_cfg["env_keys"]:
                secrets[key_name] = api_key.strip()
            if base_url:
                secrets["OPENAI_BASE_URL"] = base_url

        if not api_key.strip():
            return (
                "**Erreur :** Aucune cle API fournie.\n\n"
                "Renseignez une cle API valide pour le fournisseur **{}**.".format(provider),
                "",
            )

        inject_secrets(secrets)

        msg = "Verification de la cle API...\n\n"

        error = _validate_opencode(actual_model)
        if error:
            return f"**Erreur OpenCode :**\n```\n{error}\n```\n\nVerifiez votre cle API et le modele `{actual_model}`.", ""

        msg += "Cle API validee.\n"
        msg += f"Fournisseur : **{provider}**\n"
        msg += f"Modele : `{actual_model}`\n\n"

        hw = audit_hardware()
        hw_text = format_for_agent(hw)

        init_project_state(
            target_dir,
            instructions=instructions.strip(),
            hardware_info=hw_text,
        )
        msg += "Fichiers d'etat crees.\n\n"
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
                "DEBUILDER_MODEL": actual_model,
            },
        )

        msg += "**Session lancee !** L'agent tourne en arriere-plan."

        return msg, str(target_dir)

    except Exception as e:
        return f"**Erreur :** {e}", ""


def _validate_opencode(model: str) -> str:
    """Valide que opencode fonctionne avec la cle API.

    Args:
        model: Nom du modele a tester.

    Returns:
        Message d'erreur, ou chaine vide si OK.
    """
    bin_path = _find_opencode()
    if not bin_path:
        return "Commande `opencode` introuvable."

    cmd = [bin_path, "run", "reply with just the word ok"]
    if model:
        cmd.extend(["--model", model])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() if result.stderr else ""
            stdout = result.stdout.strip() if result.stdout else ""
            return stderr or stdout or f"Erreur inconnue (code {result.returncode})"
        return ""
    except subprocess.TimeoutExpired:
        return ""
    except FileNotFoundError:
        return "Commande `opencode` introuvable."
    except Exception as e:
        return str(e)
