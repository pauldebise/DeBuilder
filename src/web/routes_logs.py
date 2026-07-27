"""Flux de logs OpenCode quasi temps reel (SSE).

Remplace le rafraichissement manuel de ``src/gui/logs.py`` par un
flux Server-Sent Events : envoie immediatement le tail existant de
``OPENCODE_LOG.txt``, puis poll le fichier a intervalle court pour en
streamer la suite, en detectant une rotation (troncature, cf.
``_rotate_log_if_large`` dans ``src/loop/agent.py``) pour repartir
proprement d'un offset zero sans dupliquer ni planter.

``OPENCODE_LOG.txt`` est deja sanitize a l'ecriture (``sanitize_text``
+ ``strip_ansi``, cf. ``src/loop/agent.py`` autour de la ligne 449) :
on ré-applique ici les memes fonctions par defense en profondeur — ce
sont des operations idempotentes sur un texte deja propre, donc pas
de double-sanitization incoherente.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from src.core.secrets import sanitize_text
from src.utils.text import read_log_tail, strip_ansi

router = APIRouter()

_POLL_INTERVAL_SECONDS = 0.4
_TAIL_LINES = 200
_LOG_FILENAME = "OPENCODE_LOG.txt"

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


@router.get("/api/logs/stream")
async def stream_logs(request: Request, target_dir: str = Query(...)) -> StreamingResponse:
    """Flux SSE : tail immediat puis nouvelles lignes en direct."""
    return StreamingResponse(
        _tail_and_follow(request, target_dir),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


async def _tail_and_follow(request: Request, target_dir_str: str):
    if not target_dir_str.strip():
        yield _sse_event("*Aucune session active.*")
        return

    target_dir = Path(target_dir_str)
    log_file = target_dir / _LOG_FILENAME

    for line in read_log_tail(target_dir, _LOG_FILENAME, _TAIL_LINES).split("\n"):
        if await request.is_disconnected():
            return
        yield _sse_event(line)

    offset = log_file.stat().st_size if log_file.exists() else 0
    buffer = ""

    while True:
        if await request.is_disconnected():
            return
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        if not log_file.exists():
            continue

        size = log_file.stat().st_size
        if size < offset:
            # Rotation (troncature) : le fichier a retreci, on repart
            # de zero plutot que de tenter de lire a un offset devenu
            # invalide (ce qui dupliquerait ou planterait la lecture).
            offset = 0
            buffer = ""
        if size == offset:
            continue

        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            chunk = f.read()
        offset = size

        buffer += chunk
        lines = buffer.split("\n")
        buffer = lines.pop()  # fragment incomplet, reporte au prochain tour

        for line in lines:
            if await request.is_disconnected():
                return
            yield _sse_event(line)


def _sse_event(line: str) -> str:
    clean = sanitize_text(strip_ansi(line))
    return f"data: {clean}\n\n"
