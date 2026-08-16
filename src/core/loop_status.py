"""Etat de vie de la boucle agent, pour le tableau de bord.

La boucle (``agent_loop.sh``) est un processus detache, lance par le
serveur web sans supervision : jusqu'ici, l'interface ne pouvait pas
distinguer « boucle vivante mais silencieuse » de « boucle morte »
(crash, OOM, kill exterieur). Deux signaux persistants comblent ce
trou :

- ``loop_pid.txt`` : PID du processus ``agent_loop.sh``, ecrit par le
  serveur web au lancement de la session (et persistant entre les
  redemarrages de l'interface) ;
- ``heartbeat.json`` : ecrit par la boucle elle-meme a chaque tour
  (debut d'iteration, fin d'iteration, sortie) avec le numero
  d'iteration et un horodatage.

Le tout vit dans ``$DEBUILDER_STATE_DIR`` (defaut ``~/.debuilder``),
hors des depots Git, comme le reste de la persistance de session.
"""

import json
import os
import time
from pathlib import Path

from src.core.iterations import read_entries
from src.core.session import _state_dir
from src.core.state import is_done

_PROC_DIR = Path("/proc")

_HEARTBEAT_FILENAME = "heartbeat.json"
_PID_FILENAME = "loop_pid.txt"


def save_loop_pid(pid: int, state_dir: Path | None = None) -> None:
    """Enregistre le PID du processus de la boucle agent.

    Args:
        pid: PID retourne par ``subprocess.Popen``.
        state_dir: Repertoire de persistance (surtout pour les tests).
    """
    directory = state_dir or _state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / _PID_FILENAME).write_text(str(pid), encoding="utf-8")


def load_loop_pid(state_dir: Path | None = None) -> int | None:
    """Relit le PID de la boucle, ou None si jamais enregistre."""
    pid_file = (state_dir or _state_dir()) / _PID_FILENAME
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def write_heartbeat(
    iteration: int, phase: str, state_dir: Path | None = None
) -> None:
    """Ecrit un battement de coeur de la boucle (jamais bloquant).

    Args:
        iteration: Numero de l'iteration courante.
        phase: ``iteration`` (debut de tour), ``end`` (fin de tour) ou
            ``exited`` (sortie du script).
        state_dir: Repertoire de persistance (surtout pour les tests).
    """
    directory = state_dir or _state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / _HEARTBEAT_FILENAME).write_text(
        json.dumps(
            {
                "iteration": iteration,
                "phase": phase,
                "updated_at": int(time.time()),
            }
        ),
        encoding="utf-8",
    )


def read_heartbeat(state_dir: Path | None = None) -> dict | None:
    """Relit le dernier battement de coeur, ou None si absent/corrompu."""
    hb_file = (state_dir or _state_dir()) / _HEARTBEAT_FILENAME
    if not hb_file.exists():
        return None
    try:
        data = json.loads(hb_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def loop_process_alive(state_dir: Path | None = None) -> bool:
    """True si le processus de la boucle existe encore.

    La verification ``/proc/<pid>/cmdline`` ecarte les faux positifs du
    a la reutilisation du PID par un autre processus. Sur un systeme
    sans ``/proc``, on se contente de ``os.kill(pid, 0)``.

    Args:
        state_dir: Repertoire de persistance (surtout pour les tests).

    Returns:
        True si un processus ``agent_loop`` correspondant au PID
        enregistre tourne encore.
    """
    pid = load_loop_pid(state_dir)
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    cmdline = _pid_cmdline(pid)
    if cmdline is None:
        return True
    return "agent_loop" in cmdline


def _pid_cmdline(pid: int) -> str | None:
    """Ligne de commande de ``/proc/<pid>/cmdline`` (None si illisible)."""
    if not _PROC_DIR.exists():
        return None
    try:
        raw = (_PROC_DIR / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore")


def compute_loop_status(target_dir: Path, state_dir: Path | None = None) -> dict:
    """Etat de la boucle pour le tableau de bord.

    Ordre d'evaluation important : le fichier ``DONE`` est examine
    AVANT la vie du processus, pour rendre « arret en cours » tant que
    la boucle n'a pas consomme le kill-switch.

    Args:
        target_dir: Repertoire du projet cible.
        state_dir: Repertoire de persistance (surtout pour les tests).

    Returns:
        Dictionnaire ``{"state": ..., ...}`` avec :
        - ``active`` : boucle vivante (iteration, phase, since_seconds) ;
        - ``stopping`` : DONE pose, boucle encore en vie (arret propre
          a la fin de l'iteration en cours) ;
        - ``completed`` : DONE pose, derniere iteration journalisee
          avec ``mission_completed`` (validee par la session Review) ;
        - ``stopped`` : DONE pose sans validation de mission (kill-switch) ;
        - ``dead`` : boucle morte sans DONE (crash/OOM) ;
        - ``unknown`` : aucune trace de lancement de boucle.
    """
    done = is_done(target_dir)
    alive = loop_process_alive(state_dir)
    heartbeat = read_heartbeat(state_dir)

    if done and alive:
        return {"state": "stopping"}

    if done:
        if _mission_completed(target_dir):
            return {"state": "completed"}
        return {"state": "stopped"}

    if alive:
        status = {"state": "active"}
        if heartbeat:
            status.update(_heartbeat_fields(heartbeat))
        return status

    if heartbeat:
        status = {"state": "dead"}
        status.update(_heartbeat_fields(heartbeat))
        return status

    return {"state": "unknown"}


def _mission_completed(target_dir: Path) -> bool:
    """True si la derniere iteration journalisee a valide la mission."""
    entries = read_entries(target_dir, limit=1)
    if not entries:
        return False
    return bool(entries[-1].get("mission_completed"))


def _heartbeat_fields(heartbeat: dict) -> dict:
    """Champs communs issus du battement de coeur (iteration, anciennete)."""
    fields = {"iteration": heartbeat.get("iteration", 0)}
    phase = heartbeat.get("phase")
    if phase:
        fields["phase"] = phase
    updated_at = heartbeat.get("updated_at")
    if isinstance(updated_at, (int, float)):
        fields["since_seconds"] = max(0, int(time.time() - updated_at))
    return fields
