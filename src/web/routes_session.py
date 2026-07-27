"""Route de reprise de session.

Miroir de la logique de restauration de session utilisee par
``src/app.py`` (Gradio) au demarrage : la source de verite reste
``~/.debuilder/last_session.txt`` via ``src/core/session.py``.
"""

from fastapi import APIRouter

from src.core.session import load_last_session

router = APIRouter()


@router.get("/api/session")
def get_session() -> dict:
    """Renvoie la session active restauree, ou ``None`` si aucune."""
    target_dir = load_last_session()
    return {"target_dir": str(target_dir) if target_dir else None}
