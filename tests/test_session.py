"""Tests pour le module session.py (reprise de session GUI)."""

from pathlib import Path

from src.core.session import clear_last_session, load_last_session, save_last_session


def test_load_last_session_returns_none_when_absent(tmp_path: Path):
    assert load_last_session(state_dir=tmp_path) is None


def test_save_and_load_last_session(tmp_path: Path):
    state_dir = tmp_path / "state"
    target_dir = tmp_path / "project"
    target_dir.mkdir()
    (target_dir / "AGENTS.md").write_text("# Objectifs")

    save_last_session(target_dir, state_dir=state_dir)
    loaded = load_last_session(state_dir=state_dir)

    assert loaded == target_dir


def test_load_last_session_ignores_deleted_project(tmp_path: Path):
    state_dir = tmp_path / "state"
    target_dir = tmp_path / "project"
    target_dir.mkdir()
    (target_dir / "AGENTS.md").write_text("# Objectifs")

    save_last_session(target_dir, state_dir=state_dir)

    (target_dir / "AGENTS.md").unlink()

    assert load_last_session(state_dir=state_dir) is None


def test_clear_last_session(tmp_path: Path):
    state_dir = tmp_path / "state"
    target_dir = tmp_path / "project"
    target_dir.mkdir()
    (target_dir / "AGENTS.md").write_text("# Objectifs")

    save_last_session(target_dir, state_dir=state_dir)
    clear_last_session(state_dir=state_dir)

    assert load_last_session(state_dir=state_dir) is None


def test_clear_last_session_noop_when_absent(tmp_path: Path):
    clear_last_session(state_dir=tmp_path / "state")
