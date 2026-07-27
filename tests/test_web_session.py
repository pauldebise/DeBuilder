"""Tests pour les routes de session FastAPI (src/web/routes_session.py)."""

from pathlib import Path

from fastapi.testclient import TestClient

import src.web.routes_session as routes_session
from src.core.session import save_last_session
from src.web.app import app
from src.web.routes_session import PROVIDERS, _normalize_model

client = TestClient(app)


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


def test_get_session_returns_null_when_no_active_session(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEBUILDER_STATE_DIR", str(tmp_path / "state"))

    resp = client.get("/api/session")

    assert resp.status_code == 200
    assert resp.json() == {"target_dir": None}


def test_get_session_returns_active_session(tmp_path: Path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("DEBUILDER_STATE_DIR", str(state_dir))

    target_dir = tmp_path / "project"
    target_dir.mkdir()
    (target_dir / "AGENTS.md").write_text("# Objectifs")
    save_last_session(target_dir, state_dir=state_dir)

    resp = client.get("/api/session")

    assert resp.status_code == 200
    assert resp.json() == {"target_dir": str(target_dir)}


def test_get_session_ignores_deleted_project(tmp_path: Path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("DEBUILDER_STATE_DIR", str(state_dir))

    target_dir = tmp_path / "project"
    target_dir.mkdir()
    (target_dir / "AGENTS.md").write_text("# Objectifs")
    save_last_session(target_dir, state_dir=state_dir)
    (target_dir / "AGENTS.md").unlink()

    resp = client.get("/api/session")

    assert resp.json() == {"target_dir": None}


def test_start_session_requires_workspace():
    resp = client.post(
        "/api/session/start",
        json={"workspace_dir": "", "api_key": "sk-x"},
    )

    assert resp.status_code == 400
    assert "obligatoire" in resp.json()["detail"]


def test_start_session_requires_api_key():
    resp = client.post(
        "/api/session/start",
        json={"workspace_dir": "/tmp/nowhere", "api_key": ""},
    )

    assert resp.status_code == 400
    assert "cle API" in resp.json()["detail"]


def test_start_session_requires_opencode(monkeypatch):
    monkeypatch.setattr(routes_session, "_find_opencode", lambda: None)

    resp = client.post(
        "/api/session/start",
        json={"workspace_dir": "/tmp/nowhere", "api_key": "sk-x"},
    )

    assert resp.status_code == 400
    assert "opencode" in resp.json()["detail"]


def test_start_session_rejects_unknown_model_when_empty_and_no_prefix(monkeypatch):
    monkeypatch.setattr(routes_session, "_find_opencode", lambda: "/usr/bin/opencode")

    resp = client.post(
        "/api/session/start",
        json={
            "workspace_dir": "/tmp/nowhere",
            "api_key": "sk-x",
            "provider": "Autre (custom)",
            "model": "",
        },
    )

    assert resp.status_code == 400
    assert "modele" in resp.json()["detail"]


def test_start_session_reports_opencode_validation_error(monkeypatch):
    monkeypatch.setattr(routes_session, "_find_opencode", lambda: "/usr/bin/opencode")
    monkeypatch.setattr(routes_session, "_validate_opencode", lambda model: "cle invalide")

    resp = client.post(
        "/api/session/start",
        json={"workspace_dir": "/tmp/nowhere", "api_key": "sk-x"},
    )

    assert resp.status_code == 400
    assert "cle invalide" in resp.json()["detail"]


def test_start_session_success_persists_last_session(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "ws"
    saved = {}

    monkeypatch.setattr(routes_session, "_find_opencode", lambda: "/usr/bin/opencode")
    monkeypatch.setattr(routes_session, "_validate_opencode", lambda model: "")
    monkeypatch.setattr(routes_session, "init_repo", lambda d: True)
    monkeypatch.setattr(routes_session, "configure_git", lambda *a, **k: None)
    monkeypatch.setattr(routes_session, "ensure_gitignore", lambda d: None)
    monkeypatch.setattr(routes_session, "audit_hardware", lambda: object())
    monkeypatch.setattr(routes_session, "format_for_agent", lambda hw: "CPU: 1")
    monkeypatch.setattr(routes_session.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(routes_session, "save_last_session", lambda d: saved.setdefault("dir", d))

    resp = client.post(
        "/api/session/start",
        json={
            "workspace_dir": str(workspace),
            "instructions": "Objectif",
            "provider": "Autre (custom)",
            "model": "opencode/free-model",
            "api_key": "unused",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "Session lancee" in data["message"]
    assert data["target_dir"] == str(workspace)
    assert saved["dir"] == workspace
    # Fichiers d'etat crees par init_project_state (non mocke : c'est
    # la logique reutilisee de src/core/state.py qu'on veut verifier).
    assert (workspace / "AGENTS.md").exists()
    assert (workspace / "PROGRESS.md").exists()


def test_start_session_rejects_nonempty_workspace_without_repo(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "already_here.txt").write_text("residu")

    monkeypatch.setattr(routes_session, "_find_opencode", lambda: "/usr/bin/opencode")
    monkeypatch.setattr(routes_session, "_validate_opencode", lambda model: "")

    resp = client.post(
        "/api/session/start",
        json={"workspace_dir": str(workspace), "api_key": "sk-x"},
    )

    assert resp.status_code == 400
    assert "n'est pas vide" in resp.json()["detail"]
