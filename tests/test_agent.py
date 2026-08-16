"""Tests pour le module agent.py."""

import json
import subprocess
from pathlib import Path

from src.loop.agent import (
    IterationResult,
    _build_prompt,
    _classify_failure,
    _is_no_op,
    _rotate_log_if_large,
    _web_tools_env,
    run_iteration,
)
from src.core.iterations import read_entries
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


def test_build_prompt_mentions_web_research():
    prompt = _build_prompt(
        agents_md="# Objectif",
        progress_md="",
        benchmarks_md="",
        suggestions_md="",
        resources_md="",
    )
    assert "websearch" in prompt
    assert "webfetch" in prompt


def test_web_tools_env_enables_search_backend_and_permissions():
    env = _web_tools_env({})

    assert env["OPENCODE_ENABLE_EXA"] == "1"
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert config["permission"] == {"webfetch": "allow", "websearch": "allow"}


def test_web_tools_env_respects_user_backend_choice():
    env = _web_tools_env({"OPENCODE_WEBSEARCH_PROVIDER": "parallel"})

    assert "OPENCODE_ENABLE_EXA" not in env
    assert "OPENCODE_CONFIG_CONTENT" in env


def test_web_tools_env_merges_existing_inline_config():
    existing = json.dumps(
        {"model": "deepseek/deepseek-v4-pro", "permission": {"websearch": "deny"}}
    )

    env = _web_tools_env({"OPENCODE_CONFIG_CONTENT": existing})

    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert config["model"] == "deepseek/deepseek-v4-pro"
    # Un reglage explicite de l'utilisateur n'est pas ecrase.
    assert config["permission"]["websearch"] == "deny"
    assert config["permission"]["webfetch"] == "allow"


def test_web_tools_env_keeps_invalid_inline_config_untouched():
    env = _web_tools_env({"OPENCODE_CONFIG_CONTENT": "{pas du json"})

    assert "OPENCODE_CONFIG_CONTENT" not in env
    assert env["OPENCODE_ENABLE_EXA"] == "1"


def test_web_tools_env_can_be_disabled():
    assert _web_tools_env({"DEBUILDER_WEB_TOOLS": "0"}) == {}


def test_run_iteration_stops_on_done(tmp_path, monkeypatch):
    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    touch_done(target_dir)

    result = run_iteration(target_dir)
    assert result.continue_loop is False


def test_run_iteration_updates_progress(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test project")

    monkeypatch.setattr(agent_mod, "_run_opencode", _mock_run_opencode)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    result = run_iteration(target_dir)
    assert result.continue_loop is True

    progress = read_state(target_dir, "PROGRESS.md")
    assert "Created main.py" in progress


def test_run_iteration_clears_suggestions(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    write_state(target_dir, "SUGGESTIONS.md", "Use Redis for caching.\n")

    monkeypatch.setattr(agent_mod, "_run_opencode", _mock_run_opencode)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

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
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    result = run_iteration(target_dir)
    assert result.continue_loop is True

    progress = read_state(target_dir, "PROGRESS.md")
    assert "ECHEC" in progress


def test_run_iteration_returns_structured_result(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")

    monkeypatch.setattr(agent_mod, "_run_opencode", _mock_run_opencode)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    result = run_iteration(target_dir, iteration_number=7)

    assert isinstance(result, IterationResult)
    assert result.exit_code == 0
    assert result.failure_type == ""
    assert result.duration_seconds >= 0
    assert result.continue_loop is True
    # Compat avec l'ancien retour booleen.
    assert bool(result) is True


def test_run_iteration_writes_journal_entry(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")

    monkeypatch.setattr(agent_mod, "_run_opencode", _mock_run_opencode)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    run_iteration(target_dir, iteration_number=7)

    entries = read_entries(target_dir)
    assert len(entries) == 1
    assert entries[0]["iteration"] == 7
    assert entries[0]["exit_code"] == 0
    assert entries[0]["failure_type"] == ""
    assert "timestamp" in entries[0]


def test_run_iteration_classifies_timeout(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")

    def mock_timeout(target_dir, prompt):
        return subprocess.CompletedProcess(
            args=["opencode"], returncode=-1, stdout="", stderr="Timeout watchdog"
        )

    monkeypatch.setattr(agent_mod, "_run_opencode", mock_timeout)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    result = run_iteration(target_dir, iteration_number=3)

    assert result.exit_code == -1
    assert result.failure_type == "timeout"
    entries = read_entries(target_dir)
    assert entries[0]["failure_type"] == "timeout"


def test_run_iteration_classifies_api_failure(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")

    def mock_api_error(target_dir, prompt):
        return subprocess.CompletedProcess(
            args=["opencode"],
            returncode=1,
            stdout="",
            stderr="429 too many requests: rate limit depasse",
        )

    monkeypatch.setattr(agent_mod, "_run_opencode", mock_api_error)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    result = run_iteration(target_dir, iteration_number=4)

    assert result.failure_type == "api"


def test_classify_failure_types():
    ok = subprocess.CompletedProcess(args=["x"], returncode=0, stdout="done", stderr="")
    timeout = subprocess.CompletedProcess(args=["x"], returncode=-1, stdout="", stderr="")
    api = subprocess.CompletedProcess(
        args=["x"], returncode=1, stdout="", stderr="invalid_api_key"
    )
    other = subprocess.CompletedProcess(
        args=["x"], returncode=2, stdout="", stderr="syntax error"
    )

    assert _classify_failure(ok) == ""
    assert _classify_failure(timeout) == "timeout"
    assert _classify_failure(api) == "api"
    assert _classify_failure(other) == "error"


def test_is_no_op():
    assert _is_no_op([]) is True
    assert _is_no_op(["PROGRESS.md"]) is True
    assert _is_no_op(["PROGRESS.md", "BENCHMARKS.md", "SUGGESTIONS.md.lock"]) is True
    assert _is_no_op(["src/main.py"]) is False
    assert _is_no_op(["src/main.py", "PROGRESS.md"]) is False


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
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    run_iteration(target_dir)
    assert len(captured_prompts) == 1
    assert "Use async everywhere" in captured_prompts[0]
    assert "justifier ta decision" in captured_prompts[0]
