"""Onglet Requetes de l'Agent (Escalade Non-Bloquante).

Affiche les demandes de ressources emises par l'agent
et permet a l'utilisateur d'y repondre.
"""

from pathlib import Path

import gradio as gr

from src.core.state import read_state, append_state


def build_agents_tab(target_dir_state: gr.State) -> gr.TabItem:
    """Construit l'onglet des requetes agent."""
    with gr.TabItem("Requetes Agent") as tab:
        gr.Markdown("## Requetes de l'Agent")
        gr.Markdown(
            "L'agent peut demander des ressources supplementaires (bonus). "
            "Il ne bloque jamais en attendant votre reponse."
        )

        requests_display = gr.Markdown(value="*Aucune demande en attente.*")

        refresh_req_btn = gr.Button("Rafraichir les demandes", variant="secondary")

        gr.Markdown("### Votre reponse")
        response_input = gr.Textbox(
            label="Reponse a la demande",
            placeholder="Acces accorde / Ressource fournie...",
            lines=3,
        )
        respond_btn = gr.Button("Repondre", variant="secondary")
        respond_status = gr.Markdown("")

        refresh_req_btn.click(
            fn=_get_requests,
            inputs=[target_dir_state],
            outputs=[requests_display],
        )

        respond_btn.click(
            fn=_respond_to_request,
            inputs=[target_dir_state, response_input],
            outputs=[respond_status],
        )

    return tab


def _get_requests(target_dir_str: str) -> str:
    if not target_dir_str:
        return "*Aucune session active.*"
    target_dir = Path(target_dir_str)
    content = read_state(target_dir, "RESOURCES_NEEDED.md")
    return content or "*Aucune demande en attente.*"


def _respond_to_request(target_dir_str: str, response: str) -> str:
    if not target_dir_str:
        return "**Erreur :** Aucune session active."
    if not response.strip():
        return "**Erreur :** Reponse vide."
    target_dir = Path(target_dir_str)
    append_state(target_dir, "SUGGESTIONS.md", f"[Ressource] {response.strip()}\n\n")
    return "Reponse envoyee. L'agent en sera informe."
