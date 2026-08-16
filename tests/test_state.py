"""Tests pour le module state.py."""

import os
import subprocess
import sys
from pathlib import Path

from src.core.state import (
    append_state,
    clear_suggestions,
    compact_architecture,
    init_project_state,
    is_done,
    read_state,
    repair_progress,
    touch_done,
    update_progress,
    write_state,
)


def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
    )


def test_init_project_state(tmp_path: Path):
    init_project_state(tmp_path, instructions="Build a web app", hardware_info="GPU: A100")

    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / "PROGRESS.md").exists()
    assert (tmp_path / "BENCHMARKS.md").exists()
    assert (tmp_path / "SUGGESTIONS.md").exists()
    assert (tmp_path / "RESOURCES_NEEDED.md").exists()
    assert (tmp_path / "TASK.md").exists()
    assert (tmp_path / "PLAN.md").exists()
    assert (tmp_path / "ARCHITECTURE.md").exists()
    assert (tmp_path / "SPEC_COVERAGE.md").exists()

    agents = read_state(tmp_path, "AGENTS.md")
    assert "Build a web app" in agents
    assert "GPU: A100" in agents

    task = read_state(tmp_path, "TASK.md")
    assert "## Objectif" in task
    assert "## Sous-Taches" in task


def test_read_write_state(tmp_path: Path):
    write_state(tmp_path, "test.txt", "bonjour")
    content = read_state(tmp_path, "test.txt")
    assert content == "bonjour"


def test_read_state_missing_file(tmp_path: Path):
    content = read_state(tmp_path, "nonexistent.md")
    assert content == ""


def test_append_state(tmp_path: Path):
    write_state(tmp_path, "log.md", "ligne1\n")
    append_state(tmp_path, "log.md", "ligne2\n")
    content = read_state(tmp_path, "log.md")
    assert content == "ligne1\nligne2\n"


def test_append_state_new_file(tmp_path: Path):
    append_state(tmp_path, "new.md", "first line\n")
    content = read_state(tmp_path, "new.md")
    assert "first line" in content


def test_update_progress_sliding_window(tmp_path: Path):
    init_project_state(tmp_path)

    entry1 = "- **Action** : Added login\n- **Resultat** : OK"
    update_progress(tmp_path, entry1, max_iterations=2)

    content = read_state(tmp_path, "PROGRESS.md")
    assert "Added login" in content
    assert "Derniere Iteration (N)" in content

    entry2 = "- **Action** : Added dashboard\n- **Resultat** : OK"
    update_progress(tmp_path, entry2, max_iterations=2)

    content = read_state(tmp_path, "PROGRESS.md")
    assert "Added dashboard" in content
    assert "Added login" in content

    entry3 = "- **Action** : Added tests\n- **Resultat** : OK"
    update_progress(tmp_path, entry3, max_iterations=2)

    content = read_state(tmp_path, "PROGRESS.md")
    assert "Added tests" in content
    assert "Added dashboard" in content
    assert "Added login" not in content


def test_update_progress_preserves_next_task(tmp_path: Path):
    init_project_state(tmp_path)
    write_state(
        tmp_path,
        "PROGRESS.md",
        "# Journal de Progression\n\n"
        "## Derniere Iteration (N)\n- Task A\n\n"
        "## Iteration Precedente (N-1)\n- Task B\n\n"
        "## Prochaine Sous-Tache Prevue\n"
        "Implementer la base de donnees.\n",
    )

    entry = "- **Action** : Task C\n- **Resultat** : OK"
    update_progress(tmp_path, entry, max_iterations=2)

    content = read_state(tmp_path, "PROGRESS.md")
    assert "Task C" in content
    assert "Task A" in content
    assert "Task B" not in content
    assert "Implementer la base de donnees" in content


def test_touch_done_and_is_done(tmp_path: Path):
    assert not is_done(tmp_path)
    touch_done(tmp_path)
    assert is_done(tmp_path)


def test_clear_suggestions(tmp_path: Path):
    write_state(tmp_path, "SUGGESTIONS.md", "Utilise Redis pour le cache.\n")
    clear_suggestions(tmp_path)
    content = read_state(tmp_path, "SUGGESTIONS.md")
    assert content == ""


# --- Reparation deterministe de PROGRESS.md (cdc §4.1) ------------------------


def test_repair_progress_restores_missing_separator(tmp_path: Path):
    init_project_state(tmp_path)
    write_state(
        tmp_path,
        "PROGRESS.md",
        "# Journal de Progression\n\n## Derniere Iteration (N)\n- Action X\n",
    )

    changed = repair_progress(tmp_path)

    assert changed is True
    content = read_state(tmp_path, "PROGRESS.md")
    assert "## Prochaine Sous-Tache Prevue" in content
    assert "## Decisions d'Architecture" in content
    assert "Action X" in content


def test_repair_progress_restores_truncated_arch_section(tmp_path: Path):
    init_project_state(tmp_path)
    write_state(
        tmp_path,
        "PROGRESS.md",
        "# Journal de Progression\n\n"
        "## Derniere Iteration (N)\n- A\n\n"
        "## Prochaine Sous-Tache Prevue\n\n(rien)\n",
    )

    assert repair_progress(tmp_path) is True
    content = read_state(tmp_path, "PROGRESS.md")
    assert "## Decisions d'Architecture" in content
    assert "(rien)" in content


def test_repair_progress_noop_when_valid(tmp_path: Path):
    init_project_state(tmp_path)
    before = read_state(tmp_path, "PROGRESS.md")

    assert repair_progress(tmp_path) is False
    assert read_state(tmp_path, "PROGRESS.md") == before


def test_repair_progress_missing_file_is_noop(tmp_path: Path):
    assert repair_progress(tmp_path) is False


# --- Compaction d'ARCHITECTURE.md (cdc §4.2) -----------------------------------


def test_compact_architecture_compacts_old_entries(tmp_path: Path):
    init_project_state(tmp_path)
    header = "# Decisions d'Architecture\n\n## Decisions\n\n"
    entries = [
        f"### Decision {i}\n"
        + "\n".join(
            f"- point {j} : justification assez longue pour occuper de la place"
            for j in range(4)
        )
        for i in range(20)
    ]
    original = header + "\n\n".join(entries) + "\n"
    write_state(tmp_path, "ARCHITECTURE.md", original)

    changed = compact_architecture(tmp_path, max_chars=500)

    assert changed is True
    content = read_state(tmp_path, "ARCHITECTURE.md")
    assert "## Historique compacte" in content
    # Les entrees recentes sont conservees intactes...
    assert "point 0 : justification assez longue" in content
    # ... les plus anciennes existent encore, compactees en une ligne.
    assert "Decision 19" in content
    assert len(content) < len(original)


def test_compact_architecture_noop_under_budget(tmp_path: Path):
    init_project_state(tmp_path)
    write_state(
        tmp_path,
        "ARCHITECTURE.md",
        "# Decisions d'Architecture\n\n## Decisions\n\n- Decision 1 : stack Python\n",
    )

    assert compact_architecture(tmp_path, max_chars=4000) is False


def test_compact_architecture_env_default(tmp_path: Path, monkeypatch):
    init_project_state(tmp_path)
    monkeypatch.setenv("DEBUILDER_ARCH_MAX_CHARS", "200")
    header = "# Decisions d'Architecture\n\n## Decisions\n\n"
    entries = [f"- Decision {i} : description {i}" for i in range(30)]
    write_state(tmp_path, "ARCHITECTURE.md", header + "\n\n".join(entries) + "\n")

    assert compact_architecture(tmp_path) is True
    assert "## Historique compacte" in read_state(tmp_path, "ARCHITECTURE.md")


# --- Hook pre-commit de tests ----------------------------------------------


def test_init_project_state_installs_hook_on_fresh_repo(tmp_path: Path):
    from src.core.git import init_repo

    assert init_repo(tmp_path)
    init_project_state(tmp_path, instructions="Test", fresh_repo=True)

    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    assert hook.exists()
    assert os.access(hook, os.X_OK)


def test_init_project_state_no_hook_by_default(tmp_path: Path):
    from src.core.git import init_repo

    assert init_repo(tmp_path)
    init_project_state(tmp_path, instructions="Test")

    assert not (tmp_path / ".git" / "hooks" / "pre-commit").exists()


def test_init_project_state_never_overwrites_existing_hook(tmp_path: Path):
    from src.core.git import init_repo

    assert init_repo(tmp_path)
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")

    init_project_state(tmp_path, instructions="Test", fresh_repo=True)

    assert hook.read_text() == "#!/bin/sh\nexit 0\n"


def test_init_project_state_hook_without_git_is_noop(tmp_path: Path):
    init_project_state(tmp_path, instructions="Test", fresh_repo=True)

    assert not (tmp_path / ".git" / "hooks" / "pre-commit").exists()


def test_installed_hook_blocks_broken_commit(tmp_path: Path):
    from src.core.git import init_repo

    assert init_repo(tmp_path)
    init_project_state(tmp_path, instructions="Test", fresh_repo=True)
    _git(tmp_path, "config", "user.email", "test@test.com")
    _git(tmp_path, "config", "user.name", "Test")

    write_state(
        tmp_path,
        "AGENTS.md",
        "# Objectif\n\n## Commande de Test\n\n```\n"
        + f"{sys.executable} -m pytest -q\n"
        + "```\n",
    )
    (tmp_path / "test_sample.py").write_text("def test_ko():\n    assert False\n")

    result = _git(tmp_path, "add", "-A")
    result = _git(tmp_path, "commit", "-m", "etat casse")

    assert result.returncode != 0
    assert "tests en echec" in result.stderr

    # Une fois les tests corriges, le meme commit passe.
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert True\n")
    result = _git(tmp_path, "add", "-A")
    result = _git(tmp_path, "commit", "-m", "etat corrige")

    assert result.returncode == 0
