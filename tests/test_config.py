"""Tests pour la configuration de session (src/gui/config.py)."""

from src.gui.config import PROVIDERS, _normalize_model, _start_session
from src.utils.text import strip_ansi


def test_normalize_model_adds_provider_prefix():
    cfg = PROVIDERS["DeepSeek"]
    assert _normalize_model("deepseek-v4-pro", cfg) == "deepseek/deepseek-v4-pro"


def test_normalize_model_keeps_full_name():
    cfg = PROVIDERS["DeepSeek"]
    assert _normalize_model("deepseek/deepseek-chat", cfg) == "deepseek/deepseek-chat"


def test_normalize_model_empty_falls_back_to_default():
    cfg = PROVIDERS["Anthropic"]
    assert _normalize_model("", cfg) == cfg["default_model"]
    assert "/" in _normalize_model("", cfg)


def test_normalize_model_custom_provider_no_prefix():
    cfg = PROVIDERS["Autre (custom)"]
    assert _normalize_model("", cfg) == ""
    assert _normalize_model("monprovider/mon-modele", cfg) == "monprovider/mon-modele"


def test_default_models_are_provider_qualified():
    for name, cfg in PROVIDERS.items():
        if cfg["default_model"]:
            assert "/" in cfg["default_model"], name


def test_start_session_requires_workspace():
    msg, target = _start_session("", "", "objectif", "DeepSeek", "", "sk-x")
    assert "Erreur" in msg
    assert target == ""


def test_start_session_requires_api_key():
    msg, target = _start_session("", "/tmp/nowhere", "objectif", "DeepSeek", "", "")
    assert "cle API" in msg
    assert target == ""


def test_start_session_success_persists_last_session(tmp_path, monkeypatch):
    import src.gui.config as config_mod

    workspace = tmp_path / "ws"
    saved = {}

    monkeypatch.setattr(config_mod, "_find_opencode", lambda: "/usr/bin/opencode")
    monkeypatch.setattr(config_mod, "_validate_opencode", lambda model: "")
    monkeypatch.setattr(config_mod, "init_repo", lambda d: True)
    monkeypatch.setattr(config_mod, "configure_git", lambda *a, **k: None)
    monkeypatch.setattr(config_mod, "ensure_gitignore", lambda d: None)
    monkeypatch.setattr(config_mod, "audit_hardware", lambda: object())
    monkeypatch.setattr(config_mod, "format_for_agent", lambda hw: "CPU: 1")
    monkeypatch.setattr(config_mod.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(config_mod, "save_last_session", lambda d: saved.setdefault("dir", d))

    msg, target = config_mod._start_session(
        repo_url="",
        workspace_dir=str(workspace),
        instructions="Objectif",
        provider="Autre (custom)",
        model="opencode/free-model",
        api_key="unused",
    )

    assert "Session lancee" in msg
    assert target == str(workspace)
    assert saved["dir"] == workspace


def test_strip_ansi_removes_color_codes():
    colored = "\x1b[91m\x1b[1mError: \x1b[0mAuthentication Fails"
    assert strip_ansi(colored) == "Error: Authentication Fails"


def test_strip_ansi_plain_text_unchanged():
    assert strip_ansi("rien a nettoyer") == "rien a nettoyer"
