"""Tests pour le module log_summarizer.py."""

import os

import httpx
import pytest

from src.core import log_summarizer
from src.core.log_summarizer import summarize_logs


@pytest.fixture(autouse=True)
def cleanup():
    log_summarizer._cache.clear()
    yield
    log_summarizer._cache.clear()
    for key in ["DEBUILDER_MODEL", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]:
        os.environ.pop(key, None)


def test_empty_log_returns_placeholder():
    assert "attente" in summarize_logs("", cache_key="empty").lower()


def test_no_provider_configured_uses_heuristic():
    # Aucune cle API en environnement : pas d'appel LLM possible.
    raw_log = "=== Iteration 2026-07-24 10:00:00 ===\nModel: deepseek/deepseek-v4-pro\nTout va bien.\n"
    summary = summarize_logs(raw_log, cache_key="no-provider")
    assert "Iteration en cours depuis 2026-07-24 10:00:00" in summary
    assert "Aucune erreur" in summary


def test_heuristic_detects_errors():
    raw_log = "=== Iteration X ===\nSomething failed: Erreur critique\n"
    summary = summarize_logs(raw_log, cache_key="with-error")
    assert "erreurs sont visibles" in summary.lower()


def test_cache_avoids_recompute_on_unchanged_log():
    raw_log = "=== Iteration A ===\nOK\n"
    first = summarize_logs(raw_log, cache_key="cache-key")
    # Un deuxieme appel avec le meme contenu ne doit pas re-parcourir
    # la logique de resume : le cache renvoie exactement le meme objet.
    second = summarize_logs(raw_log, cache_key="cache-key")
    assert first == second
    assert log_summarizer._cache["cache-key"][1] == first


def test_cache_recomputes_on_changed_log():
    summarize_logs("=== Iteration A ===\nOK\n", cache_key="changing-key")
    changed = summarize_logs("=== Iteration B ===\nErreur\n", cache_key="changing-key")
    assert "Iteration en cours depuis B" in changed


def test_llm_called_when_provider_configured(monkeypatch):
    os.environ["DEBUILDER_MODEL"] = "deepseek/deepseek-chat"
    os.environ["DEEPSEEK_API_KEY"] = "sk-test-key-1234567890"

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": " Resume genere. "}}]}

        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)

    summary = summarize_logs("=== Iteration A ===\nTout va bien.\n", cache_key="llm-key")

    assert summary == "Resume genere."
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["json"]["model"] == "deepseek-chat"


def test_llm_failure_falls_back_to_heuristic(monkeypatch):
    os.environ["DEBUILDER_MODEL"] = "openai/gpt-5.2-codex"
    os.environ["OPENAI_API_KEY"] = "sk-test-key-1234567890"

    def failing_post(*args, **kwargs):
        raise httpx.ConnectError("network unreachable")

    monkeypatch.setattr(httpx, "post", failing_post)

    summary = summarize_logs("=== Iteration A ===\nTout va bien.\n", cache_key="fallback-key")

    assert "Iteration en cours depuis A" in summary


def test_secrets_never_sent_to_llm(monkeypatch):
    os.environ["DEBUILDER_MODEL"] = "deepseek/deepseek-chat"
    os.environ["DEEPSEEK_API_KEY"] = "sk-real-secret-999999"

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["sent_content"] = json["messages"][-1]["content"]

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)

    raw_log = "Authorization: Bearer sk-real-secret-999999\nOK\n"
    summarize_logs(raw_log, cache_key="sanitize-key")

    assert "sk-real-secret-999999" not in captured["sent_content"]
