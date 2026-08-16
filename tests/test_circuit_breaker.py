"""Tests pour le circuit breaker API (src/core/circuit_breaker.py)."""

import json
from pathlib import Path

import src.core.circuit_breaker as cb_mod
from src.core.circuit_breaker import CircuitBreaker


def _trip(breaker: CircuitBreaker, times: int = 3) -> None:
    for _ in range(times):
        breaker.record_failure("api")


def test_initial_state_is_closed(tmp_path: Path):
    breaker = CircuitBreaker(state_dir=tmp_path)

    assert breaker.should_pause() is False
    assert breaker.use_fallback() is False
    assert breaker.pause_remaining() == 0


def test_trips_after_max_api_failures(tmp_path: Path):
    breaker = CircuitBreaker(state_dir=tmp_path)

    breaker.record_failure("api")
    breaker.record_failure("api")
    assert breaker.should_pause() is False

    breaker.record_failure("api")
    assert breaker.should_pause() is True
    assert breaker.pause_remaining() > 0
    assert breaker.to_dict()["tripped"] is True


def test_success_resets_circuit(tmp_path: Path):
    breaker = CircuitBreaker(state_dir=tmp_path)
    _trip(breaker)
    assert breaker.should_pause() is True

    breaker.record_success()

    assert breaker.should_pause() is False
    assert breaker.to_dict()["api_failures"] == 0
    assert breaker.use_fallback() is False


def test_non_api_failure_breaks_streak(tmp_path: Path):
    breaker = CircuitBreaker(state_dir=tmp_path)

    breaker.record_failure("api")
    breaker.record_failure("api")
    breaker.record_failure("timeout")

    assert breaker.to_dict()["api_failures"] == 0
    assert breaker.should_pause() is False


def test_pause_grows_with_each_trip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEBUILDER_CB_PAUSE_SECONDS", "100")
    breaker = CircuitBreaker(state_dir=tmp_path)

    _trip(breaker)
    first = breaker.to_dict()["pause_seconds"]

    # Nouvelle serie de K echecs API, sans succes entre-temps : la
    # pause suivante est plus longue (duree croissante).
    _trip(breaker)
    second = breaker.to_dict()["pause_seconds"]

    assert second == first * 2


def test_max_failures_configurable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEBUILDER_CB_MAX_FAILURES", "5")
    breaker = CircuitBreaker(state_dir=tmp_path)

    for _ in range(4):
        breaker.record_failure("api")
    assert breaker.should_pause() is False

    breaker.record_failure("api")
    assert breaker.should_pause() is True


def test_use_fallback_when_configured(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEBUILDER_MODEL_FALLBACK", "deepseek/fallback-model")
    breaker = CircuitBreaker(state_dir=tmp_path)

    assert breaker.use_fallback() is False

    _trip(breaker)
    assert breaker.use_fallback() is True

    breaker.record_success()
    assert breaker.use_fallback() is False


def test_no_fallback_without_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DEBUILDER_MODEL_FALLBACK", raising=False)
    breaker = CircuitBreaker(state_dir=tmp_path)

    _trip(breaker)
    assert breaker.use_fallback() is False


def test_state_is_persisted(tmp_path: Path):
    breaker = CircuitBreaker(state_dir=tmp_path)
    _trip(breaker)

    reloaded = CircuitBreaker(state_dir=tmp_path)
    assert reloaded.should_pause() is True
    assert reloaded.to_dict()["trip_count"] == 1


def test_corrupt_state_file_falls_back_to_defaults(tmp_path: Path):
    (tmp_path / "circuit_breaker.json").write_text("{corrompu")

    breaker = CircuitBreaker(state_dir=tmp_path)
    assert breaker.should_pause() is False


def test_partial_state_file_is_merged(tmp_path: Path):
    (tmp_path / "circuit_breaker.json").write_text(json.dumps({"api_failures": 7}))

    breaker = CircuitBreaker(state_dir=tmp_path)
    assert breaker.to_dict()["api_failures"] == 7
    assert breaker.should_pause() is False


def test_webhook_fires_on_trip(tmp_path: Path, monkeypatch):
    calls = []
    monkeypatch.setenv("DEBUILDER_WEBHOOK_URL", "http://example.invalid/hook")

    def _fake_post(url, json=None, timeout=None):
        calls.append((url, json))

    monkeypatch.setattr(cb_mod.httpx, "post", _fake_post)

    breaker = CircuitBreaker(state_dir=tmp_path)
    _trip(breaker)

    assert len(calls) == 1
    assert calls[0][0] == "http://example.invalid/hook"
    assert calls[0][1]["event"] == "circuit_breaker_tripped"
    assert calls[0][1]["breaker"]["tripped"] is True


def test_no_webhook_without_url(tmp_path: Path, monkeypatch):
    calls = []
    monkeypatch.delenv("DEBUILDER_WEBHOOK_URL", raising=False)

    def _fake_post(url, json=None, timeout=None):
        calls.append(url)

    monkeypatch.setattr(cb_mod.httpx, "post", _fake_post)

    _trip(CircuitBreaker(state_dir=tmp_path))

    assert calls == []
