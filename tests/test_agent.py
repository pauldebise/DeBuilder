"""Tests pour le module agent.py (boucle Plan/Implement)."""

import json
import subprocess
import sys
from pathlib import Path

from src.loop.agent import (
    IterationResult,
    _build_implement_prompt,
    _build_plan_prompt,
    _classify_failure,
    _is_no_op,
    _recovery_section,
    _rotate_log_if_large,
    _web_tools_env,
    compute_backoff,
    run_iteration,
)
from src.core.iterations import append_entry, read_entries
from src.core.state import (
    init_project_state,
    read_state,
    touch_done,
    update_progress,
    write_state,
)

# --- Mocks des deux sessions ------------------------------------------------

_PLAN_RESPONSE = (
    "```TASK\n"
    "# Tache de l'Iteration\n\n"
    "## Objectif\n\nImplementer main.py.\n\n"
    "## Criteres d'Acceptation\n\n- [ ] main.py existe\n\n"
    "## Commande de Test\n\n"
    "## Sous-Taches\n\n- [ ] creer main.py\n"
    "```\n"
    "```PLAN\n"
    "# Plan de Developpement\n\n"
    "## Backlog (par priorite)\n\n- [ ] tache suivante\n\n"
    "## Terminees\n\n- [x] creer main.py\n"
    "```\n"
)


def _plan_response_with_test_cmd(cmd: str) -> str:
    return _PLAN_RESPONSE.replace(
        "## Commande de Test\n\n", f"## Commande de Test\n\n{cmd}\n\n"
    )


def _plan_ok(target_dir, prompt, model=None, read_only=False):
    return subprocess.CompletedProcess(
        args=["opencode"], returncode=0, stdout=_PLAN_RESPONSE, stderr=""
    )


def _implement_ok(target_dir, prompt, model=None, read_only=False):
    task = read_state(target_dir, "TASK.md")
    write_state(target_dir, "TASK.md", task.replace("- [ ]", "- [x]"))
    update_progress(
        target_dir,
        "- **Action realisee** : main.py cree\n"
        "- **Resultat** : OK\n"
        "- **Problemes rencontres** : Suggestion utilisateur : rejetee "
        "(non pertinente pour l'instant)\n",
    )
    return subprocess.CompletedProcess(
        args=["opencode"], returncode=0, stdout="travail implemente", stderr=""
    )


def _implement_checks_boxes_only(target_dir, prompt, model=None, read_only=False):
    """Coche les cases sans toucher a PROGRESS.md (gate en echec attendue)."""
    task = read_state(target_dir, "TASK.md")
    write_state(target_dir, "TASK.md", task.replace("- [ ]", "- [x]"))
    return subprocess.CompletedProcess(
        args=["opencode"], returncode=0, stdout="cases cochees", stderr=""
    )


def _implement_updates_progress_only(target_dir, prompt, model=None, read_only=False):
    """Met a jour PROGRESS.md sans cocher les cases (gate en echec attendue)."""
    update_progress(target_dir, "- **Action realisee** : travail\n- **Resultat** : OK\n")
    return subprocess.CompletedProcess(
        args=["opencode"], returncode=0, stdout="progression ecrite", stderr=""
    )


def _implement_timeout(target_dir, prompt, model=None, read_only=False):
    return subprocess.CompletedProcess(
        args=["opencode"], returncode=-1, stdout="", stderr="Timeout watchdog"
    )


_FINISHED_REPORT_CLAIMED = (
    "# Rapport de Fin de Mission\n\n"
    "## Checklist du Cahier des Charges\n\n"
    "- [x] item 1 realise et teste\n\n"
    "## Validation par les Tests\n\n"
    "python -m pytest -q : OK\n"
)

_REVIEW_ACCEPT = "```VERDICT\nACCEPTE\n```\n"
_REVIEW_REFUSE = "```VERDICT\nREFUSE\nil manque les tests de bout en bout\n```\n"


def _review_accept(target_dir, prompt, model=None, read_only=False):
    return subprocess.CompletedProcess(
        args=["opencode"], returncode=0, stdout=_REVIEW_ACCEPT, stderr=""
    )


def _review_refuse(target_dir, prompt, model=None, read_only=False):
    return subprocess.CompletedProcess(
        args=["opencode"], returncode=0, stdout=_REVIEW_REFUSE, stderr=""
    )


def _implement_claim_finished(target_dir, prompt, model=None, read_only=False):
    task = read_state(target_dir, "TASK.md")
    write_state(target_dir, "TASK.md", task.replace("- [ ]", "- [x]"))
    update_progress(
        target_dir, "- **Action realisee** : mission terminee\n- **Resultat** : OK\n"
    )
    write_state(target_dir, "FINISHED_REPORT.md", _FINISHED_REPORT_CLAIMED)
    return subprocess.CompletedProcess(
        args=["opencode"], returncode=0, stdout="fini", stderr=""
    )


def _make_session_mock(*fns):
    calls = {"count": 0, "prompts": [], "read_only": [], "models": []}

    def mock(target_dir, prompt, model=None, read_only=False):
        calls["count"] += 1
        calls["prompts"].append(prompt)
        calls["read_only"].append(read_only)
        calls["models"].append(model)
        fn = fns[min(calls["count"] - 1, len(fns) - 1)]
        return fn(target_dir, prompt, model=model, read_only=read_only)

    return mock, calls


def _make_dual_mock(plan_fn, implement_fn):
    return _make_session_mock(plan_fn, implement_fn)


def _dual_ok():
    return _make_dual_mock(_plan_ok, _implement_ok)


def _git(repo_dir: Path, *args: str) -> None:
    subprocess.run(
        ["git"] + list(args),
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
    )


def _init_git_repo(repo_dir: Path) -> None:
    from src.core.git import ensure_gitignore, init_repo

    assert init_repo(repo_dir)
    ensure_gitignore(repo_dir)
    _git(repo_dir, "config", "user.email", "test@test.com")
    _git(repo_dir, "config", "user.name", "Test")


# --- Construction des prompts -----------------------------------------------


def test_plan_prompt_contains_cahier_des_charges():
    prompt = _build_plan_prompt(
        agents_md="# Objectif\nBuild a web app.",
        progress_md="",
        plan_md="- [ ] tache 1",
        spec_md="| item | fichier | test |",
        gate_state="Gates OK.",
    )
    assert "Build a web app" in prompt
    assert "tache 1" in prompt
    assert "Gates OK" in prompt
    assert "```TASK" in prompt
    assert "```PLAN" in prompt


def test_implement_prompt_contains_task_and_context():
    prompt = _build_implement_prompt(
        task_md="# Tache\n\n## Sous-Taches\n\n- [ ] creer main.py",
        progress_md="## Derniere Iteration (N)\n- Login",
        benchmarks_md="# Benchmarks\n| CNN | 0.9 |",
        arch_md="stack: Python",
        suggestions_md="",
        resources_md="",
    )
    assert "creer main.py" in prompt
    assert "Login" in prompt
    assert "CNN" in prompt
    assert "stack: Python" in prompt
    assert "Contrat de Tache" in prompt


def test_implement_prompt_with_suggestions():
    prompt = _build_implement_prompt(
        task_md="",
        progress_md="",
        benchmarks_md="",
        arch_md="",
        suggestions_md="Utilise FastAPI au lieu de Flask.",
        resources_md="",
    )
    assert "FastAPI" in prompt
    assert "Suggestion de l'utilisateur" in prompt
    assert "justifier ta decision" in prompt


def test_implement_prompt_with_resources():
    prompt = _build_implement_prompt(
        task_md="",
        progress_md="",
        benchmarks_md="",
        arch_md="",
        suggestions_md="",
        resources_md="GPU A100 disponible.",
    )
    assert "GPU A100" in prompt
    assert "Ressources disponibles" in prompt


def test_implement_prompt_mentions_web_research_and_commits():
    prompt = _build_implement_prompt(
        task_md="",
        progress_md="",
        benchmarks_md="",
        arch_md="",
        suggestions_md="",
        resources_md="",
    )
    assert "websearch" in prompt
    assert "webfetch" in prompt
    assert "Conventional Commits" in prompt
    assert "DERNIERE ETAPE OBLIGATOIRE" in prompt


# --- Permissions des sessions ------------------------------------------------


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


def test_web_tools_env_read_only_forces_write_deny():
    existing = json.dumps({"permission": {"bash": "allow", "edit": "allow"}})

    env = _web_tools_env({"OPENCODE_CONFIG_CONTENT": existing}, read_only=True)

    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    # Les refus de lecture seule ecrasent meme un allow explicite.
    assert config["permission"]["bash"] == "deny"
    assert config["permission"]["edit"] == "deny"
    assert config["permission"]["write"] == "deny"
    assert config["permission"]["webfetch"] == "allow"


def test_web_tools_env_read_only_without_web_tools():
    env = _web_tools_env({"DEBUILDER_WEB_TOOLS": "0"}, read_only=True)

    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert config["permission"]["bash"] == "deny"
    assert config["permission"]["write"] == "deny"


# --- run_iteration -----------------------------------------------------------


def test_run_iteration_stops_on_done(tmp_path, monkeypatch):
    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    touch_done(target_dir)

    result = run_iteration(target_dir)
    assert result.continue_loop is False


def test_run_iteration_synthesizes_progress_via_llm(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test project")

    def _implement_passthrough(target_dir, prompt, model=None, read_only=False):
        return subprocess.CompletedProcess(
            args=["opencode"],
            returncode=0,
            stdout="- **Action** : Created main.py\n- **Resultat** : Works",
            stderr="",
        )

    synthesized = (
        "- **Action realisee** : main.py cree\n"
        "- **Resultat** : OK\n"
        "- **Problemes rencontres** : aucun\n"
        "- **Solutions envisagees** : suite\n"
    )
    monkeypatch.setattr(
        agent_mod, "synthesize_progress_entry", lambda **kwargs: synthesized
    )

    mock, _ = _make_dual_mock(_plan_ok, _implement_passthrough)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    result = run_iteration(target_dir)
    assert result.continue_loop is True

    progress = read_state(target_dir, "PROGRESS.md")
    assert "main.py cree" in progress
    # La sortie brute n'est JAMAIS injectee telle quelle.
    assert "Created main.py" not in progress


def test_run_iteration_heuristic_fallback_without_raw_stdout(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test project")

    def _implement_passthrough(target_dir, prompt, model=None, read_only=False):
        return subprocess.CompletedProcess(
            args=["opencode"],
            returncode=0,
            stdout="sortie brute tres verbeuse a ne jamais injecter",
            stderr="",
        )

    monkeypatch.setattr(agent_mod, "synthesize_progress_entry", lambda **kwargs: None)

    mock, _ = _make_dual_mock(_plan_ok, _implement_passthrough)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    run_iteration(target_dir)

    progress = read_state(target_dir, "PROGRESS.md")
    assert "Non consigne par l'agent" in progress
    assert "sortie brute tres verbeuse" not in progress


def test_run_iteration_survives_unexpected_exception(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")

    def _boom(target_dir, prompt, model=None, read_only=False):
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

    mock, _ = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)
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

    mock, _ = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)
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

    def mock_timeout(target_dir, prompt, model=None, read_only=False):
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

    def mock_api_error(target_dir, prompt, model=None, read_only=False):
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
    empty = subprocess.CompletedProcess(args=["x"], returncode=0, stdout="", stderr="")

    assert _classify_failure(ok) == ""
    assert _classify_failure(timeout) == "timeout"
    assert _classify_failure(api) == "api"
    assert _classify_failure(other) == "error"
    assert _classify_failure(empty) == "empty"


def test_is_no_op():
    assert _is_no_op([]) is True
    assert _is_no_op(["PROGRESS.md"]) is True
    assert _is_no_op(["PROGRESS.md", "BENCHMARKS.md", "SUGGESTIONS.md.lock"]) is True
    assert _is_no_op(["TASK.md", "PLAN.md", "ARCHITECTURE.md"]) is True
    assert _is_no_op(["src/main.py"]) is False
    assert _is_no_op(["src/main.py", "PROGRESS.md"]) is False


def test_compute_backoff():
    assert compute_backoff(2, failed=False) == 2.0
    assert compute_backoff(2, failed=True) == 4.0
    assert compute_backoff(4, failed=True) == 8.0
    assert compute_backoff(256, failed=True, cap=300) == 300.0
    assert compute_backoff(300, failed=True, cap=300) == 300.0


# --- Session Plan : lecture seule et materialisation --------------------------


def test_plan_session_is_read_only_and_implement_is_not(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")

    mock, calls = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    run_iteration(target_dir)

    assert calls["count"] == 2
    assert calls["read_only"] == [True, False]


def test_plan_outputs_are_materialized(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")

    mock, _ = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    run_iteration(target_dir)

    task_md = read_state(target_dir, "TASK.md")
    assert "## Objectif" in task_md
    assert "Implementer main.py" in task_md
    assert "- [x] creer main.py" in task_md  # cochees par la session Implement

    plan_md = read_state(target_dir, "PLAN.md")
    assert "## Backlog" in plan_md
    assert "tache suivante" in plan_md


def test_missing_task_block_fails_plan(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")

    def _plan_without_block(target_dir, prompt, model=None, read_only=False):
        return subprocess.CompletedProcess(
            args=["opencode"], returncode=0, stdout="aucun contrat ici", stderr=""
        )

    mock, calls = _make_dual_mock(_plan_without_block, _implement_ok)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    result = run_iteration(target_dir, iteration_number=2)

    assert result.failure_type == "plan"
    assert calls["count"] == 1  # Implement jamais lance
    assert "ECHEC (plan)" in read_state(target_dir, "PROGRESS.md")


# --- Gates deterministes -------------------------------------------------------


def test_gate_failure_when_boxes_unchecked(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    mock, _ = _make_dual_mock(_plan_ok, _implement_updates_progress_only)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    result = run_iteration(target_dir, iteration_number=1)

    assert result.failure_type == "gate"
    assert result.gate_failures
    gate_md = read_state(target_dir, "GATE_FAILURE.md")
    assert "cases" in gate_md
    entries = read_entries(target_dir)
    assert entries[0]["gate_failures"]


def test_gate_failure_when_progress_unchanged(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    mock, _ = _make_dual_mock(_plan_ok, _implement_checks_boxes_only)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    result = run_iteration(target_dir, iteration_number=1)

    assert result.failure_type == "gate"
    gate_md = read_state(target_dir, "GATE_FAILURE.md")
    assert "PROGRESS.md" in gate_md


def test_gate_failure_file_cleared_after_success(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    failing_mock, _ = _make_dual_mock(_plan_ok, _implement_checks_boxes_only)
    monkeypatch.setattr(agent_mod, "_run_opencode", failing_mock)
    run_iteration(target_dir, iteration_number=1)
    assert (target_dir / "GATE_FAILURE.md").exists()

    ok_mock, _ = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", ok_mock)
    result = run_iteration(target_dir, iteration_number=2)

    assert result.failure_type == ""
    assert not (target_dir / "GATE_FAILURE.md").exists()


def test_gates_pass_with_complete_implement(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    mock, _ = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    result = run_iteration(target_dir, iteration_number=1)

    assert result.failure_type == ""
    assert result.gate_failures == []
    assert not (target_dir / "GATE_FAILURE.md").exists()


# --- Gate de tests (phase 2, pilotee par la commande de TASK.md) --------------


def test_run_iteration_gate_failing_tests(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)
    (target_dir / "test_sample.py").write_text("def test_ko():\n    assert False\n")

    def _plan_with_cmd(target_dir, prompt, model=None, read_only=False):
        return subprocess.CompletedProcess(
            args=["opencode"],
            returncode=0,
            stdout=_plan_response_with_test_cmd(f"{sys.executable} -m pytest -q"),
            stderr="",
        )

    mock, _ = _make_dual_mock(_plan_with_cmd, _implement_ok)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    result = run_iteration(target_dir, iteration_number=3)

    assert result.tests_passed is False
    assert result.failure_type == "tests"
    progress = read_state(target_dir, "PROGRESS.md")
    assert "ECHEC (tests)" in progress
    entries = read_entries(target_dir)
    assert entries[0]["tests_passed"] is False
    assert entries[0]["failure_type"] == "tests"
    assert entries[0]["tests"]["failures"] == 1


def test_run_iteration_gate_passing_tests(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)
    (target_dir / "test_sample.py").write_text("def test_ok():\n    assert True\n")

    def _plan_with_cmd(target_dir, prompt, model=None, read_only=False):
        return subprocess.CompletedProcess(
            args=["opencode"],
            returncode=0,
            stdout=_plan_response_with_test_cmd(f"{sys.executable} -m pytest -q"),
            stderr="",
        )

    mock, _ = _make_dual_mock(_plan_with_cmd, _implement_ok)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    result = run_iteration(target_dir, iteration_number=4)

    assert result.tests_passed is True
    assert result.failure_type == ""
    assert "ECHEC (tests)" not in read_state(target_dir, "PROGRESS.md")
    assert read_entries(target_dir)[0]["tests_passed"] is True


def test_run_iteration_gate_ignored_without_command(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    mock, _ = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)
    monkeypatch.delenv("DEBUILDER_TEST_CMD", raising=False)

    result = run_iteration(target_dir, iteration_number=1)

    assert result.tests_passed is None
    assert "tests_passed" not in read_entries(target_dir)[0]


def test_run_iteration_gate_skipped_on_session_failure(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    mock, _ = _make_dual_mock(_plan_ok, _implement_timeout)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    result = run_iteration(target_dir, iteration_number=5)

    assert result.failure_type == "timeout"
    assert result.tests_passed is None


# --- Purge de SUGGESTIONS.md (cdc §4.4) ---------------------------------------


def test_suggestions_cleared_with_justification(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    write_state(target_dir, "SUGGESTIONS.md", "Use Redis for caching.\n")

    mock, _ = _dual_ok()  # _implement_ok justifie "rejetee"
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    run_iteration(target_dir)

    assert read_state(target_dir, "SUGGESTIONS.md") == ""


def test_suggestions_kept_without_justification(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    write_state(target_dir, "SUGGESTIONS.md", "Use Redis for caching.\n")

    def _implement_no_justification(target_dir, prompt, model=None, read_only=False):
        task = read_state(target_dir, "TASK.md")
        write_state(target_dir, "TASK.md", task.replace("- [ ]", "- [x]"))
        update_progress(target_dir, "- **Action realisee** : OK\n- **Resultat** : OK\n")
        return subprocess.CompletedProcess(
            args=["opencode"], returncode=0, stdout="fait", stderr=""
        )

    mock, _ = _make_dual_mock(_plan_ok, _implement_no_justification)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    run_iteration(target_dir)

    assert "Use Redis for caching" in read_state(target_dir, "SUGGESTIONS.md")


def test_suggestions_kept_on_failure(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    write_state(target_dir, "SUGGESTIONS.md", "Use Redis for caching.\n")

    mock, _ = _make_dual_mock(_plan_ok, _implement_timeout)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    run_iteration(target_dir)

    assert "Use Redis for caching" in read_state(target_dir, "SUGGESTIONS.md")


# --- Circuit breaker ------------------------------------------------------------


def test_run_iteration_uses_fallback_model_when_tripped(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod
    from src.core.circuit_breaker import CircuitBreaker

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    monkeypatch.setenv("DEBUILDER_CB_PAUSE_SECONDS", "0")
    monkeypatch.setenv("DEBUILDER_MODEL_FALLBACK", "deepseek/fallback-model")

    state_dir = tmp_path / "state"
    monkeypatch.setenv("DEBUILDER_STATE_DIR", str(state_dir))
    breaker = CircuitBreaker(state_dir=state_dir)
    for _ in range(breaker.max_failures):
        breaker.record_failure("api")

    mock, calls = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    run_iteration(target_dir, iteration_number=1)

    assert calls["models"] == ["deepseek/fallback-model", "deepseek/fallback-model"]


def test_run_iteration_resets_breaker_on_success(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod
    from src.core.circuit_breaker import CircuitBreaker

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    monkeypatch.setenv("DEBUILDER_CB_PAUSE_SECONDS", "0")
    state_dir = tmp_path / "state"
    monkeypatch.setenv("DEBUILDER_STATE_DIR", str(state_dir))
    breaker = CircuitBreaker(state_dir=state_dir)
    for _ in range(breaker.max_failures):
        breaker.record_failure("api")
    assert breaker.to_dict()["tripped"] is True

    mock, _ = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    run_iteration(target_dir, iteration_number=1)

    assert CircuitBreaker(state_dir=state_dir).to_dict()["tripped"] is False


def test_run_iteration_feeds_breaker_on_api_failure(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod
    from src.core.circuit_breaker import CircuitBreaker

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    state_dir = tmp_path / "state"
    monkeypatch.setenv("DEBUILDER_STATE_DIR", str(state_dir))

    def _mock_api_error(target_dir, prompt, model=None, read_only=False):
        return subprocess.CompletedProcess(
            args=["opencode"],
            returncode=1,
            stdout="",
            stderr="429 too many requests",
        )

    monkeypatch.setattr(agent_mod, "_run_opencode", _mock_api_error)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    run_iteration(target_dir, iteration_number=1)

    breaker = CircuitBreaker(state_dir=state_dir)
    assert breaker.to_dict()["api_failures"] == 1
    assert breaker.to_dict()["last_failure_type"] == "api"


# --- Tags ----------------------------------------------------------------------


def test_run_iteration_tags_after_effective_commit(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod
    from src.core.git import list_iteration_tags

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    mock, _ = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    result = run_iteration(target_dir, iteration_number=1)

    assert result.tags == ["debuilder/iter-0001"]
    assert list_iteration_tags(target_dir) == ["debuilder/iter-0001"]


def test_run_iteration_no_tag_when_head_unchanged(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod
    from src.core.git import list_iteration_tags

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    mock, _ = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)
    # Simule l'absence de commit effectif : HEAD ne bouge pas.
    monkeypatch.setattr(agent_mod, "head_commit", lambda d: "samesha")

    result = run_iteration(target_dir, iteration_number=1)

    assert result.tags == []
    assert list_iteration_tags(target_dir) == []


# --- Reprise apres echec ----------------------------------------------------------


def test_recovery_section_empty_when_no_previous_iteration(tmp_path):
    assert _recovery_section(tmp_path) == ""


def test_recovery_section_empty_after_success(tmp_path):
    append_entry(tmp_path, {"iteration": 1, "exit_code": 0, "failure_type": ""})

    assert _recovery_section(tmp_path) == ""


def test_recovery_section_adapts_message_per_type(tmp_path):
    (tmp_path / "OPENCODE_LOG.txt").write_text("ligne de travail\ninterrompue ici\n")

    for failure_type in ("timeout", "api", "empty", "error", "exception"):
        append_entry(
            tmp_path,
            {"iteration": 1, "exit_code": 1, "failure_type": failure_type},
        )
        section = _recovery_section(tmp_path)
        assert "interrompue ici" in section

    append_entry(tmp_path, {"iteration": 9, "exit_code": 0, "failure_type": "tests"})
    section = _recovery_section(tmp_path)
    assert "gate de tests" in section
    assert "interrompue ici" not in section

    append_entry(tmp_path, {"iteration": 10, "exit_code": 0, "failure_type": "gate"})
    section = _recovery_section(tmp_path)
    assert "GATE_FAILURE.md" in section


def test_recovery_section_uses_only_last_entry(tmp_path):
    append_entry(tmp_path, {"iteration": 1, "exit_code": 1, "failure_type": "timeout"})
    append_entry(tmp_path, {"iteration": 2, "exit_code": 0, "failure_type": ""})

    assert _recovery_section(tmp_path) == ""


def test_run_iteration_injects_recovery_after_failure(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)
    (target_dir / "OPENCODE_LOG.txt").write_text(
        "=== Iteration ===\ntravail interrompu\ncode a moitie ecrit\n"
    )

    failing_mock, _ = _make_dual_mock(_plan_ok, _implement_timeout)
    monkeypatch.setattr(agent_mod, "_run_opencode", failing_mock)

    run_iteration(target_dir, iteration_number=1)

    ok_mock, calls = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", ok_mock)

    run_iteration(target_dir, iteration_number=2)

    assert calls["count"] == 2
    assert "Reprise apres echec" in calls["prompts"][1]
    assert "garde-fou de temps" in calls["prompts"][1]
    assert "travail interrompu" in calls["prompts"][1]
    # La session Plan, elle, recois l'etat des gates, pas le transcript.
    assert "Reprise apres echec" not in calls["prompts"][0]


def test_run_iteration_no_recovery_after_success(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    first_mock, _ = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", first_mock)
    run_iteration(target_dir, iteration_number=1)

    second_mock, calls = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", second_mock)
    run_iteration(target_dir, iteration_number=2)

    assert "Reprise apres echec" not in calls["prompts"][1]


# --- Suggestions dans le prompt ----------------------------------------------------


def test_run_iteration_with_suggestions_in_prompt(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    write_state(target_dir, "SUGGESTIONS.md", "Use async everywhere.\n")

    mock, calls = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    run_iteration(target_dir)
    assert len(calls["prompts"]) == 2
    # La suggestion va a la session Implement, pas a la session Plan.
    assert "Use async everywhere" in calls["prompts"][1]
    assert "justifier ta decision" in calls["prompts"][1]


# --- Rotation du log ----------------------------------------------------------------


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


# --- Fin de mission : revendication, review, DONE -------------------------------


def test_claim_finished_false_on_template(tmp_path):
    from src.loop.agent import _claim_finished

    init_project_state(tmp_path, instructions="Test")

    assert _claim_finished(tmp_path) is False


def test_claim_finished_true_when_all_checked(tmp_path):
    from src.loop.agent import _claim_finished

    write_state(tmp_path, "FINISHED_REPORT.md", _FINISHED_REPORT_CLAIMED)

    assert _claim_finished(tmp_path) is True


def test_claim_finished_false_when_partial(tmp_path):
    from src.loop.agent import _claim_finished

    write_state(
        tmp_path,
        "FINISHED_REPORT.md",
        "# Rapport\n\n## Checklist du Cahier des Charges\n\n- [x] a\n- [ ] b\n",
    )

    assert _claim_finished(tmp_path) is False


def test_mission_validated_creates_done(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    mock, calls = _make_session_mock(_plan_ok, _implement_claim_finished, _review_accept)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    result = run_iteration(target_dir, iteration_number=1)

    assert calls["count"] == 3
    assert result.mission_completed is True
    assert result.continue_loop is False
    assert (target_dir / "DONE").exists()
    assert read_entries(target_dir)[0]["mission_completed"] is True


def test_review_refusal_writes_feedback_and_continues(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    mock, _ = _make_session_mock(_plan_ok, _implement_claim_finished, _review_refuse)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    result = run_iteration(target_dir, iteration_number=1)

    assert result.mission_completed is False
    assert result.continue_loop is True
    assert not (target_dir / "DONE").exists()
    assert result.failure_type == "review"
    assert "tests de bout en bout" in read_state(target_dir, "REVIEW.md")


def test_review_session_is_read_only(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    mock, calls = _make_session_mock(_plan_ok, _implement_claim_finished, _review_accept)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    run_iteration(target_dir)

    assert calls["read_only"] == [True, False, True]


def test_review_not_run_without_claim(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    mock, calls = _make_session_mock(_plan_ok, _implement_ok, _review_accept)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    run_iteration(target_dir)

    assert calls["count"] == 2


def test_review_session_failure_keeps_loop_alive(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    mock, _ = _make_session_mock(_plan_ok, _implement_claim_finished, _implement_timeout)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    result = run_iteration(target_dir, iteration_number=1)

    assert result.mission_completed is False
    assert result.continue_loop is True
    assert not (target_dir / "DONE").exists()


# --- Detection de no-op ---------------------------------------------------------


def test_count_consecutive_noops(tmp_path):
    from src.loop.agent import _count_consecutive_noops

    assert _count_consecutive_noops(tmp_path) == 0

    append_entry(tmp_path, {"no_op": True})
    append_entry(tmp_path, {"no_op": True})
    assert _count_consecutive_noops(tmp_path) == 2

    append_entry(tmp_path, {"no_op": False})
    assert _count_consecutive_noops(tmp_path) == 0


def test_max_noops_warns_in_plan_prompt(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)
    for i in range(3):
        append_entry(target_dir, {"iteration": i + 1, "no_op": True})

    mock, calls = _make_session_mock(_plan_ok, _implement_ok)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    run_iteration(target_dir, iteration_number=4)

    assert "no-op consecutives" in calls["prompts"][0]


def test_max_noops_triggers_notification_and_failure(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)

    monkeypatch.setenv("DEBUILDER_MAX_NOOPS", "1")
    webhook_calls = []
    monkeypatch.setattr(
        agent_mod, "_notify_webhook", lambda payload: webhook_calls.append(payload)
    )
    monkeypatch.setattr(agent_mod, "status_files", lambda d: [])
    monkeypatch.setattr(agent_mod, "stage_and_commit_all", lambda d, m: (True, ""))

    mock, _ = _make_session_mock(_plan_ok, _implement_ok)
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    result = run_iteration(target_dir, iteration_number=1)

    assert result.no_op is True
    assert result.failure_type == "noop"
    assert "ECHEC (no-op)" in read_state(target_dir, "PROGRESS.md")
    assert webhook_calls[0]["event"] == "max_noops_reached"


# --- Caps durs -------------------------------------------------------------------


def test_record_cap_stop_writes_progress_and_webhook(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    webhook_calls = []
    monkeypatch.setattr(
        agent_mod, "_notify_webhook", lambda payload: webhook_calls.append(payload)
    )
    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")

    agent_mod.record_cap_stop(target_dir, "Cap dur atteint (5 iterations), arret.")

    assert "Arret par cap dur" in read_state(target_dir, "PROGRESS.md")
    assert webhook_calls[0]["event"] == "cap_reached"


# --- Fiabilite de la memoire persistante (cdc §4.1) ------------------------------


def test_run_iteration_repairs_corrupt_progress(tmp_path, monkeypatch):
    import src.loop.agent as agent_mod

    target_dir = tmp_path / "project"
    init_project_state(target_dir, instructions="Test")
    _init_git_repo(target_dir)
    write_state(
        target_dir,
        "PROGRESS.md",
        "# Journal de Progression\n\n## Derniere Iteration (N)\n- Action X\n",
    )

    mock, _ = _dual_ok()
    monkeypatch.setattr(agent_mod, "_run_opencode", mock)

    run_iteration(target_dir, iteration_number=1)

    content = read_state(target_dir, "PROGRESS.md")
    assert "## Prochaine Sous-Tache Prevue" in content
    assert "## Decisions d'Architecture" in content
