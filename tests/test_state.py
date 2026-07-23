"""Tests pour le module state.py."""

from pathlib import Path

from src.core.state import (
    append_state,
    clear_suggestions,
    init_project_state,
    is_done,
    read_state,
    touch_done,
    update_progress,
    write_state,
)


def test_init_project_state(tmp_path: Path):
    init_project_state(tmp_path, instructions="Build a web app", hardware_info="GPU: A100")

    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / "PROGRESS.md").exists()
    assert (tmp_path / "BENCHMARKS.md").exists()
    assert (tmp_path / "SUGGESTIONS.md").exists()
    assert (tmp_path / "RESOURCES_NEEDED.md").exists()

    agents = read_state(tmp_path, "AGENTS.md")
    assert "Build a web app" in agents
    assert "GPU: A100" in agents


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
