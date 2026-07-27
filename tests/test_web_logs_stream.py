"""Tests pour le flux SSE des logs (src/web/routes_logs.py).

Pilote directement le generateur ``_tail_and_follow`` (plutot que de
passer par une connexion HTTP streamee reelle) pour garder un controle
precis et deterministe sur la deconnexion client et la simulation de
rotation, sans dependre d'un thread d'arriere-plan indefini.
"""

import asyncio
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from src.web.app import app
from src.web.routes_logs import _tail_and_follow

client = TestClient(app)


class _FakeRequest:
    """Simule ``fastapi.Request.is_disconnected()`` de facon controlee."""

    def __init__(self, disconnect_after: int | None = None):
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._calls += 1
        if self._disconnect_after is not None and self._calls > self._disconnect_after:
            return True
        return False


def test_sends_immediate_backlog(tmp_path: Path):
    (tmp_path / "OPENCODE_LOG.txt").write_text("ligne 1\nligne 2")

    async def scenario():
        gen = _tail_and_follow(_FakeRequest(), str(tmp_path))
        events = [await gen.__anext__(), await gen.__anext__()]
        await gen.aclose()
        return events

    events = asyncio.run(scenario())
    assert events == ["data: ligne 1\n\n", "data: ligne 2\n\n"]


def test_follows_growth_then_handles_rotation_without_crash_or_duplicate(tmp_path: Path):
    # Chaque ligne se termine par un retour a la ligne, comme le fait
    # reellement l'agent (cf. src/loop/agent.py:449-452, qui ecrit
    # chaque ligne de la sortie d'OpenCode telle qu'iteree sur
    # proc.stdout, deja terminee par \n).
    log_file = tmp_path / "OPENCODE_LOG.txt"
    log_file.write_text("ligne 1\n")

    async def scenario():
        gen = _tail_and_follow(_FakeRequest(), str(tmp_path))
        # Tail initial : "ligne 1" puis un evenement vide (artefact du
        # \n final du fichier, cf. read_log_tail/split("\n")).
        events = [await gen.__anext__(), await gen.__anext__()]

        with open(log_file, "a") as f:
            f.write("ligne 2\n")  # le fichier grossit (append reel)
        events.append(await gen.__anext__())  # "ligne 2" en direct

        # Rotation : le fichier retrecit (troncature, cf.
        # _rotate_log_if_large dans src/loop/agent.py). Le flux doit
        # repartir d'un offset zero au lieu de planter ou dupliquer.
        log_file.write_text("apres rotation\n")
        events.append(await gen.__anext__())

        await gen.aclose()
        return events

    events = asyncio.run(scenario())
    assert events == [
        "data: ligne 1\n\n",
        "data: \n\n",
        "data: ligne 2\n\n",
        "data: apres rotation\n\n",
    ]


def test_buffers_partial_lines_across_polls(tmp_path: Path):
    """Une ligne coupee entre deux cycles de poll n'est emise qu'une
    fois completee (pas de fragment sans \\n envoye au client).
    """
    log_file = tmp_path / "OPENCODE_LOG.txt"
    log_file.write_text("")

    async def scenario():
        gen = _tail_and_follow(_FakeRequest(), str(tmp_path))
        await gen.__anext__()  # tail initial (fichier vide)

        loop = asyncio.get_running_loop()
        # Complete la ligne pendant le 2e cycle de poll du generateur
        # (apres le 1er sleep, avant le 2e) : le fragment ecrit ci-dessous
        # doit rester en tampon jusque-la, sans etre emis premature.
        loop.call_later(0.55, log_file.write_text, "ligne incomplete\nsuite")
        log_file.write_text("ligne incomplete")

        event = await gen.__anext__()
        await gen.aclose()
        return event

    event = asyncio.run(scenario())
    assert event == "data: ligne incomplete\n\n"


def test_masks_secret_in_streamed_line(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAKE_TEST_API_KEY", "supersecretvalue123456")
    (tmp_path / "OPENCODE_LOG.txt").write_text("contient supersecretvalue123456 ici")

    async def scenario():
        gen = _tail_and_follow(_FakeRequest(), str(tmp_path))
        event = await gen.__anext__()
        await gen.aclose()
        return event

    event = asyncio.run(scenario())
    assert "supersecretvalue123456" not in event
    assert "***" in event


def test_no_active_session_yields_placeholder_and_stops():
    async def scenario():
        gen = _tail_and_follow(_FakeRequest(), "")
        first = await gen.__anext__()
        stopped = False
        try:
            await gen.__anext__()
        except StopAsyncIteration:
            stopped = True
        return first, stopped

    first, stopped = asyncio.run(scenario())
    assert first == "data: *Aucune session active.*\n\n"
    assert stopped


def test_stops_immediately_when_client_already_disconnected(tmp_path: Path):
    (tmp_path / "OPENCODE_LOG.txt").write_text("ligne 1")

    async def scenario():
        gen = _tail_and_follow(_FakeRequest(disconnect_after=0), str(tmp_path))
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    asyncio.run(scenario())


# --- Plomberie de la route FastAPI (wiring, pas la boucle infinie) --------


def test_route_wires_target_dir_and_media_type(monkeypatch):
    import src.web.routes_logs as routes_logs

    captured = {}

    async def _fake_tail_and_follow(request, target_dir_str):
        captured["target_dir"] = target_dir_str
        yield "data: hello\n\n"
        yield "data: world\n\n"

    monkeypatch.setattr(routes_logs, "_tail_and_follow", _fake_tail_and_follow)

    resp = client.get("/api/logs/stream", params={"target_dir": "/tmp/exemple"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.text == "data: hello\n\ndata: world\n\n"
    assert captured["target_dir"] == "/tmp/exemple"


def test_route_requires_target_dir_param():
    resp = client.get("/api/logs/stream")
    assert resp.status_code == 422
