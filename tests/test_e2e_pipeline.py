"""Tests bout-en-bout du pipeline (phase 9 du plan d'implementation).

Ces tests lancent la VRAIE boucle ``src/loop/agent_loop.sh`` sur un
projet cible vierge, avec un faux ``opencode`` (script shell pilote
par scenario, cf. tests/fixtures/fake_opencode.sh) mis en tete du
PATH. Ils verifient le comportement de bout en bout :

- session vierge -> Plan -> Implement -> gates -> tags -> fin de
  mission -> DONE cree par la boucle (scenario ``full``) ;
- timeout de session (watchdog) ;
- erreurs API repetees -> pause du circuit breaker + modele de
  secours ;
- no-ops repetes -> fin forcee.

Aucune cle API ni appel reseau : les sessions sont simulees.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.core.git import commit_all, configure_git, ensure_gitignore, init_repo
from src.core.iterations import read_entries
from src.core.state import init_project_state, read_state

_DEBUILDER_ROOT = Path(__file__).resolve().parent.parent
_AGENT_LOOP = _DEBUILDER_ROOT / "src" / "loop" / "agent_loop.sh"
_FAKE_OPENCODE = Path(__file__).resolve().parent / "fixtures" / "fake_opencode.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="bash et git sont requis pour le test bout-en-bout",
)


def _init_target(tmp_path: Path) -> Path:
    """Cree un projet cible vierge (session vierge, jamais un clone).

    Le scaffold initial (fichiers d'etat + .gitignore) est commite
    avant la boucle : la detection de no-op d'une iteration ne doit
    porter que sur le travail de l'iteration, pas sur le bootstrap.
    """
    target_dir = tmp_path / "project"
    assert init_repo(target_dir)
    ensure_gitignore(target_dir)
    configure_git(target_dir)
    init_project_state(
        target_dir,
        instructions="Cahier des charges factice : creer main.py.",
        fresh_repo=True,
    )
    assert commit_all(target_dir, "chore: etat initial de la session vierge")
    return target_dir


def _run_loop(
    tmp_path: Path,
    target_dir: Path,
    scenario: str,
    extra_env: dict | None = None,
    max_iterations: str = "4",
) -> subprocess.CompletedProcess:
    """Lance la boucle reelle avec le faux opencode en tete du PATH."""
    fake_dir = tmp_path / "fakebin"
    fake_dir.mkdir(exist_ok=True)
    fake_bin = fake_dir / "opencode"
    fake_bin.symlink_to(_FAKE_OPENCODE)

    env = {
        **os.environ,
        "PATH": f"{fake_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "DEBUILDER_TARGET_DIR": str(target_dir),
        "DEBUILDER_PYTHON": sys.executable,
        "DEBUILDER_MODEL": "deepseek/fake-model",
        "DEBUILDER_STATE_DIR": str(tmp_path / "debuilder-state"),
        "DEBUILDER_MAX_ITERATIONS": max_iterations,
        "DEBUILDER_MAX_HOURS": "0",
        "DEBUILDER_BACKOFF_CAP_SECONDS": "2",
        "FAKE_SCENARIO": scenario,
    }
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["bash", str(_AGENT_LOOP)],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _git_tags(target_dir: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(target_dir), "tag", "--list", "debuilder/iter-*"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [tag for tag in result.stdout.splitlines() if tag]


def test_e2e_full_mission_until_done(tmp_path):
    """Session vierge -> Plan -> Implement -> gates -> tags -> DONE.

    Iteration 1 cree main.py (tag iter-0001), iteration 2 declare la
    fin de mission, la session Review valide et la boucle cree DONE.
    """
    target_dir = _init_target(tmp_path)
    result = _run_loop(tmp_path, target_dir, "full")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (target_dir / "DONE").exists()
    assert (target_dir / "main.py").exists()
    assert "hello from debuilder e2e" in (target_dir / "main.py").read_text()

    # Tags d'iteration : rollback possible a granularite d'iteration.
    assert _git_tags(target_dir) == ["debuilder/iter-0001", "debuilder/iter-0002"]

    # Contrat de tache : toutes les cases cochees par la session Implement.
    task_md = read_state(target_dir, "TASK.md")
    assert "- [ ]" not in task_md
    assert "- [x]" in task_md

    # Journal machine-readable : 2 lignes conformes, sessions et fin de
    # mission journalisees.
    entries = read_entries(target_dir)
    assert len(entries) == 2
    assert [e["iteration"] for e in entries] == [1, 2]
    assert [s["type"] for s in entries[0]["sessions"]] == ["plan", "implement"]
    assert [s["type"] for s in entries[1]["sessions"]] == [
        "plan",
        "implement",
        "review",
    ]
    assert entries[0]["no_op"] is False
    assert entries[1]["mission_completed"] is True
    assert entries[1]["tags"] == ["debuilder/iter-0002"]

    # Gate de tests deterministe : la commande du contrat ("true") a ete
    # executee par la boucle, pas seulement declaree par l'agent.
    assert entries[0]["tests_passed"] is True
    assert entries[1]["tests_passed"] is True


def test_e2e_timeout_is_journaled(tmp_path):
    """Une session bloque sans sortie : watchdog, typologie timeout."""
    target_dir = _init_target(tmp_path)
    result = _run_loop(
        tmp_path,
        target_dir,
        "timeout",
        extra_env={"DEBUILDER_OPENCODE_INACTIVITY_TIMEOUT": "1"},
        max_iterations="1",
    )

    assert result.returncode == 0
    entries = read_entries(target_dir)
    assert len(entries) == 1
    assert entries[0]["failure_type"] == "timeout"
    assert entries[0]["sessions"][0]["exit_code"] == -1
    assert "Cap dur atteint" in read_state(target_dir, "PROGRESS.md")


def test_e2e_api_failures_trip_circuit_breaker(tmp_path):
    """Erreurs API repetees : pause du breaker + bascule de secours."""
    target_dir = _init_target(tmp_path)
    state_dir = tmp_path / "debuilder-state"
    result = _run_loop(
        tmp_path,
        target_dir,
        "api",
        extra_env={
            "DEBUILDER_CB_MAX_FAILURES": "2",
            "DEBUILDER_CB_PAUSE_SECONDS": "1",
            "DEBUILDER_MODEL_FALLBACK": "fallback/model-fake",
        },
        max_iterations="3",
    )

    assert result.returncode == 0
    entries = read_entries(target_dir)
    assert len(entries) == 3
    assert all(e["failure_type"] == "api" for e in entries)
    # Apres le declenchement, l'iteration 3 utilise le modele de secours.
    assert entries[2]["model"] == "fallback/model-fake"

    breaker_state = json.loads(
        (state_dir / "circuit_breaker.json").read_text(encoding="utf-8")
    )
    assert breaker_state["tripped"] is True
    assert breaker_state["trip_count"] >= 1
    assert breaker_state["using_fallback"] is True
    assert "Cap dur atteint" in read_state(target_dir, "PROGRESS.md")


def test_e2e_noops_force_conclusion(tmp_path):
    """No-ops repetes : la boucle consigne l'echec et s'arrete au cap."""
    target_dir = _init_target(tmp_path)
    result = _run_loop(
        tmp_path,
        target_dir,
        "noop",
        extra_env={"DEBUILDER_MAX_NOOPS": "2"},
        max_iterations="2",
    )

    assert result.returncode == 0
    assert not (target_dir / "main.py").exists()

    entries = read_entries(target_dir)
    assert len(entries) == 2
    assert all(e["no_op"] is True for e in entries)
    assert entries[1]["failure_type"] == "noop"

    progress = read_state(target_dir, "PROGRESS.md")
    assert "ECHEC (no-op)" in progress
    assert "Cap dur atteint" in progress
