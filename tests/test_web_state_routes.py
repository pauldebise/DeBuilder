"""Tests pour les routes de controle et de requetes (routes_control.py,
routes_requests.py) : verifie que les effets fichiers sont identiques
a ceux produits par les anciens callbacks Gradio (src/gui/control.py,
src/gui/agents.py), qui appelaient les memes fonctions de
src/core/state.py et src/core/git.py.
"""

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from src.core.git import commit_all, init_repo
from src.web.app import app

client = TestClient(app)


def _init_test_repo(repo_dir: Path) -> None:
    assert init_repo(repo_dir)
    _run(repo_dir, "config", "user.email", "test@test.com")
    _run(repo_dir, "config", "user.name", "Test")


def _run(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
    )


# --- Suggestions -----------------------------------------------------------


def test_send_suggestion_appends_to_file(tmp_path: Path):
    resp = client.post(
        "/api/suggestions",
        json={"target_dir": str(tmp_path), "message": "Attention a la memoire"},
    )

    assert resp.status_code == 200
    assert "Suggestion envoyee" in resp.json()["message"]
    assert "> Attention a la memoire" in (tmp_path / "SUGGESTIONS.md").read_text()


def test_send_suggestion_requires_target_dir():
    resp = client.post("/api/suggestions", json={"target_dir": "", "message": "x"})
    assert resp.status_code == 400


def test_send_suggestion_requires_message(tmp_path: Path):
    resp = client.post("/api/suggestions", json={"target_dir": str(tmp_path), "message": "   "})
    assert resp.status_code == 400


# --- Kill-switch -------------------------------------------------------------


def test_kill_switch_creates_done_file(tmp_path: Path):
    resp = client.post("/api/control/kill", json={"target_dir": str(tmp_path)})

    assert resp.status_code == 200
    assert (tmp_path / "DONE").exists()


def test_kill_switch_requires_target_dir():
    resp = client.post("/api/control/kill", json={"target_dir": ""})
    assert resp.status_code == 400


# --- Rollback ----------------------------------------------------------------


def test_rollback_reverts_last_commit(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)
    (repo_dir / "file1.txt").write_text("version 1")
    commit_all(repo_dir, "commit 1")
    (repo_dir / "file1.txt").write_text("version 2")
    commit_all(repo_dir, "commit 2")

    resp = client.post("/api/control/rollback", json={"target_dir": str(repo_dir)})

    assert resp.status_code == 200
    assert (repo_dir / "file1.txt").read_text() == "version 1"


def test_rollback_fails_without_prior_commit(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)
    (repo_dir / "file1.txt").write_text("seule version")
    commit_all(repo_dir, "unique commit")

    resp = client.post("/api/control/rollback", json={"target_dir": str(repo_dir)})

    assert resp.status_code == 400
    assert (repo_dir / "file1.txt").read_text() == "seule version"


def test_rollback_requires_target_dir():
    resp = client.post("/api/control/rollback", json={"target_dir": ""})
    assert resp.status_code == 400


def test_rollback_to_tag_reverts(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)
    (repo_dir / "f.txt").write_text("v1")
    commit_all(repo_dir, "c1")
    _run(repo_dir, "tag", "debuilder/iter-0001")
    (repo_dir / "f.txt").write_text("v2")
    commit_all(repo_dir, "c2")

    resp = client.post(
        "/api/control/rollback",
        json={"target_dir": str(repo_dir), "to": "debuilder/iter-0001"},
    )

    assert resp.status_code == 200
    assert (repo_dir / "f.txt").read_text() == "v1"


def test_rollback_to_unknown_tag_returns_400(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)
    (repo_dir / "f.txt").write_text("v1")
    commit_all(repo_dir, "c1")

    resp = client.post(
        "/api/control/rollback",
        json={"target_dir": str(repo_dir), "to": "debuilder/iter-9999"},
    )

    assert resp.status_code == 400


def test_list_tags_returns_iteration_tags(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _init_test_repo(repo_dir)
    (repo_dir / "f.txt").write_text("v1")
    commit_all(repo_dir, "c1")
    _run(repo_dir, "tag", "debuilder/iter-0001")

    resp = client.get("/api/tags", params={"target_dir": str(repo_dir)})

    assert resp.status_code == 200
    assert resp.json() == {"tags": ["debuilder/iter-0001"]}


def test_list_tags_requires_target_dir():
    resp = client.get("/api/tags", params={"target_dir": ""})
    assert resp.status_code == 400


# --- Circuit breaker (dashboard) ---------------------------------------------


def test_dashboard_exposes_circuit_breaker_field(tmp_path: Path):
    resp = client.get("/api/dashboard", params={"target_dir": str(tmp_path)})

    assert resp.status_code == 200
    assert resp.json()["circuit_breaker"]["tripped"] is False


def test_dashboard_exposes_loop_status(tmp_path: Path, monkeypatch):
    import src.web.routes_dashboard as routes_dashboard

    monkeypatch.setattr(
        routes_dashboard,
        "compute_loop_status",
        lambda target_dir: {"state": "active", "iteration": 4, "since_seconds": 60},
    )

    resp = client.get("/api/dashboard", params={"target_dir": str(tmp_path)})

    assert resp.status_code == 200
    assert resp.json()["loop_status"] == {
        "state": "active",
        "iteration": 4,
        "since_seconds": 60,
    }


def test_dashboard_without_session_exposes_none_loop_status():
    resp = client.get("/api/dashboard", params={"target_dir": ""})

    assert resp.status_code == 200
    assert resp.json()["loop_status"] == {"state": "none"}


def test_dashboard_exposes_coverage_from_spec(tmp_path: Path):
    (tmp_path / "SPEC_COVERAGE.md").write_text(
        "**Couverture : 2 / 4 items implementes et testes.**",
        encoding="utf-8",
    )

    resp = client.get("/api/dashboard", params={"target_dir": str(tmp_path)})

    assert resp.status_code == 200
    assert resp.json()["coverage"] == {"done": 2, "total": 4, "percent": 50}


def test_dashboard_exposes_coverage_none_without_spec(tmp_path: Path):
    resp = client.get("/api/dashboard", params={"target_dir": str(tmp_path)})

    assert resp.status_code == 200
    assert resp.json()["coverage"] is None


# --- Journal d'iterations (route /api/iterations, cdc §5.2) ------------------


def test_get_iterations_empty_when_no_journal(tmp_path: Path):
    resp = client.get("/api/iterations", params={"target_dir": str(tmp_path)})

    assert resp.status_code == 200
    assert resp.json() == {"entries": [], "total": 0}


def test_get_iterations_returns_entries_bounded_by_limit(tmp_path: Path):
    from src.core.iterations import append_entry

    for i in range(5):
        append_entry(tmp_path, {"iteration": i + 1, "exit_code": 0})

    resp = client.get(
        "/api/iterations", params={"target_dir": str(tmp_path), "limit": 2}
    )

    data = resp.json()
    assert data["total"] == 5
    assert [e["iteration"] for e in data["entries"]] == [4, 5]


def test_get_iterations_skips_corrupt_lines(tmp_path: Path):
    from src.core.iterations import append_entry, journal_path

    append_entry(tmp_path, {"iteration": 1, "exit_code": 0})
    with journal_path(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write("{ligne corrompue\n")
    append_entry(tmp_path, {"iteration": 2, "exit_code": 0})

    resp = client.get("/api/iterations", params={"target_dir": str(tmp_path)})

    data = resp.json()
    assert data["total"] == 2
    assert [e["iteration"] for e in data["entries"]] == [1, 2]


def test_get_iterations_rejects_out_of_range_limit(tmp_path: Path):
    resp = client.get(
        "/api/iterations", params={"target_dir": str(tmp_path), "limit": 5000}
    )
    assert resp.status_code == 422


def test_get_iterations_requires_target_dir():
    resp = client.get("/api/iterations", params={"target_dir": ""})
    assert resp.status_code == 400


def test_dashboard_alerts_when_breaker_tripped(tmp_path: Path, monkeypatch):
    from src.core.circuit_breaker import CircuitBreaker

    state_dir = tmp_path / "state"
    monkeypatch.setenv("DEBUILDER_STATE_DIR", str(state_dir))
    breaker = CircuitBreaker(state_dir=state_dir)
    for _ in range(breaker.max_failures):
        breaker.record_failure("api")

    resp = client.get("/api/dashboard", params={"target_dir": str(tmp_path / "project")})

    data = resp.json()
    assert data["circuit_breaker"]["tripped"] is True
    assert "Circuit breaker" in data["system_alerts"]


# --- Barrieres ---------------------------------------------------------------


def test_enable_and_disable_barrier(tmp_path: Path):
    enable_resp = client.post(
        "/api/control/barrier",
        json={"target_dir": str(tmp_path), "barrier_type": "entrainement", "enabled": True},
    )
    assert enable_resp.status_code == 200
    assert (tmp_path / "BARRIER_ENTRAINEMENT").exists()

    disable_resp = client.post(
        "/api/control/barrier",
        json={"target_dir": str(tmp_path), "barrier_type": "entrainement", "enabled": False},
    )
    assert disable_resp.status_code == 200
    assert not (tmp_path / "BARRIER_ENTRAINEMENT").exists()


def test_disable_barrier_is_noop_when_absent(tmp_path: Path):
    resp = client.post(
        "/api/control/barrier",
        json={"target_dir": str(tmp_path), "barrier_type": "deploiement", "enabled": False},
    )
    assert resp.status_code == 200


# --- Requetes agent -----------------------------------------------------------


def test_get_requests_returns_placeholder_when_empty(tmp_path: Path):
    (tmp_path / "RESOURCES_NEEDED.md").write_text("")

    resp = client.get("/api/requests", params={"target_dir": str(tmp_path)})

    assert resp.status_code == 200
    assert resp.json() == {"content": "*Aucune demande en attente.*"}


def test_get_requests_returns_content(tmp_path: Path):
    (tmp_path / "RESOURCES_NEEDED.md").write_text("GPU supplementaire demande.")

    resp = client.get("/api/requests", params={"target_dir": str(tmp_path)})

    assert resp.json() == {"content": "GPU supplementaire demande."}


def test_respond_to_request_appends_to_suggestions(tmp_path: Path):
    resp = client.post(
        "/api/requests/respond",
        json={"target_dir": str(tmp_path), "response": "Acces accorde"},
    )

    assert resp.status_code == 200
    assert "[Ressource] Acces accorde" in (tmp_path / "SUGGESTIONS.md").read_text()


def test_respond_to_request_requires_response(tmp_path: Path):
    resp = client.post(
        "/api/requests/respond",
        json={"target_dir": str(tmp_path), "response": ""},
    )
    assert resp.status_code == 400
