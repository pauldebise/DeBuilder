"""Onglet Centre de Controle (Intervention Asynchrone).

Permet a l'utilisateur de:
- Envoyer des suggestions via SUGGESTIONS.md
- Activer le kill-switch (creation du fichier DONE)
- Effectuer un rollback (git reset --hard HEAD~1)
- Configurer des barrieres (Human-in-the-Loop)
"""

from pathlib import Path

import gradio as gr

from src.core.state import append_state, touch_done
from src.core.git import rollback_last


def build_control_tab(target_dir_state: gr.State) -> gr.TabItem:
    """Construit l'onglet centre de controle."""
    with gr.TabItem("Centre de Controle") as tab:
        gr.Markdown("## Centre de Controle")

        with gr.Row():
            with gr.Column():
                gr.Markdown("### Boite aux lettres (Suggestions)")
                suggestion_input = gr.Textbox(
                    label="Votre message",
                    placeholder="Suggestion, directive ou observation...",
                    lines=4,
                )
                send_btn = gr.Button("Envoyer", variant="secondary")
                suggestion_status = gr.Markdown("")

            with gr.Column():
                gr.Markdown("### Actions")
                kill_btn = gr.Button("Arret d'urgence (Kill-Switch)", variant="stop")
                kill_status = gr.Markdown("")

                rollback_btn = gr.Button("Rollback (HEAD~1)", variant="secondary")
                rollback_status = gr.Markdown("")

        gr.Markdown("---")
        gr.Markdown("### Barrieres (Human-in-the-Loop)")

        with gr.Row():
            barrier_type = gr.Dropdown(
                label="Type d'operation",
                choices=["entrainement", "deploiement", "modification_critique"],
                value="entrainement",
            )
            with gr.Column(scale=1):
                enable_barrier_btn = gr.Button("Activer", variant="secondary")
                disable_barrier_btn = gr.Button("Desactiver", variant="secondary")
            barrier_status = gr.Markdown("")

        send_btn.click(
            fn=_send_suggestion,
            inputs=[target_dir_state, suggestion_input],
            outputs=[suggestion_status],
        )

        kill_btn.click(
            fn=_activate_kill_switch,
            inputs=[target_dir_state],
            outputs=[kill_status],
        )

        rollback_btn.click(
            fn=_do_rollback,
            inputs=[target_dir_state],
            outputs=[rollback_status],
        )

        enable_barrier_btn.click(
            fn=lambda td, bt: _set_barrier(td, bt, True),
            inputs=[target_dir_state, barrier_type],
            outputs=[barrier_status],
        )

        disable_barrier_btn.click(
            fn=lambda td, bt: _set_barrier(td, bt, False),
            inputs=[target_dir_state, barrier_type],
            outputs=[barrier_status],
        )

    return tab


def _send_suggestion(target_dir_str: str, message: str) -> str:
    if not target_dir_str:
        return "**Erreur :** Aucune session active."
    if not message.strip():
        return "**Erreur :** Message vide."
    target_dir = Path(target_dir_str)
    append_state(target_dir, "SUGGESTIONS.md", f"> {message.strip()}\n\n")
    return "Suggestion envoyee. L'agent la lira a la prochaine iteration."


def _activate_kill_switch(target_dir_str: str) -> str:
    if not target_dir_str:
        return "**Erreur :** Aucune session active."
    target_dir = Path(target_dir_str)
    touch_done(target_dir)
    return "**Kill-switch active.** L'agent s'arretera a la fin de l'iteration en cours."


def _do_rollback(target_dir_str: str) -> str:
    if not target_dir_str:
        return "**Erreur :** Aucune session active."
    target_dir = Path(target_dir_str)
    success = rollback_last(target_dir)
    if success:
        return "**Rollback effectue.** Le dernier commit a ete annule (HEAD~1)."
    return "**Erreur :** Le rollback a echoue."


def _set_barrier(target_dir_str: str, barrier_type: str, enabled: bool) -> str:
    if not target_dir_str:
        return "**Erreur :** Aucune session active."
    target_dir = Path(target_dir_str)
    barrier_file = target_dir / f"BARRIER_{barrier_type.upper()}"
    if enabled:
        barrier_file.touch(exist_ok=True)
        return f"Barriere activee pour `{barrier_type}`."
    else:
        if barrier_file.exists():
            barrier_file.unlink()
        return f"Barriere desactivee pour `{barrier_type}`."
