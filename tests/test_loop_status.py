"""Tests de l'etat de vie de la boucle (src/core/loop_status.py).

Le tableau de bord doit distinguer boucle vivante / en cours d'arret /
mission terminee / arretee manuellement / morte, uniquement a partir
des fichiers persistants (PID, heartbeat, DONE, journal).
"""

import time
from pathlib import Path

import src.core.loop_status as loop_status
from src.core.iterations import append_entry
from src.core.loop_status import (
    compute_loop_status,
    load_loop_pid,
    loop_process_alive,
    read_heartbeat,
    save_loop_pid,
    write_heartbeat,
)


# --- Persistance PID ----------------------------------------------------------


def test_save_and_load_loop_pid(tmp_path: Path):
    save_loop_pid(4242, state_dir=tmp_path)
    assert load_loop_pid(state_dir=tmp_path) == 4242


def test_load_loop_pid_none_when_absent(tmp_path: Path):
    assert load_loop_pid(state_dir=tmp_path) is None


def test_load_loop_pid_none_when_corrupt(tmp_path: Path):
    (tmp_path / "loop_pid.txt").write_text("pas-un-nombre")
    assert load_loop_pid(state_dir=tmp_path) is None


# --- Heartbeat ----------------------------------------------------------------


def test_heartbeat_roundtrip(tmp_path: Path):
    write_heartbeat(7, "iteration", state_dir=tmp_path)
    hb = read_heartbeat(state_dir=tmp_path)
    assert hb is not None
    assert hb["iteration"] == 7
    assert hb["phase"] == "iteration"
    assert abs(time.time() - hb["updated_at"]) < 5


def test_read_heartbeat_none_when_absent(tmp_path: Path):
    assert read_heartbeat(state_dir=tmp_path) is None


def test_read_heartbeat_none_when_corrupt(tmp_path: Path):
    (tmp_path / "heartbeat.json").write_text("{corrompu")
    assert read_heartbeat(state_dir=tmp_path) is None


# --- Vie du processus -----------------------------------------------------------


def test_loop_process_alive_false_without_pid(tmp_path: Path):
    assert loop_process_alive(state_dir=tmp_path) is False


def _fake_current_process(monkeypatch):
    """Fait croire que le PID enregistre est celui du process de test."""
    import os

    monkeypatch.setattr(loop_status, "load_loop_pid", lambda sd=None: os.getpid())


def test_loop_process_alive_true_when_cmdline_matches(tmp_path: Path, monkeypatch):
    _fake_current_process(monkeypatch)
    monkeypatch.setattr(loop_status, "_pid_cmdline", lambda pid: "/bin/bash agent_loop.sh")
    assert loop_process_alive(state_dir=tmp_path) is True


def test_loop_process_alive_false_on_pid_reuse(tmp_path: Path, monkeypatch):
    _fake_current_process(monkeypatch)
    monkeypatch.setattr(loop_status, "_pid_cmdline", lambda pid: "python3 -m pytest")
    assert loop_process_alive(state_dir=tmp_path) is False


def test_loop_process_alive_true_when_cmdline_unreadable(tmp_path: Path, monkeypatch):
    _fake_current_process(monkeypatch)
    monkeypatch.setattr(loop_status, "_pid_cmdline", lambda pid: None)
    assert loop_process_alive(state_dir=tmp_path) is True


def test_loop_process_alive_false_when_process_gone(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loop_status, "load_loop_pid", lambda sd=None: 999999)
    assert loop_process_alive(state_dir=tmp_path) is False


# --- Statut composite ------------------------------------------------------------


def _setup(target_dir: Path, monkeypatch, alive: bool, heartbeat=None, done=False):
    monkeypatch.setattr(loop_status, "loop_process_alive", lambda sd=None: alive)
    monkeypatch.setattr(
        loop_status,
        "read_heartbeat",
        lambda sd=None: heartbeat,
    )
    if done:
        (target_dir / "DONE").touch()
    return target_dir


def test_status_unknown_without_any_signal(tmp_path: Path, monkeypatch):
    status = compute_loop_status(_setup(tmp_path, monkeypatch, alive=False))
    assert status["state"] == "unknown"


def test_status_active_with_heartbeat(tmp_path: Path, monkeypatch):
    now = int(time.time())
    status = compute_loop_status(
        _setup(
            tmp_path,
            monkeypatch,
            alive=True,
            heartbeat={"iteration": 12, "phase": "iteration", "updated_at": now - 300},
        )
    )
    assert status["state"] == "active"
    assert status["iteration"] == 12
    assert status["since_seconds"] == 300


def test_status_stopping_when_done_and_alive(tmp_path: Path, monkeypatch):
    status = compute_loop_status(_setup(tmp_path, monkeypatch, alive=True, done=True))
    assert status["state"] == "stopping"


def test_status_completed_when_mission_validated(tmp_path: Path, monkeypatch):
    append_entry(tmp_path, {"iteration": 3, "mission_completed": True})
    status = compute_loop_status(_setup(tmp_path, monkeypatch, alive=False, done=True))
    assert status["state"] == "completed"


def test_status_stopped_when_done_without_validation(tmp_path: Path, monkeypatch):
    append_entry(tmp_path, {"iteration": 3, "mission_completed": False})
    status = compute_loop_status(_setup(tmp_path, monkeypatch, alive=False, done=True))
    assert status["state"] == "stopped"


def test_status_stopped_when_done_without_journal(tmp_path: Path, monkeypatch):
    status = compute_loop_status(_setup(tmp_path, monkeypatch, alive=False, done=True))
    assert status["state"] == "stopped"


def test_status_dead_when_heartbeat_without_process(tmp_path: Path, monkeypatch):
    status = compute_loop_status(
        _setup(
            tmp_path,
            monkeypatch,
            alive=False,
            heartbeat={"iteration": 9, "phase": "iteration", "updated_at": int(time.time())},
        )
    )
    assert status["state"] == "dead"
    assert status["iteration"] == 9
