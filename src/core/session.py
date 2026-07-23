"""Persistance minimale de la session active.

Permet a l'interface GUI de se rattacher a une session d'agent deja
en cours apres un redemarrage (crash, redemarrage du pod RunPod,
etc.) : le repertoire cible n'est normalement connu que via l'etat
en memoire de Gradio (gr.State), perdu si le process GUI redemarre
alors que agent_loop.sh continue de tourner en arriere-plan.

Le fichier de suivi est stocke hors des depots Git (ni celui de
DeBuilder, ni celui du projet cible) pour respecter l'isolation
imposee par le cahier des charges.
"""

import os
from pathlib import Path

_DEFAULT_STATE_DIR = Path.home() / ".debuilder"


def _state_dir() -> Path:
    override = os.environ.get("DEBUILDER_STATE_DIR")
    return Path(override) if override else _DEFAULT_STATE_DIR


def _session_file(state_dir: Path | None = None) -> Path:
    return (state_dir or _state_dir()) / "last_session.txt"


def save_last_session(target_dir: Path, state_dir: Path | None = None) -> None:
    """Enregistre le repertoire de la session active.

    Args:
        target_dir: Repertoire du projet cible.
        state_dir: Repertoire de persistance (surtout pour les tests).
            Par defaut ``~/.debuilder`` ou ``$DEBUILDER_STATE_DIR``.
    """
    session_file = _session_file(state_dir)
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(str(target_dir), encoding="utf-8")


def load_last_session(state_dir: Path | None = None) -> Path | None:
    """Recharge le repertoire de la derniere session valide.

    Ignore silencieusement les sessions dont le repertoire ou les
    fichiers d'etat n'existent plus (projet supprime, deplace, etc.).

    Args:
        state_dir: Repertoire de persistance (surtout pour les tests).

    Returns:
        Le chemin de la session si elle semble toujours valide,
        sinon None.
    """
    session_file = _session_file(state_dir)
    if not session_file.exists():
        return None

    raw = session_file.read_text(encoding="utf-8").strip()
    if not raw:
        return None

    target_dir = Path(raw)
    if not (target_dir / "AGENTS.md").exists():
        return None

    return target_dir


def clear_last_session(state_dir: Path | None = None) -> None:
    """Supprime le suivi de session active.

    Args:
        state_dir: Repertoire de persistance (surtout pour les tests).
    """
    session_file = _session_file(state_dir)
    try:
        session_file.unlink()
    except FileNotFoundError:
        pass
