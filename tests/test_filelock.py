"""Tests pour le module filelock.py."""

import multiprocessing
import time
from pathlib import Path

import pytest

from src.core.filelock import acquire_lock, file_lock, release_lock


def _try_acquire_timeout(args):
    lock_path, timeout = args
    try:
        acquire_lock(lock_path, timeout=timeout)
        return "acquired"
    except TimeoutError:
        return "timeout"


def _try_acquire_after_release(args):
    lock_path, timeout = args
    fd = acquire_lock(lock_path, timeout=timeout)
    return fd


def test_acquire_and_release(tmp_path: Path):
    lock_path = tmp_path / "test.lock"
    fd = acquire_lock(lock_path, timeout=1.0)
    assert fd >= 0
    release_lock(fd)


def test_file_lock_context_manager(tmp_path: Path):
    target = tmp_path / "data.txt"
    target.write_text("hello")
    with file_lock(target):
        content = target.read_text()
        assert content == "hello"


def test_concurrent_locks_block(tmp_path: Path):
    lock_path = tmp_path / "concurrent.lock"
    fd1 = acquire_lock(lock_path, timeout=1.0)

    with multiprocessing.Pool(1) as pool:
        result = pool.apply_async(_try_acquire_timeout, ((lock_path, 0.5),))
        got = result.get(timeout=2)

    assert got == "timeout"
    release_lock(fd1)


def test_concurrent_lock_acquired_after_release(tmp_path: Path):
    lock_path = tmp_path / "concurrent2.lock"
    fd1 = acquire_lock(lock_path, timeout=1.0)

    with multiprocessing.Pool(1) as pool:
        result = pool.apply_async(_try_acquire_after_release, ((lock_path, 2.0),))
        time.sleep(0.1)
        release_lock(fd1)
        fd2 = result.get(timeout=3)

    assert fd2 >= 0
    release_lock(fd2)


def test_timeout_raises(tmp_path: Path):
    lock_path = tmp_path / "timeout.lock"
    fd = acquire_lock(lock_path, timeout=1.0)
    try:
        with pytest.raises(TimeoutError):
            acquire_lock(lock_path, timeout=0.1)
    finally:
        release_lock(fd)


def test_file_lock_creates_and_removes_lock_file(tmp_path: Path):
    target = tmp_path / "target.txt"
    lock_path = Path(str(target) + ".lock")
    assert not lock_path.exists()
    with file_lock(target):
        assert lock_path.exists()
    assert not lock_path.exists()
