"""Routes du Centre de Controle (Intervention Asynchrone).

Miroir de ``src/gui/control.py`` : suggestions, kill-switch,
rollback, barrieres (Human-in-the-Loop). Reutilise directement
``src/core/state.py`` et ``src/core/git.py``.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.core.git import list_iteration_tags, rollback_last, rollback_to_tag
from src.core.state import append_state, touch_done

router = APIRouter()


class TargetDirBody(BaseModel):
    target_dir: str


class SuggestionBody(BaseModel):
    target_dir: str
    message: str


class BarrierBody(BaseModel):
    target_dir: str
    barrier_type: str
    enabled: bool


class RollbackBody(BaseModel):
    target_dir: str
    to: str | None = None


@router.post("/api/suggestions")
def send_suggestion(payload: SuggestionBody) -> dict:
    """Envoie une suggestion/directive vers SUGGESTIONS.md."""
    if not payload.target_dir.strip():
        raise HTTPException(400, "Aucune session active.")
    if not payload.message.strip():
        raise HTTPException(400, "Message vide.")
    target_dir = Path(payload.target_dir)
    append_state(target_dir, "SUGGESTIONS.md", f"> {payload.message.strip()}\n\n")
    return {"message": "Suggestion envoyee. L'agent la lira a la prochaine iteration."}


@router.post("/api/control/kill")
def activate_kill_switch(payload: TargetDirBody) -> dict:
    """Active le kill-switch (creation du fichier DONE)."""
    if not payload.target_dir.strip():
        raise HTTPException(400, "Aucune session active.")
    touch_done(Path(payload.target_dir))
    return {"message": "Kill-switch active. L'agent s'arretera a la fin de l'iteration en cours."}


@router.post("/api/control/rollback")
def do_rollback(payload: RollbackBody) -> dict:
    """Annule le dernier commit ou revient a un tag d'iteration.

    ``to`` vide/absent : annule le dernier commit (HEAD~1). ``to``
    defini (ex: ``debuilder/iter-0012``) : reinitialise le depot sur ce
    tag (``git reset --hard <tag>``).
    """
    if not payload.target_dir.strip():
        raise HTTPException(400, "Aucune session active.")
    target_dir = Path(payload.target_dir)

    if payload.to and payload.to.strip():
        tag = payload.to.strip()
        success = rollback_to_tag(target_dir, tag)
        if not success:
            raise HTTPException(400, f"Le rollback vers le tag {tag} a echoue.")
        return {"message": f"Rollback effectue vers {tag}."}

    success = rollback_last(target_dir)
    if not success:
        raise HTTPException(400, "Le rollback a echoue.")
    return {"message": "Rollback effectue. Le dernier commit a ete annule (HEAD~1)."}


@router.get("/api/tags")
def list_tags(target_dir: str = Query(...)) -> dict:
    """Liste les tags d'iteration du depot cible (plus recent d'abord)."""
    if not target_dir.strip():
        raise HTTPException(400, "Aucune session active.")
    return {"tags": list_iteration_tags(Path(target_dir))}


@router.post("/api/control/barrier")
def set_barrier(payload: BarrierBody) -> dict:
    """Active ou desactive une barriere Human-in-the-Loop."""
    if not payload.target_dir.strip():
        raise HTTPException(400, "Aucune session active.")
    target_dir = Path(payload.target_dir)
    barrier_file = target_dir / f"BARRIER_{payload.barrier_type.upper()}"
    if payload.enabled:
        barrier_file.touch(exist_ok=True)
        return {"message": f"Barriere activee pour {payload.barrier_type}."}
    if barrier_file.exists():
        barrier_file.unlink()
    return {"message": f"Barriere desactivee pour {payload.barrier_type}."}
