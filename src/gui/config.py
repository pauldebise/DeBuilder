"""Onglet Configuration Initiale et Gestion de Session.

Permet de lancer une nouvelle session en preparant un
environnement vierge ou en clonant un depot Git.
Configuration simplifiee de l'API et du modele OpenCode.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import gradio as gr

from src.core.state import init_project_state
from src.core.secrets import inject_secrets
from src.core.session import save_last_session
from src.core.git import clone_repo, init_repo, configure_git, ensure_gitignore
from src.utils.hw_audit import audit_hardware, format_for_agent
from src.utils.text import strip_ansi

# Les modeles opencode s'ecrivent toujours `fournisseur/modele`
# (cf. `opencode models`). `prefix` sert a completer un nom saisi
# sans fournisseur.
PROVIDERS = {
    "DeepSeek": {
        "env_keys": ["DEEPSEEK_API_KEY"],
        "prefix": "deepseek",
        "default_model": "deepseek/deepseek-v4-pro",
    },
    "OpenAI": {
        "env_keys": ["OPENAI_API_KEY"],
        "prefix": "openai",
        "default_model": "openai/gpt-5.2-codex",
    },
    "Anthropic": {
        "env_keys": ["ANTHROPIC_API_KEY"],
        "prefix": "anthropic",
        "default_model": "anthropic/claude-sonnet-5",
    },
    "Autre (custom)": {
        "env_keys": ["OPENAI_API_KEY"],
        "prefix": "",
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
                    value="deepseek/deepseek-v4-pro",
                    placeholder="deepseek/deepseek-v4-pro",
                    info="Format opencode : fournisseur/modele.",
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


def _normalize_model(model: str, provider_cfg: dict) -> str:
    """Normalise un nom de modele au format opencode `fournisseur/modele`.

    Args:
        model: Nom saisi par l'utilisateur (ex: "deepseek-v4-pro").
        provider_cfg: Entree de PROVIDERS pour le fournisseur choisi.

    Returns:
        Nom complet (ex: "deepseek/deepseek-v4-pro"), ou "" si inconnu.
    """
    model = (model or "").strip()
    if not model:
        model = provider_cfg["default_model"]
    if model and "/" not in model and provider_cfg["prefix"]:
        model = f"{provider_cfg['prefix']}/{model}"
    return model


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

    if not api_key.strip():
        return (
            "**Erreur :** Aucune cle API fournie.\n\n"
            "Renseignez une cle API valide pour le fournisseur **{}**.".format(provider),
            "",
        )

    if not _find_opencode():
        return (
            "**Erreur :** `opencode` n'est pas installe sur ce systeme.\n\n"
            "Installez-le : `curl -fsSL https://opencode.ai/install | bash`\n"
            "ou : `npm install -g opencode-ai@latest`",
            "",
        )

    provider_cfg = PROVIDERS.get(provider, PROVIDERS["Autre (custom)"])
    actual_model = _normalize_model(model, provider_cfg)
    if not actual_model:
        return (
            "**Erreur :** Aucun modele renseigne.\n\n"
            "Indiquez un modele au format `fournisseur/modele` "
            "(ex: `deepseek/deepseek-v4-pro`, cf. `opencode models`).",
            "",
        )

    target_dir = Path(workspace_dir.strip()).expanduser().resolve()

    try:
        secrets = {key_name: api_key.strip() for key_name in provider_cfg["env_keys"]}
        if gh_token.strip():
            # Nom contenant "TOKEN" pour etre reconnu automatiquement
            # par sanitize_text() si jamais reflete dans une sortie
            # (ex: l'agent qui lance `git remote -v`).
            secrets["DEBUILDER_GH_TOKEN"] = gh_token.strip()
        inject_secrets(secrets)

        error = _validate_opencode(actual_model)
        if error:
            return (
                f"**Erreur OpenCode :**\n```\n{error}\n```\n\n"
                f"Verifiez votre cle API et le modele `{actual_model}` "
                f"(format `fournisseur/modele`, cf. `opencode models`).",
                "",
            )

        msg = "Cle API validee.\n"
        msg += f"Fournisseur : **{provider}**\n"
        msg += f"Modele : `{actual_model}`\n\n"

        if repo_url.strip():
            if target_dir.exists():
                return f"**Erreur :** `{target_dir}` existe deja.", ""
            clone_url = _inject_token(repo_url.strip(), gh_token.strip() if gh_token else "")
            if not clone_repo(clone_url, target_dir):
                return "**Erreur :** Echec du clone du depot.", ""
            msg += f"Depot clone dans `{target_dir}`.\n\n"
        else:
            if not target_dir.exists():
                target_dir.mkdir(parents=True)
            elif any(target_dir.iterdir()):
                return f"**Erreur :** `{target_dir}` n'est pas vide.", ""
            if not init_repo(target_dir):
                return "**Erreur :** Echec de l'initialisation Git.", ""
            msg += f"Nouveau projet initialise dans `{target_dir}`.\n\n"

        ensure_gitignore(target_dir)

        configure_git(
            target_dir,
            user_name=git_name.strip() or "DeBuilder Agent",
            user_email=git_email.strip() or "agent@debuilder.local",
            token=gh_token.strip() if gh_token else "",
            remote_url=repo_url.strip() if repo_url else "",
        )

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

        save_last_session(target_dir)

        msg += "**Session lancee !** L'agent tourne en arriere-plan."

        return msg, str(target_dir)

    except Exception as e:
        return f"**Erreur :** {e}", ""


def _validate_opencode(model: str) -> str:
    """Valide que opencode fonctionne avec la cle API.

    Le test s'execute dans un repertoire temporaire vierge :
    jamais dans le repertoire de DeBuilder (isolation), et sans
    toucher au repertoire cible (un echec ne laisse aucun residu).

    Args:
        model: Nom du modele a tester (format `fournisseur/modele`).

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
        with tempfile.TemporaryDirectory(prefix="debuilder-validate-") as tmp_dir:
            # opencode se fie a $PWD plutot qu'au cwd reel : il faut
            # l'epingler pour ne pas qu'il tourne dans DeBuilder.
            result = subprocess.run(
                cmd + ["--dir", tmp_dir],
                cwd=tmp_dir,
                env={**os.environ, "PWD": tmp_dir},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        if result.returncode != 0:
            stderr = strip_ansi(result.stderr).strip() if result.stderr else ""
            stdout = strip_ansi(result.stdout).strip() if result.stdout else ""
            return stderr or stdout or f"Erreur inconnue (code {result.returncode})"
        return ""
    except subprocess.TimeoutExpired:
        return ""
    except FileNotFoundError:
        return "Commande `opencode` introuvable."
    except Exception as e:
        return str(e)
