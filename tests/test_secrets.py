"""Tests pour le module secrets.py."""

import os

import pytest

from src.core.secrets import get_secret, inject_secrets, sanitize_text


_UNIQUE_KEY = "DEBUILDER_TEST_API_KEY"


@pytest.fixture(autouse=True)
def cleanup_env():
    yield
    os.environ.pop("MY_API_KEY", None)
    os.environ.pop(_UNIQUE_KEY, None)
    os.environ.pop("OPENAI_KEY", None)
    os.environ.pop("TEST_KEY", None)


def test_inject_and_get_secret():
    inject_secrets({"MY_API_KEY": "sk-abc123"})
    assert get_secret("MY_API_KEY") == "sk-abc123"
    assert get_secret("NONEXISTENT") is None


def test_sanitize_with_explicit_dict():
    secrets = {"KEY": "secret123456"}
    text = "Using key: secret123456 for auth"
    sanitized = sanitize_text(text, secrets=secrets)
    assert "secret123456" not in sanitized
    assert "***" in sanitized
    assert "Using key:" in sanitized


def test_sanitize_with_env_vars():
    inject_secrets({"OPENAI_API_KEY": "sk-verysecretkey123"})
    text = "Authorization: Bearer sk-verysecretkey123"
    sanitized = sanitize_text(text)
    assert "sk-verysecretkey123" not in sanitized
    assert "***" in sanitized


def test_sanitize_no_secrets():
    text = "Nothing sensitive here."
    sanitized = sanitize_text(text, secrets={})
    assert sanitized == text


def test_sanitize_multiple_secrets():
    secrets = {"KEY_A": "aaaa1111", "KEY_B": "bbbb2222"}
    text = "Keys: aaaa1111 and bbbb2222 used"
    sanitized = sanitize_text(text, secrets=secrets)
    assert "aaaa1111" not in sanitized
    assert "bbbb2222" not in sanitized


def test_secret_never_written_to_disk(tmp_path):
    secret_value = "sk-disk-test-123"
    inject_secrets({"DEBUILDER_DISK_TEST_KEY": secret_value})

    log_file = tmp_path / "log.txt"
    log_file.write_text("Logging with key: " + secret_value)
    raw_content = log_file.read_text()
    assert secret_value in raw_content
    sanitized = sanitize_text(raw_content, secrets={"KEY": secret_value})
    assert secret_value not in sanitized
