"""Tests pour le journal machine-readable ITERATIONS.jsonl."""

import json
import threading
from pathlib import Path

from src.core.iterations import append_entry, journal_path, read_entries


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
