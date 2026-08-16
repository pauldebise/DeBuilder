"""Tests pour la gate de tests deterministe (src/utils/test_results.py)."""

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from src.utils.task_parser import extract_test_command
from src.utils.test_results import (
    parse_junit,
    resolve_test_command,
    run_test_gate,
)


def _write_junit(path: Path, tests: int, failures: int, errors: int, skipped: int) -> None:
    root = ET.Element(
        "testsuite",
        {
            "name": "pytest",
            "tests": str(tests),
            "failures": str(failures),
            "errors": str(errors),
            "skipped": str(skipped),
        },
    )
    tree = ET.ElementTree(root)
    tree.write(str(path), encoding="utf-8")


def _write_test_project(root: Path, failing: bool = False) -> None:
    (root / "test_sample.py").write_text(
        "def test_ok():\n    assert True\n"
        + ("def test_ko():\n    assert False\n" if failing else "")
    )


# --- parse_junit ---------------------------------------------------------


def test_parse_junit_all_pass(tmp_path: Path):
    path = tmp_path / "junit.xml"
    _write_junit(path, tests=5, failures=0, errors=0, skipped=1)

    result = parse_junit(path)

    assert result is not None
    assert result.passed is True
    assert result.tests == 5
    assert result.failures == 0
    assert result.errors == 0
    assert result.skipped == 1


def test_parse_junit_with_failures(tmp_path: Path):
    path = tmp_path / "junit.xml"
    _write_junit(path, tests=5, failures=2, errors=0, skipped=0)

    result = parse_junit(path)

    assert result is not None
    assert result.passed is False
    assert result.failures == 2


def test_parse_junit_with_errors(tmp_path: Path):
    path = tmp_path / "junit.xml"
    _write_junit(path, tests=5, failures=0, errors=3, skipped=0)

    assert parse_junit(path).passed is False


def test_parse_junit_missing_file(tmp_path: Path):
    assert parse_junit(tmp_path / "absent.xml") is None


def test_parse_junit_empty_file(tmp_path: Path):
    path = tmp_path / "junit.xml"
    path.write_text("")

    assert parse_junit(path) is None


def test_parse_junit_invalid_xml(tmp_path: Path):
    path = tmp_path / "junit.xml"
    path.write_text("<testsuite")

    assert parse_junit(path) is None


# --- resolve_test_command -------------------------------------------------


def test_resolve_test_command_from_agents_section(tmp_path: Path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text(
        "# Objectif\n\n## Commande de Test\n\n```\npython -m pytest\n```\n"
    )

    monkeypatch.delenv("DEBUILDER_TEST_CMD", raising=False)
    assert resolve_test_command(tmp_path) == "python -m pytest"


def test_resolve_test_command_plain_line_after_section(tmp_path: Path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("# Objectif\n\n## Commande de Test\nmake test\n")

    monkeypatch.delenv("DEBUILDER_TEST_CMD", raising=False)
    assert resolve_test_command(tmp_path) == "make test"


def test_resolve_test_command_task_beats_agents(tmp_path: Path):
    (tmp_path / "TASK.md").write_text("## Commande de test\n```\npytest -x\n```\n")
    (tmp_path / "AGENTS.md").write_text("## Commande de test\n```\npytest -q\n```\n")

    assert resolve_test_command(tmp_path) == "pytest -x"


def test_resolve_test_command_env_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEBUILDER_TEST_CMD", "pytest tests/")

    assert resolve_test_command(tmp_path) == "pytest tests/"


def test_resolve_test_command_empty_when_no_source(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DEBUILDER_TEST_CMD", raising=False)

    assert resolve_test_command(tmp_path) == ""


def test_resolve_test_command_ignores_other_sections(tmp_path: Path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("# Objectif\n\n## Contexte\n```\nmake build\n```\n")

    monkeypatch.delenv("DEBUILDER_TEST_CMD", raising=False)
    assert resolve_test_command(tmp_path) == ""


def test_extract_test_command_handles_missing_section():
    assert extract_test_command("# Objectif\nrien\n") == ""


# --- run_test_gate --------------------------------------------------------


def test_run_test_gate_passing_pytest_project(tmp_path: Path):
    _write_test_project(tmp_path, failing=False)

    result = run_test_gate(tmp_path, f"{sys.executable} -m pytest -q")

    assert result.ran is True
    assert result.passed is True
    assert result.returncode == 0
    assert result.tests == 1
    assert result.failures == 0


def test_run_test_gate_failing_pytest_project(tmp_path: Path):
    _write_test_project(tmp_path, failing=True)

    result = run_test_gate(tmp_path, f"{sys.executable} -m pytest -q")

    assert result.passed is False
    assert result.failures == 1
    assert "assert False" in result.detail or "test_ko" in result.detail


def test_run_test_gate_respects_existing_junitxml_flag(tmp_path: Path, monkeypatch):
    _write_test_project(tmp_path, failing=False)

    result = run_test_gate(
        tmp_path,
        f"{sys.executable} -m pytest -q --junitxml={tmp_path / 'perso.xml'}",
    )

    assert result.passed is True
    assert (tmp_path / "perso.xml").exists()


def test_run_test_gate_non_pytest_command_uses_exit_code(tmp_path: Path):
    result = run_test_gate(tmp_path, "sh -c 'exit 1'")

    assert result.passed is False
    assert result.returncode == 1
    assert result.tests is None


def test_run_test_gate_missing_command(tmp_path: Path):
    result = run_test_gate(tmp_path, "commande-qui-nexiste-pas-xyz")

    assert result.ran is False
    assert result.passed is False
    assert "introuvable" in result.detail


def test_run_test_gate_empty_command(tmp_path: Path):
    result = run_test_gate(tmp_path, "  ")

    assert result.passed is False
    assert result.ran is False
