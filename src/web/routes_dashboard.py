"""Routes du tableau de bord et des onglets Markdown dedies.

Reprend telle quelle la logique de ``src/gui/dashboard.py::_get_dashboard_data``
(resume LLM, PROGRESS.md parse, alertes) et l'expose en JSON plutot
qu'en sorties Gradio. ``/api/progress`` et ``/api/benchmarks``
renvoient le contenu brut des fichiers pour les onglets dedies
(section 4 du cahier des charges).
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from src.core.circuit_breaker import load_breaker_state
from src.core.iterations import read_entries
from src.core.log_summarizer import summarize_logs
from src.core.loop_status import compute_loop_status
from src.core.state import read_state
from src.utils.markdown_parser import (
    parse_alerts,
    parse_benchmarks,
    parse_coverage,
    parse_progress,
)
from src.utils.text import read_log_tail

router = APIRouter()

_MAX_DASHBOARD_ALERTS = 2

# Bornes de lecture du journal d'iterations : le tableau de bord ne
# charge jamais plus de lignes que necessaire au graphe et aux stats.
_DEFAULT_ITERATIONS_LIMIT = 100
_MAX_ITERATIONS_LIMIT = 1000

_NO_SESSION_DASHBOARD = {
    "activity_text": "*Aucune session active.*",
    "system_alerts": "",
    "progress_text": "*Aucune session active.*",
    "benchmarks": [],
    "alerts_text": "*Aucune alerte.*",
    "circuit_breaker": None,
    "loop_status": {"state": "none"},
    "coverage": None,
}


@router.get("/api/dashboard")
def get_dashboard(target_dir: str = Query(...)) -> dict:
    """Donnees du tableau de bord (resume, progression, alertes)."""
    if not target_dir.strip():
        return _NO_SESSION_DASHBOARD
    return _get_dashboard_data(Path(target_dir))


@router.get("/api/iterations")
def get_iterations(
    target_dir: str = Query(...),
    limit: int = Query(default=_DEFAULT_ITERATIONS_LIMIT, ge=1, le=_MAX_ITERATIONS_LIMIT),
) -> dict:
    """Lignes du journal ITERATIONS.jsonl (cdc §5.2), les plus recentes.

    Une ligne par iteration : horodatage, sessions (type, modele,
    duree, code de sortie), taille du diff, tests parses, flag no-op,
    tags. Les lignes corrompues sont ignorees a la lecture.

    Args:
        target_dir: Repertoire du projet cible.
        limit: Nombre maximum de lignes retournees (les plus recentes).

    Returns:
        ``{"entries": [...], "total": n}`` ou ``total`` est le nombre
        total de lignes valides du journal (non borne).
    """
    if not target_dir.strip():
        raise HTTPException(400, "Aucune session active.")
    entries = read_entries(Path(target_dir))
    return {"entries": entries[-limit:], "total": len(entries)}


@router.get("/api/progress")
def get_progress(target_dir: str = Query(...)) -> dict:
    """Contenu brut de PROGRESS.md, pour l'onglet dedie."""
    return {"content": read_state(Path(target_dir), "PROGRESS.md")}


@router.get("/api/benchmarks")
def get_benchmarks(target_dir: str = Query(...)) -> dict:
    """Contenu brut de BENCHMARKS.md, pour l'onglet dedie."""
    return {"content": read_state(Path(target_dir), "BENCHMARKS.md")}


def _get_dashboard_data(target_dir: Path) -> dict:
    log_tail = read_log_tail(target_dir, "OPENCODE_LOG.txt", 200)
    activity_summary = summarize_logs(log_tail, cache_key=str(target_dir))
    activity_text = activity_summary.text
    if activity_summary.warning:
        activity_text = f"> :warning: **{activity_summary.warning}**\n\n{activity_text}"

    progress_md = read_state(target_dir, "PROGRESS.md")
    benches_md = read_state(target_dir, "BENCHMARKS.md")

    sys_alert_text = ""
    if "ECHEC" in progress_md or "Erreur" in progress_md:
        lines = []
        if "ECHEC" in progress_md:
            lines.append(
                "> :warning: **L'agent rencontre des echecs repetes. Verifiez la configuration OpenCode (cle API, modele) dans l'onglet Configuration.**"
            )
        if "opencode" not in progress_md.lower() and any(
            kw in progress_md.lower()
            for kw in ["introuvable", "not found", "command not found"]
        ):
            lines.append(
                "> :x: **OpenCode est introuvable.** Installez-le : `curl -fsSL https://opencode.ai/install | bash`"
            )
        sys_alert_text = "\n\n".join(lines)

    breaker_state = load_breaker_state()
    if breaker_state.get("tripped"):
        breaker_alert = (
            "> :warning: **Circuit breaker API ouvert** : "
            f"{breaker_state['api_failures']} echec(s) API, pause en cours "
            f"(~{int(breaker_state['pause_remaining_seconds'])}s restantes)."
        )
        if breaker_state.get("fallback_model"):
            breaker_alert += f" Bascule sur le modele de secours `{breaker_state['fallback_model']}`."
        if sys_alert_text:
            sys_alert_text = breaker_alert + "\n\n" + sys_alert_text
        else:
            sys_alert_text = breaker_alert

    progress_data = parse_progress(progress_md)
    latest = progress_data.get("latest_iteration", "*En attente...*")
    next_task = progress_data.get("next_task", "")
    prev_iterations = progress_data.get("previous_iterations", [])

    progress_text = f"### Derniere iteration\n\n{latest}\n\n"
    if prev_iterations:
        progress_text += "### Iterations precedentes\n\n"
        for pi in prev_iterations:
            progress_text += f"{pi}\n\n"
    if next_task:
        progress_text += f"### Prochaine tache\n\n{next_task}\n"

    benchmarks = parse_benchmarks(benches_md)

    # Ne garder que les alertes les plus recentes : sur une session
    # longue, parse_alerts() renvoie une entree par occurrence de
    # mot-cle dans tout PROGRESS.md, ce qui gonfle l'encadre de facon
    # disproportionnee au fil des iterations.
    alerts_list = sorted(parse_alerts(progress_md), key=lambda a: a["position"])
    recent_alerts = alerts_list[-_MAX_DASHBOARD_ALERTS:]
    alerts_text = (
        "\n".join(f"- **{a['keyword']}** : {a['line']}" for a in recent_alerts)
        if recent_alerts
        else "*Aucune alerte detectee.*"
    )

    return {
        "activity_text": activity_text,
        "system_alerts": sys_alert_text,
        "progress_text": progress_text or "*En attente de la premiere iteration...*",
        "benchmarks": benchmarks,
        "alerts_text": alerts_text,
        "circuit_breaker": breaker_state,
        "loop_status": compute_loop_status(target_dir),
        "coverage": parse_coverage(read_state(target_dir, "SPEC_COVERAGE.md")),
    }
