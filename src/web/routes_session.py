"""Routes de session : reprise et demarrage.

``GET /api/session`` miroir de la restauration au chargement de
``src/app.py`` (Gradio). ``POST /api/session/start`` miroir de
``src/gui/config.py::_start_session`` : clone/init du depot,
validation OpenCode, creation des fichiers d'etat, lancement de
``agent_loop.sh`` en sous-processus detache.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.core.git import clone_repo, configure_git, ensure_gitignore, init_repo
from src.core.loop_status import save_loop_pid
from src.core.secrets import inject_secrets
from src.core.session import clear_last_session, load_last_session, save_last_session
from src.core.state import init_project_state
from src.utils.hw_audit import audit_hardware, format_for_agent
from src.utils.text import strip_ansi

router = APIRouter()

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


class SessionStartRequest(BaseModel):
    """Corps de requete pour ``POST /api/session/start``.

    Les champs ``max_iterations``/``max_hours``/``web_tools``/
    ``model_fallback``/``test_cmd`` sont les reglages avances de
    l'ecran de configuration, injectes comme variables d'environnement
    dans le processus de la boucle (jamais dans ce process serveur) :
    ils ne sont lus que par ``agent_loop.sh`` et ``agent.py``.
    """

    repo_url: str = ""
    workspace_dir: str
    instructions: str = ""
    provider: str = "DeepSeek"
    model: str = ""
    api_key: str = ""
    github_token: str = ""
    git_name: str = ""
    git_email: str = ""
    max_iterations: int | None = Field(default=None, ge=0)
    max_hours: int | None = Field(default=None, ge=0)
    web_tools: bool = True
    model_fallback: str = ""
    test_cmd: str = ""


@router.get("/api/session")
def get_session() -> dict:
    """Renvoie la session active restauree, ou ``None`` si aucune."""
    target_dir = load_last_session()
    return {"target_dir": str(target_dir) if target_dir else None}


@router.post("/api/session/clear")
def clear_session() -> dict:
    """Oublie la session active (bouton « Nouvelle session »).

    Ne touche ni au depot cible ni a la boucle (deja arretee dans le
    cas nominal) : supprime uniquement le suivi de session, pour que
    l'ecran de configuration reapparaisse au prochain chargement.
    """
    clear_last_session()
    return {"message": "Session oubliee. L'ecran de configuration va s'afficher."}


@router.post("/api/session/start")
def start_session(payload: SessionStartRequest) -> dict:
    """Demarre une nouvelle session (clone/init + boucle agent)."""
    if not payload.workspace_dir.strip():
        raise HTTPException(400, "Le repertoire de travail est obligatoire.")

    if not payload.api_key.strip():
        raise HTTPException(
            400,
            f"Aucune cle API fournie. Renseignez une cle API valide pour le "
            f"fournisseur {payload.provider}.",
        )

    if not _find_opencode():
        raise HTTPException(
            400,
            "`opencode` n'est pas installe sur ce systeme. Installez-le : "
            "curl -fsSL https://opencode.ai/install | bash",
        )

    provider_cfg = PROVIDERS.get(payload.provider, PROVIDERS["Autre (custom)"])
    actual_model = _normalize_model(payload.model, provider_cfg)
    if not actual_model:
        raise HTTPException(
            400,
            "Aucun modele renseigne. Indiquez un modele au format "
            "fournisseur/modele (ex: deepseek/deepseek-v4-pro).",
        )

    target_dir = Path(payload.workspace_dir.strip()).expanduser().resolve()

    try:
        secrets = {key_name: payload.api_key.strip() for key_name in provider_cfg["env_keys"]}
        github_token = payload.github_token.strip()
        if github_token:
            # Nom contenant "TOKEN" pour etre reconnu automatiquement
            # par sanitize_text() si jamais reflete dans une sortie
            # (ex: l'agent qui lance `git remote -v`).
            secrets["DEBUILDER_GH_TOKEN"] = github_token
        inject_secrets(secrets)

        error = _validate_opencode(actual_model)
        if error:
            raise HTTPException(
                400,
                f"Erreur OpenCode : {error}. Verifiez votre cle API et le "
                f"modele {actual_model} (format fournisseur/modele).",
            )

        repo_url = payload.repo_url.strip()
        if repo_url:
            if target_dir.exists():
                raise HTTPException(400, f"{target_dir} existe deja.")
            clone_url = _inject_token(repo_url, github_token)
            if not clone_repo(clone_url, target_dir):
                raise HTTPException(400, "Echec du clone du depot.")
        else:
            if not target_dir.exists():
                target_dir.mkdir(parents=True)
            elif any(target_dir.iterdir()):
                raise HTTPException(400, f"{target_dir} n'est pas vide.")
            if not init_repo(target_dir):
                raise HTTPException(400, "Echec de l'initialisation Git.")

        ensure_gitignore(target_dir)

        configure_git(
            target_dir,
            user_name=payload.git_name.strip() or "DeBuilder Agent",
            user_email=payload.git_email.strip() or "agent@debuilder.local",
            token=github_token,
            remote_url=repo_url,
        )

        hw = audit_hardware()
        hw_text = format_for_agent(hw)

        init_project_state(
            target_dir,
            instructions=payload.instructions.strip(),
            hardware_info=hw_text,
            fresh_repo=not repo_url,
        )

        agent_script = (
            Path(__file__).resolve().parent.parent / "loop" / "agent_loop.sh"
        ).resolve()
        python_bin = os.environ.get("DEBUILDER_PYTHON", "python3")

        # Necessaire aussi dans ce process (pas seulement dans l'env du
        # sous-processus agent_loop.sh) : c'est ce process qui genere le
        # resume LLM du tableau de bord (cf. src/core/log_summarizer.py).
        os.environ["DEBUILDER_MODEL"] = actual_model

        loop_env = {
            **os.environ,
            "DEBUILDER_TARGET_DIR": str(target_dir),
            "DEBUILDER_PYTHON": python_bin,
            "DEBUILDER_MODEL": actual_model,
            "DEBUILDER_WEB_TOOLS": "1" if payload.web_tools else "0",
        }
        # Reglages avances optionnels : absents de l'env de la boucle
        # s'ils n'ont pas ete renseignes, pour laisser jouer les
        # valeurs par defaut (0 = illimite pour les caps, etc.).
        for key, value in (
            ("DEBUILDER_MAX_ITERATIONS", payload.max_iterations),
            ("DEBUILDER_MAX_HOURS", payload.max_hours),
            ("DEBUILDER_MODEL_FALLBACK", payload.model_fallback.strip()),
            ("DEBUILDER_TEST_CMD", payload.test_cmd.strip()),
        ):
            if value:
                loop_env[key] = str(value)

        proc = subprocess.Popen(
            ["bash", str(agent_script)],
            env=loop_env,
        )
        # PID persiste dans $DEBUILDER_STATE_DIR : le tableau de bord
        # peut ensuite distinguer boucle vivante / morte (cf.
        # src/core/loop_status.py), meme apres redemarrage de l'interface.
        save_loop_pid(proc.pid)

        save_last_session(target_dir)

        return {
            "message": "Session lancee. L'agent tourne en arriere-plan.",
            "target_dir": str(target_dir),
            "provider": payload.provider,
            "model": actual_model,
            "hardware_info": hw_text,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)) from e


def _inject_token(url: str, token: str) -> str:
    """Injecte un token GitHub dans l'URL pour l'authentification."""
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
    """Normalise un nom de modele au format opencode `fournisseur/modele`."""
    model = (model or "").strip()
    if not model:
        model = provider_cfg["default_model"]
    if model and "/" not in model and provider_cfg["prefix"]:
        model = f"{provider_cfg['prefix']}/{model}"
    return model


def _validate_opencode(model: str) -> str:
    """Valide que opencode fonctionne avec la cle API.

    Le test s'execute dans un repertoire temporaire vierge : jamais
    dans le repertoire de DeBuilder (isolation), et sans toucher au
    repertoire cible (un echec ne laisse aucun residu).

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
        return (
            "Le test de la cle API a expire apres 60s (opencode ne repond pas : "
            "cle invalide, ou en attente d'une confirmation interactive)."
        )
    except FileNotFoundError:
        return "Commande `opencode` introuvable."
    except Exception as e:
        return str(e)
