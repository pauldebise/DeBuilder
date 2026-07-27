"""Tests pour le module text.py (nettoyage ANSI, lecture de logs)."""

from pathlib import Path

from src.utils.text import read_log_tail, strip_ansi


def test_strip_ansi_removes_color_codes():
    colored = "\x1b[91m\x1b[1mError: \x1b[0mAuthentication Fails"
    assert strip_ansi(colored) == "Error: Authentication Fails"


def test_strip_ansi_plain_text_unchanged():
    assert strip_ansi("rien a nettoyer") == "rien a nettoyer"


def test_read_log_tail_missing_file(tmp_path: Path):
    assert read_log_tail(tmp_path, "OPENCODE_LOG.txt", 10) == ""


def test_read_log_tail_returns_last_n_lines(tmp_path: Path):
    (tmp_path / "OPENCODE_LOG.txt").write_text("\n".join(f"ligne {i}" for i in range(1, 6)))

    tail = read_log_tail(tmp_path, "OPENCODE_LOG.txt", 2)

    assert tail == "ligne 4\nligne 5"
