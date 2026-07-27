"""Routes du tableau de bord et des onglets Markdown dedies.

Reprend telle quelle la logique de ``src/gui/dashboard.py::_get_dashboard_data``
(resume LLM, PROGRESS.md parse, alertes) et l'expose en JSON plutot
qu'en sorties Gradio. ``/api/progress`` et ``/api/benchmarks``
renvoient le contenu brut des fichiers pour les onglets dedies
(section 4 du cahier des charges).
"""

from pathlib import Path

from fastapi import APIRouter, Query

from src.core.log_summarizer import summarize_logs
from src.core.state import read_state
from src.utils.markdown_parser import parse_alerts, parse_benchmarks, parse_progress
from src.utils.text import read_log_tail

router = APIRouter()

_NO_SESSION_DASHBOARD = {
    "activity_text": "*Aucune session active.*",
    "system_alerts": "",
    "progress_text": "*Aucune session active.*",
    "benchmarks": [],
    "alerts_text": "*Aucune alerte.*",
}


@router.get("/api/dashboard")
def get_dashboard(target_dir: str = Query(...)) -> dict:
    """Donnees du tableau de bord (resume, progression, alertes)."""
    if not target_dir.strip():
        return _NO_SESSION_DASHBOARD
    return _get_dashboard_data(Path(target_dir))


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

    alerts_list = parse_alerts(progress_md)
    alerts_text = (
        "\n".join(f"- **{a['keyword']}** : {a['line']}" for a in alerts_list)
        if alerts_list
        else "*Aucune alerte detectee.*"
    )

    return {
        "activity_text": activity_text,
        "system_alerts": sys_alert_text,
        "progress_text": progress_text or "*En attente de la premiere iteration...*",
        "benchmarks": benchmarks,
        "alerts_text": alerts_text,
    }
