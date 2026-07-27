"""Point d'entree principal de l'interface web DeBuilder.

Lance l'application FastAPI sur le port 7680 (ou DEBUILDER_PORT),
remplacant l'ancienne interface Gradio (src/app.py + src/gui/*.py)
par une page unique HTML/JS vanilla servie en statique, avec des
routes API REST/SSE en lieu et place des callbacks Gradio.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.web.routes_control import router as control_router
from src.web.routes_dashboard import router as dashboard_router
from src.web.routes_logs import router as logs_router
from src.web.routes_requests import router as requests_router
from src.web.routes_session import router as session_router

PORT = int(os.environ.get("DEBUILDER_PORT", "7680"))
TITLE = "DeBuilder - Orchestrateur OpenCode"

_STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title=TITLE)

app.include_router(session_router)
app.include_router(dashboard_router)
app.include_router(control_router)
app.include_router(requests_router)
app.include_router(logs_router)

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    """Sert la page unique du frontend."""
    return FileResponse(_STATIC_DIR / "index.html")


def main() -> None:
    """Fonction principale. Lance le serveur uvicorn."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
