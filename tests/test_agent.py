"""Tests pour le module agent.py."""

import subprocess
from pathlib import Path

from src.loop.agent import _build_prompt, _rotate_log_if_large, run_iteration
from src.core.state import init_project_state, is_done, read_state, touch_done, write_state


def _mock_run_opencode(target_dir, prompt):
    return subprocess.CompletedProcess(
        args=["opencode"],
        returncode=0,
        stdout="- **Action** : Created main.py\n- **Resultat** : Works",
        stderr="",
    )


def test_build_prompt_basic():
    prompt = _build_prompt(
        agents_md="# Objectif\nBuild a web app.",
        progress_md="## Derniere Iteration (N)\n- Login",
        benchmarks_md="# Benchmarks\n| Model | Score |\n|-------|-------|\n| CNN | 0.9 |",
        suggestions_md="",
        resources_md="",
    )
    assert "Build a web app" in prompt
    assert "Login" in prompt
    assert "CNN" in prompt
    assert "Objectifs et Contexte" in prompt
    assert "Benchmarks" in prompt


def test_build_prompt_with_suggestions():
    prompt = _build_prompt(
        agents_md="# Objectif",
        progress_md="",
        benchmarks_md="",
        suggestions_md="Utilise FastAPI au lieu de Flask.",
        resources_md="",
    )
    assert "FastAPI" in prompt
    assert "Suggestion de l'utilisateur" in prompt
    assert "justifier ta decision" in prompt


def test_build_prompt_with_resources():
    prompt = _build_prompt(
        agents_md="# Objectif",
        progress_md="",
        benchmarks_md="",
        suggestions_md="",
        resources_md="GPU A100 disponible.",
    )
    assert "GPU A100" in prompt
    assert "Ressources disponibles" in prompt


def test_run_iteration_stops_on_done(tmp_path, monkeypatch):
    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    touch_done(target_dir)

    result = run_iteration(target_dir)
    assert result is False


def test_run_iteration_updates_progress(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test project")

    monkeypatch.setattr(agent_mod, "_run_opencode", _mock_run_opencode)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: True)

    result = run_iteration(target_dir)
    assert result is True

    progress = read_state(target_dir, "PROGRESS.md")
    assert "Created main.py" in progress


def test_run_iteration_clears_suggestions(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    write_state(target_dir, "SUGGESTIONS.md", "Use Redis for caching.\n")

    monkeypatch.setattr(agent_mod, "_run_opencode", _mock_run_opencode)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: True)

    run_iteration(target_dir)
    suggestions = read_state(target_dir, "SUGGESTIONS.md")
    assert suggestions == ""


def test_run_iteration_survives_unexpected_exception(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")

    def _boom(target_dir, prompt):
        raise RuntimeError("panne inattendue")

    monkeypatch.setattr(agent_mod, "_run_opencode", _boom)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: True)

    result = run_iteration(target_dir)
    assert result is True

    progress = read_state(target_dir, "PROGRESS.md")
    assert "ECHEC" in progress


def test_rotate_log_if_large_truncates(tmp_path):
    log_file = tmp_path / "OPENCODE_LOG.txt"
    log_file.write_text("=== Iteration old ===\n" + ("x" * 1000) + "\n")

    _rotate_log_if_large(log_file, max_bytes=100)

    content = log_file.read_text()
    assert len(content) < 1000
    assert "tronque" in content


def test_rotate_log_if_large_noop_when_small(tmp_path):
    log_file = tmp_path / "OPENCODE_LOG.txt"
    log_file.write_text("small content")

    _rotate_log_if_large(log_file, max_bytes=100)

    assert log_file.read_text() == "small content"


def test_run_iteration_with_suggestions_in_prompt(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    write_state(target_dir, "SUGGESTIONS.md", "Use async everywhere.\n")

    captured_prompts = []
    def mock_opencode(target_dir, prompt):
        captured_prompts.append(prompt)
        return _mock_run_opencode(target_dir, prompt)

    monkeypatch.setattr(agent_mod, "_run_opencode", mock_opencode)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: True)

    run_iteration(target_dir)
    assert len(captured_prompts) == 1
    assert "Use async everywhere" in captured_prompts[0]
    assert "justifier ta decision" in captured_prompts[0]
