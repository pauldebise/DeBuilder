"""Tests pour le module git.py."""

import subprocess
from pathlib import Path

from src.core.git import (
    commit_all,
    ensure_gitignore,
    init_repo,
    rollback_last,
    stage_and_commit_all,
)


def _init_test_repo(repo_dir: Path) -> None:
    assert init_repo(repo_dir)
    _run(repo_dir, "config", "user.email", "test@test.com")
    _run(repo_dir, "config", "user.name", "Test")


def _run(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
    )


def test_init_repo(tmp_path: Path):
    repo_dir = tmp_path / "test_repo"
    result = init_repo(repo_dir)
    assert result
    assert (repo_dir / ".git").is_dir()


def test_commit_all(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)

    (repo_dir / "test.txt").write_text("hello")
    result = commit_all(repo_dir, "feat: add test file")
    assert result

    log = _run(repo_dir, "log", "--oneline")
    assert "add test file" in log.stdout


def test_commit_all_no_changes(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)
    (repo_dir / "test.txt").write_text("hello")
    commit_all(repo_dir, "first")

    result = commit_all(repo_dir, "no changes")
    assert result


def test_rollback_last(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)

    (repo_dir / "file1.txt").write_text("version 1")
    commit_all(repo_dir, "commit 1")

    (repo_dir / "file1.txt").write_text("version 2")
    commit_all(repo_dir, "commit 2")

    assert (repo_dir / "file1.txt").read_text() == "version 2"

    result = rollback_last(repo_dir)
    assert result
    assert (repo_dir / "file1.txt").read_text() == "version 1"


def test_stage_and_commit_all(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)

    (repo_dir / "work.txt").write_text("progress")
    result = stage_and_commit_all(repo_dir, "iteration 1")
    assert result

    log = _run(repo_dir, "log", "--oneline")
    assert "iteration 1" in log.stdout


def test_stage_and_commit_all_no_changes(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)
    (repo_dir / "work.txt").write_text("progress")
    stage_and_commit_all(repo_dir, "iteration 1")

    result = stage_and_commit_all(repo_dir, "iteration 2 - no changes")
    assert result


def test_ensure_gitignore_creates_entries(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)

    ensure_gitignore(repo_dir)

    content = (repo_dir / ".gitignore").read_text()
    for pattern in ["DONE", "BARRIER_*", "*.lock", "OPENCODE_LOG.txt"]:
        assert pattern in content


def test_ensure_gitignore_preserves_existing_content(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)
    (repo_dir / ".gitignore").write_text("node_modules/\n*.pyc\n")

    ensure_gitignore(repo_dir)

    content = (repo_dir / ".gitignore").read_text()
    assert "node_modules/" in content
    assert "*.pyc" in content
    assert "DONE" in content


def test_ensure_gitignore_idempotent(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)

    ensure_gitignore(repo_dir)
    ensure_gitignore(repo_dir)

    content = (repo_dir / ".gitignore").read_text()
    assert content.count("DONE") == 1


def test_stage_and_commit_all_ignores_operational_files(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)
    ensure_gitignore(repo_dir)

    (repo_dir / "work.txt").write_text("progress")
    (repo_dir / "DONE").touch()
    (repo_dir / "OPENCODE_LOG.txt").write_text("raw output")
    (repo_dir / "PROGRESS.md.lock").touch()

    stage_and_commit_all(repo_dir, "iteration 1")

    tracked = _run(repo_dir, "ls-files").stdout.splitlines()
    assert "work.txt" in tracked
    assert "DONE" not in tracked
    assert "OPENCODE_LOG.txt" not in tracked
    assert "PROGRESS.md.lock" not in tracked


def test_operations_isolated_from_debuilder(tmp_path: Path):
    debuilder_git_dir = Path(__file__).parent.parent / ".git"
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)

    def _last_commit_msg(git_dir: Path) -> str:
        result = subprocess.run(
            ["git", "-C", str(git_dir), "log", "-1", "--format=%s"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    msg_before = _last_commit_msg(debuilder_git_dir)

    (repo_dir / "isolated.txt").write_text("do not touch debuilder")
    commit_all(repo_dir, "isolated test")

    msg_after = _last_commit_msg(debuilder_git_dir)
    assert msg_before == msg_after
