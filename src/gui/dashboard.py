"""Onglet Tableau de Bord (lecture seule).

Affichage en temps reel (polling) de:
- L'etat d'avancement depuis PROGRESS.md
- Les metriques depuis BENCHMARKS.md
- Les alertes watchdog
- Les alertes systeme DeBuilder (crash, erreur wrapper)
"""

from pathlib import Path

import gradio as gr

from src.core.state import read_state
from src.utils.markdown_parser import parse_progress, parse_benchmarks, parse_alerts


def build_dashboard_tab(target_dir_state: gr.State) -> gr.TabItem:
    """Construit l'onglet tableau de bord."""
    with gr.TabItem("Tableau de Bord") as tab:
        gr.Markdown("## Tableau de Bord")

        sys_alerts = gr.Markdown(value="", visible=False)

        progress_display = gr.Markdown(value="*Aucune session active.*")
        benchmarks_display = gr.Dataframe(
            headers=["Parametre", "Valeur"],
            interactive=False,
        )
        alerts_display = gr.Markdown(value="*Aucune alerte.*")

        refresh_btn = gr.Button("Rafraichir", variant="secondary")

        def _refresh(target_dir_str):
            return _get_dashboard_data(target_dir_str)

        refresh_btn.click(
            fn=_refresh,
            inputs=[target_dir_state],
            outputs=[sys_alerts, progress_display, benchmarks_display, alerts_display],
        )

    return tab


def _get_dashboard_data(target_dir_str: str) -> tuple[str, str, list, str]:
    if not target_dir_str:
        return "", "*Aucune session active.*", [], "*Aucune alerte.*"

    target_dir = Path(target_dir_str)

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
    alerts_text = "\n".join(
        f"- **{a['keyword']}** : {a['line']}" for a in alerts_list
    ) if alerts_list else "*Aucune alerte detectee.*"

    return (
        sys_alert_text,
        progress_text or "*En attente de la premiere iteration...*",
        benchmarks,
        alerts_text,
    )
