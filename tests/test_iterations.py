"""Tests pour le journal machine-readable ITERATIONS.jsonl."""

import json
import threading
from pathlib import Path

from src.core.iterations import (
    append_entry,
    journal_path,
    missing_entry_fields,
    read_entries,
)


def test_append_entry_creates_jsonl_lines(tmp_path: Path):
    append_entry(tmp_path, {"iteration": 1, "exit_code": 0})
    append_entry(tmp_path, {"iteration": 2, "exit_code": 1})

    entries = read_entries(tmp_path)
    assert len(entries) == 2
    assert entries[0]["iteration"] == 1
    assert entries[1]["iteration"] == 2
    assert "timestamp" in entries[0]
    assert "timestamp" in entries[1]


def test_append_entry_each_line_is_valid_json(tmp_path: Path):
    append_entry(tmp_path, {"iteration": 1})
    append_entry(tmp_path, {"iteration": 2})

    content = journal_path(tmp_path).read_text(encoding="utf-8")
    lines = content.strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)


def test_append_entry_preserves_unicode(tmp_path: Path):
    append_entry(tmp_path, {"failure_type": "échec"})
    assert read_entries(tmp_path)[0]["failure_type"] == "échec"


def test_read_entries_missing_file_returns_empty(tmp_path: Path):
    assert read_entries(tmp_path) == []


def test_read_entries_limit_returns_most_recent(tmp_path: Path):
    for i in range(5):
        append_entry(tmp_path, {"iteration": i})

    entries = read_entries(tmp_path, limit=2)
    assert len(entries) == 2
    assert [e["iteration"] for e in entries] == [3, 4]


def test_read_entries_skips_corrupt_lines(tmp_path: Path):
    append_entry(tmp_path, {"iteration": 1})
    with journal_path(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write("{ligne corrompue\n")
    append_entry(tmp_path, {"iteration": 2})

    entries = read_entries(tmp_path)
    assert [e["iteration"] for e in entries] == [1, 2]


def test_append_entry_is_locked_and_append_only(tmp_path: Path):
    def _worker(n: int) -> None:
        for i in range(25):
            append_entry(tmp_path, {"iteration": n * 100 + i})

    threads = [threading.Thread(target=_worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    entries = read_entries(tmp_path)
    assert len(entries) == 100


# --- Conformite des lignes (schema cdc §5.2) ---------------------------------


def _conform_entry() -> dict:
    """Ligne complete telle qu'ecrite par la boucle a chaque iteration."""
    return {
        "timestamp": "2026-08-16T12:00:00",
        "iteration": 12,
        "exit_code": 0,
        "failure_type": "",
        "duration_seconds": 42.5,
        "changed_files": 3,
        "diff": {"added": 25, "removed": 2},
        "no_op": False,
        "tags": ["debuilder/iter-0012"],
        "model": "deepseek/deepseek-v4-pro",
        "sessions": [
            {"type": "plan", "model": "deepseek/deepseek-v4-pro", "exit_code": 0, "duration_seconds": 10.0},
            {"type": "implement", "model": "deepseek/deepseek-v4-pro", "exit_code": 0, "duration_seconds": 32.5},
        ],
        "mission_completed": False,
    }


def test_missing_entry_fields_none_on_conform_line():
    assert missing_entry_fields(_conform_entry()) == []


def test_missing_entry_fields_reports_all_missing():
    missing = missing_entry_fields({"iteration": 1})
    assert "timestamp" in missing
    assert "exit_code" in missing
    assert "sessions" in missing
    assert "diff" in missing
    assert "iteration" not in missing


def test_conform_entry_roundtrips_through_journal(tmp_path: Path):
    entry = _conform_entry()
    append_entry(tmp_path, entry)

    read_back = read_entries(tmp_path)[0]
    assert read_back["iteration"] == 12
    assert read_back["diff"] == {"added": 25, "removed": 2}
    assert [s["type"] for s in read_back["sessions"]] == ["plan", "implement"]
    assert missing_entry_fields(read_back) == []
