"""Routes des Requetes de l'Agent (Escalade Non-Bloquante).

Miroir de ``src/gui/agents.py`` : lecture de RESOURCES_NEEDED.md et
reponse ecrite dans SUGGESTIONS.md.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.core.state import append_state, read_state

router = APIRouter()


class RequestResponseBody(BaseModel):
    target_dir: str
    response: str


@router.get("/api/requests")
def get_requests(target_dir: str = Query(...)) -> dict:
    """Contenu de RESOURCES_NEEDED.md (demandes de ressources en attente)."""
    if not target_dir.strip():
        return {"content": "*Aucune session active.*"}
    content = read_state(Path(target_dir), "RESOURCES_NEEDED.md")
    return {"content": content or "*Aucune demande en attente.*"}


@router.post("/api/requests/respond")
def respond_to_request(payload: RequestResponseBody) -> dict:
    """Repond a une demande de ressource (ecrit dans SUGGESTIONS.md)."""
    if not payload.target_dir.strip():
        raise HTTPException(400, "Aucune session active.")
    if not payload.response.strip():
        raise HTTPException(400, "Reponse vide.")
    append_state(
        Path(payload.target_dir),
        "SUGGESTIONS.md",
        f"[Ressource] {payload.response.strip()}\n\n",
    )
    return {"message": "Reponse envoyee. L'agent en sera informe."}
