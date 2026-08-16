# Couverture du Cahier des Charges Pipeline

Mapping cahier des charges (`cahier_des_charges_pipeline.md`) → item
implémenté + test associé. La fin de mission est un fait vérifiable :
couverture 100 % + tests verts.

## Items

| Item du cahier des charges | Implementé dans | Test associé |
|---|---|---|
| §1 Découpage en 2 sessions (Plan lecture seule / Implement) + contrat `TASK.md` + gates déterministes exécutées par la boucle | `src/loop/agent.py` (`run_iteration`, `_web_tools_env`, `_materialize_plan_outputs`, `_check_task_gates`) ; `src/utils/task_parser.py` ; `templates/TASK.md.tmpl` | `tests/test_agent.py`, `tests/test_task_parser.py` |
| §2.1 Session Plan lecture seule, sortie `TASK.md` obligatoire, backlog `PLAN.md`, anti plan-drift | `src/loop/agent.py` (`_build_plan_prompt`, `_gate_state_summary`, `_merge_config_content`) ; `src/core/state.py` | `tests/test_agent.py` (`test_plan_session_is_read_only_and_implement_is_not`, `test_plan_outputs_are_materialized`, `test_missing_task_block_fails_plan`) |
| §2.2 Session Implement : consomme `TASK.md`, Conventional Commits, mise à jour obligatoire de `PROGRESS.md` | `src/loop/agent.py` (`_build_implement_prompt`, `_run_implement_post_steps`) | `tests/test_agent.py` (gates, `test_implement_prompt_contains_task_and_context`) |
| §2.3 Gates déterministes (cases cochées, commande de test relancée, `PROGRESS.md` modifié), motif consigné et injecté | `src/loop/agent.py` (`_check_task_gates`, `_run_test_gate`, `_write_gate_failure`) ; `src/utils/test_results.py` ; `templates/pre-commit.tmpl` | `tests/test_agent.py` (gates en échec/passantes), `tests/test_test_results.py` |
| §3 Fin de mission : proposition via `FINISHED_REPORT.md`, session Review lecture seule, `DONE` créé par la boucle seule ; ancre `SPEC_COVERAGE.md` ; no-op ×N ; caps durs | `src/loop/agent.py` (`_claim_finished`, `_run_review_session`, `touch_done`, `_count_consecutive_noops`) ; `src/loop/agent_loop.sh` (`DEBUILDER_MAX_ITERATIONS`, `DEBUILDER_MAX_HOURS`) ; `src/core/state.py` (`record_cap_stop` dans `agent.py`) | `tests/test_agent.py` (review accepte/refuse, no-ops, caps), `tests/test_e2e_pipeline.py` |
| §4.1 Réparation déterministe de `PROGRESS.md` | `src/core/state.py` (`repair_progress`, `_rebuild_progress`) | `tests/test_state.py`, `tests/test_agent.py` (`test_run_iteration_repairs_corrupt_progress`) |
| §4.2 `ARCHITECTURE.md` séparé avec budget de compaction | `src/core/state.py` (`compact_architecture`, `_compact_arch_content`) ; `templates/ARCHITECTURE.md.tmpl` | `tests/test_state.py` |
| §4.3 Repli mémoire par résumé LLM (jamais le stdout brut) + repli heuristique | `src/core/log_summarizer.py` (`synthesize_progress_entry`) ; `src/loop/agent.py` (`_synthesize_progress_entry`, `_heuristic_progress_entry`) | `tests/test_log_summarizer.py`, `tests/test_agent.py` |
| §4.4 Purge de `SUGGESTIONS.md` uniquement après succès et justification | `src/loop/agent.py` (`_maybe_clear_suggestions`) ; `src/core/state.py` (`clear_suggestions`) | `tests/test_agent.py` (suggestions conservées/vidées) |
| §5.1 Tags git par itération + rollback | `src/core/git.py` (`tag_iteration`, `rollback_to_tag`, `list_iteration_tags`) ; `src/loop/agent.py` (pose du tag) ; `src/web/routes_control.py` (`/api/tags`, `/api/control/rollback`) ; `src/web/static/app.js` | `tests/test_git.py`, `tests/test_web_state_routes.py`, `tests/test_e2e_pipeline.py` |
| §5.2 `ITERATIONS.jsonl` append-only + exploitation (route, graphe, anomalies, alertes) | `src/core/iterations.py` ; `src/loop/agent.py` (`_journal_iteration`, `_session_record`, `_count_consecutive_failures`) ; `src/web/routes_dashboard.py` (`/api/iterations`) ; `src/web/static/app.js` (onglet Itérations) | `tests/test_iterations.py`, `tests/test_agent.py`, `tests/test_web_state_routes.py` |
| §5.3 Résultats de tests parsés par la boucle (JUnit) | `src/utils/test_results.py` (`run_test_gate`, `parse_junit`) ; `src/loop/agent.py` (`_run_test_gate`) | `tests/test_test_results.py`, `tests/test_agent.py` |
| §5.4 Hook pre-commit de tests installé en session vierge uniquement | `src/core/state.py` (`install_test_hook`) ; `templates/pre-commit.tmpl` | `tests/test_state.py` |
| §6 Continuité après échec : fin du transcript injectée, typologie des échecs, backoff, trap de sortie propre, relance tmux | `src/loop/agent.py` (`_recovery_section`, `_RECOVERY_MESSAGES`, `compute_backoff`) ; `src/loop/agent_loop.sh` ; `start.sh` (`DEBUILDER_AUTO_RESTART`) | `tests/test_agent.py` (reprise après échec), `tests/test_e2e_pipeline.py` (timeout) |
| §7 Circuit breaker API : pause croissante, état persisté, bascule de secours, alertes dashboard/webhook | `src/core/circuit_breaker.py` ; `src/loop/agent.py` (`_maybe_pause`, `_select_model`) ; `src/web/routes_dashboard.py` (champ `circuit_breaker`) | `tests/test_circuit_breaker.py`, `tests/test_web_state_routes.py`, `tests/test_e2e_pipeline.py` (erreurs API ×K) |
| §8 Priorités d'implémentation 1→8 | `PLAN_IMPLEMENTATION_PIPELINE.md` (phases 0→9) | Ensemble de la suite (`python -m pytest`) |
| §3.1 CDC original — paramètres avancés de l'écran de configuration (caps, web, modèle de secours, commande de test) | `src/web/routes_session.py` (`SessionStartRequest`, env de la boucle) ; `src/web/static/{index.html,app.js}` | `tests/test_web_session.py` |
| §3.2 CDC original — badge d'état de la boucle (heartbeat + PID persisté) | `src/core/loop_status.py` ; `src/loop/agent_loop.sh` (`_heartbeat`) ; `src/web/routes_dashboard.py` ; `src/web/static/app.js` | `tests/test_loop_status.py`, `tests/test_web_state_routes.py` |
| §3.2 CDC original — barre de progression de la mission (bilan `Couverture : X / N` de `SPEC_COVERAGE.md`) | `src/utils/markdown_parser.py` (`parse_coverage`) ; `src/loop/agent.py` (prompt Plan) ; `templates/SPEC_COVERAGE.md.tmpl` ; `src/web/static/app.js` | `tests/test_markdown_parser.py`, `tests/test_agent.py`, `tests/test_web_state_routes.py` |
| §3.1/§3 CDC original — bannière de fin de mission + bouton « Nouvelle session » | `src/web/routes_session.py` (`/api/session/clear`) ; `src/web/static/{index.html,app.js,style.css}` | `tests/test_web_session.py` |

## Validation

Commande de test : `python -m pytest` — l'ensemble de la suite doit être
verte, y compris les scénarios bout-en-bout (`tests/test_e2e_pipeline.py`)
qui lancent la vraie boucle avec un faux `opencode`.
