"""Logique d'iteration de l'agent DeBuilder.

Ce module contient la fonction run_iteration() appelee
par agent_loop.sh a chaque tour de boucle.

L'agent n'a aucune memoire interne entre deux iterations:
il reconstruit son contexte depuis les fichiers d'etat.
"""

import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from src.core.circuit_breaker import CircuitBreaker, _notify_webhook
from src.core.log_summarizer import synthesize_progress_entry
from src.core.state import (
    clear_suggestions,
    compact_architecture,
    is_done,
    read_state,
    repair_progress,
    touch_done,
    update_progress,
    write_state,
)
from src.core.git import (
    head_commit,
    recent_changes,
    stage_and_commit_all,
    status_files,
    tag_iteration,
)
from src.core.iterations import append_entry, read_entries
from src.core.secrets import sanitize_text
from src.utils.task_parser import all_boxes_checked, parse_checkboxes, parse_task
from src.utils.test_results import resolve_test_command, run_test_gate
from src.utils.text import read_log_tail, strip_ansi

# Taille max de OPENCODE_LOG.txt avant troncature : un job sans
# surveillance (pod distant) ne doit pas remplir le disque au fil
# des iterations.
_MAX_LOG_BYTES = 5 * 1024 * 1024

# Duree max SANS NOUVELLE SORTIE d'OpenCode. Sur un pod sans
# surveillance, un blocage (ex: attente d'une confirmation
# interactive qu'il ne recevra jamais) ne doit jamais figer la boucle
# indefiniment. Base sur l'inactivite (et non la duree totale) pour
# ne pas tuer une iteration qui avance encore dans sa liste de taches.
# L'agent est instruit (cf. _build_implement_prompt) de lancer les commandes
# longues (entrainement ML...) en arriere-plan plutot que de bloquer
# ici, mais ces valeurs restent surchargeables au cas ou.
_OPENCODE_INACTIVITY_TIMEOUT_SECONDS = int(
    os.environ.get("DEBUILDER_OPENCODE_INACTIVITY_TIMEOUT", "600")
)

# Garde-fou absolu : meme si OpenCode continue de produire de la
# sortie, une iteration ne doit pas tourner indefiniment (ex: boucle
# de retry qui log en continu sans jamais converger).
_OPENCODE_MAX_SECONDS = int(
    os.environ.get("DEBUILDER_OPENCODE_MAX_SECONDS", str(3 * 3600))
)

# OpenCode n'expose l'outil `websearch` que si le fournisseur du modele
# est `opencode` (Zen), ou si un backend de recherche est active par
# variable d'environnement. DeBuilder tournant avec DeepSeek / OpenAI /
# Anthropic, l'outil serait purement absent de la liste presentee au
# modele sans ce reglage. On active donc le backend Exa (service MCP
# heberge, aucune cle API requise) pour que l'agent puisse s'informer
# des pratiques, ressources et documentations a jour.
# L'outil `webfetch`, lui, est toujours disponible : seule sa
# permission doit etre accordee (cf. _merge_config_content).
_WEBSEARCH_BACKEND_VARS = (
    "OPENCODE_WEBSEARCH_PROVIDER",
    "OPENCODE_ENABLE_EXA",
    "OPENCODE_ENABLE_PARALLEL",
    "OPENCODE_EXPERIMENTAL_EXA",
    "OPENCODE_EXPERIMENTAL_PARALLEL",
)

_FALSY = {"0", "false", "no", "off"}

# Backoff entre iterations en echec (cf. agent_loop.sh) : double a
# chaque echec consecutif, plafonne, et revient au minimum apres un
# succes. Testable comme fonction pure.
_BACKOFF_MIN_SECONDS = 2.0


def compute_backoff(current: float, failed: bool, cap: float | None = None) -> float:
    """Prochaine duree d'attente entre iterations.

    Args:
        current: Duree d'attente actuelle (secondes).
        failed: True si l'iteration precedente a echoue.
        cap: Plafond en secondes (defaut :
            ``DEBUILDER_BACKOFF_CAP_SECONDS``, 300).

    Returns:
        Nouvelle duree d'attente : doublee a chaque echec (plafonnee),
        ou revenue au minimum apres un succes.
    """
    if cap is None:
        cap = float(os.environ.get("DEBUILDER_BACKOFF_CAP_SECONDS", "300"))
    if not failed:
        return _BACKOFF_MIN_SECONDS
    return min(max(current * 2, _BACKOFF_MIN_SECONDS), cap)

# Motifs indiquant un echec lie a l'API/au fournisseur (cle epuisee,
# quota, provider injoignable) plutot qu'a la tache elle-meme. Base de
# la typologie partagee avec le circuit breaker (phase 3).
_API_FAILURE_MOTIFS = (
    "api key",
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "insufficient_quota",
    "rate limit",
    "billing",
    "quota",
    "connection refused",
    "connection error",
    "http 401",
    "http 403",
    "http 429",
    "401 unauthorized",
    "403 forbidden",
    "429 too many requests",
)

# Fichiers dont le changement ne constitue pas un travail reel sur le
# projet : un diff limite a ces fichiers (ou vide) compte comme no-op.
_STATE_FILES = {
    "AGENTS.md",
    "PROGRESS.md",
    "BENCHMARKS.md",
    "SUGGESTIONS.md",
    "RESOURCES_NEEDED.md",
    "TASK.md",
    "PLAN.md",
    "ARCHITECTURE.md",
    "SPEC_COVERAGE.md",
    "GATE_FAILURE.md",
    "FINISHED_REPORT.md",
    "REVIEW.md",
    "DONE",
}


@dataclass
class IterationResult:
    """Resultat structure d'une iteration complete.

    Remplace l'ancien retour booleen de ``run_iteration`` : la boucle
    et le journal consomment les champs bruts, ``continue_loop`` (et
    ``__bool__``) preservent la compat avec l'usage booleen.
    """

    exit_code: int = 0
    failure_type: str = ""
    duration_seconds: float = 0.0
    changed_files: int = 0
    no_op: bool = False
    tests_passed: bool | None = None
    tests_summary: dict | None = None
    tags: list[str] | None = None
    gate_failures: list[str] | None = None
    mission_completed: bool = False
    continue_loop: bool = True

    def __bool__(self) -> bool:
        """Compat : ``bool(result)`` vaut l'ancien booleen de boucle."""
        return self.continue_loop

    def __post_init__(self) -> None:
        if self.tags is None:
            self.tags = []
        if self.gate_failures is None:
            self.gate_failures = []


def run_iteration(
    target_dir: Path,
    iteration_number: int | None = None,
) -> IterationResult:
    """Execute une iteration complete de l'agent.

    Une iteration = 2 sessions OpenCode specialisees (cahier des
    charges §2) :
    1. Session Plan, strictement lecture seule : analyse l'etat du
       projet et produit le contrat de tache (```TASK) ainsi que le
       plan mis a jour (```PLAN). La boucle materialise elle-meme
       TASK.md et PLAN.md a partir de ces blocs.
    2. Session Implement : consomme TASK.md, implemente, teste,
       committe par sous-tache, coche les cases, met a jour
       PROGRESS.md.

    Les gates (cases cochees, tests verts, PROGRESS.md mis a jour)
    sont ensuite verifiees par la boucle, qui ne croit pas l'agent sur
    parole.

    Args:
        target_dir: Repertoire du projet cible.
        iteration_number: Numero de l'iteration courante (fourni par la
            boucle ; defaut : variable ``DEBUILDER_ITERATION``).

    Returns:
        Resultat structure de l'iteration.
    """
    if iteration_number is None:
        iteration_number = _env_iteration_number()

    started = time.monotonic()
    result = IterationResult()

    if is_done(target_dir):
        result.continue_loop = False
        return result

    breaker = CircuitBreaker()
    _maybe_pause(breaker, target_dir)
    model = _select_model(breaker)

    # Reparation deterministe de la memoire persistante (cdc §4.1) :
    # un PROGRESS.md malforme (separateur absent, section tronquee)
    # est reconstruit depuis le template AVANT toute lecture, sans
    # demander a l'agent de reparer.
    if repair_progress(target_dir):
        _log("[agent] PROGRESS.md malforme : repare depuis le template.")

    progress_before = read_state(target_dir, "PROGRESS.md")
    suggestions_md = read_state(target_dir, "SUGGESTIONS.md")
    agents_md = read_state(target_dir, "AGENTS.md")
    spec_md = read_state(target_dir, "SPEC_COVERAGE.md")
    session_failures: list[str] = []

    # --- Session Plan (lecture seule) -----------------------------------
    try:
        plan_prompt = _build_plan_prompt(
            agents_md=agents_md,
            progress_md=progress_before,
            plan_md=read_state(target_dir, "PLAN.md"),
            spec_md=spec_md,
            gate_state=_gate_state_summary(target_dir),
        )
        _log("[agent] Session Plan (lecture seule)...")
        plan_completed = _run_opencode(
            target_dir, plan_prompt, model=model, read_only=True
        )
        _log(f"[agent] Session Plan terminee (code={plan_completed.returncode})")

        plan_failure = _classify_failure(plan_completed)
        session_failures.append(plan_failure)
        if plan_failure:
            result.exit_code = plan_completed.returncode
            result.failure_type = plan_failure
            _record_session_failure(target_dir, plan_failure, plan_completed)
        elif not _materialize_plan_outputs(target_dir, plan_completed.stdout):
            # La session a repondu, mais sans contrat de tache valide :
            # la boucle ne lance jamais Implement sans TASK.md.
            result.failure_type = "plan"
            _log("[agent] Session Plan : pas de contrat de tache valide (bloc TASK manquant ou vide).")
            update_progress(
                target_dir,
                f"- **Action realisee** : Session Plan\n"
                f"- **Resultat** : ECHEC (plan)\n"
                f"- **Problemes rencontres** : Bloc ```TASK manquant ou vide dans la reponse.\n"
                f"- **Solutions envisagees** : Reformuler la reponse avec les blocs TASK et PLAN.\n",
            )
    except Exception as exc:
        # Une iteration ne doit jamais tuer la boucle autonome : sur
        # un pod sans surveillance, un crash non rattrape ici arrete
        # l'agent de facon definitive jusqu'a intervention manuelle.
        _log(f"[agent] ERREUR inattendue pendant la session Plan : {exc}")
        _record_iteration_exception(target_dir, exc)
        result.exit_code = 1
        result.failure_type = "exception"
        session_failures.append("error")

    # --- Session Implement (seulement si la session Plan a reussi) -----
    if result.failure_type == "":
        try:
            implement_prompt = _build_implement_prompt(
                task_md=read_state(target_dir, "TASK.md"),
                progress_md=progress_before,
                benchmarks_md=read_state(target_dir, "BENCHMARKS.md"),
                arch_md=read_state(target_dir, "ARCHITECTURE.md"),
                suggestions_md=suggestions_md,
                resources_md=read_state(target_dir, "RESOURCES_NEEDED.md"),
                recovery_md=_recovery_section(target_dir),
            )
            _log("[agent] Session Implement...")
            completed = _run_opencode(target_dir, implement_prompt, model=model)
            _log(f"[agent] Session Implement terminee (code={completed.returncode})")

            result.exit_code = completed.returncode
            result.failure_type = _classify_failure(completed)
            session_failures.append(result.failure_type)

            if completed.returncode != 0 and completed.stderr:
                _log(f"[agent] Erreur OpenCode: {completed.stderr[:500]}")

            if result.failure_type:
                _record_session_failure(
                    target_dir, result.failure_type, completed
                )
            else:
                _run_implement_post_steps(
                    target_dir,
                    result,
                    completed,
                    suggestions_md,
                    progress_before,
                )

                # --- Session Review (lecture seule) : fin de mission ---
                # L'agent ne cree jamais DONE lui-meme : il coche la
                # checklist de FINISHED_REPORT.md, et seule la boucle
                # valide le rapport puis cree DONE.
                if result.failure_type == "" and _claim_finished(target_dir):
                    accepted, feedback, review_failure = _run_review_session(
                        target_dir,
                        model=model,
                        agents_md=agents_md,
                        spec_md=read_state(target_dir, "SPEC_COVERAGE.md"),
                        finished_md=read_state(target_dir, "FINISHED_REPORT.md"),
                        progress_md=read_state(target_dir, "PROGRESS.md"),
                    )
                    if review_failure:
                        session_failures.append(review_failure)
                        _log(
                            "[agent] Session Review en echec "
                            f"({review_failure}) : pas de validation, la boucle repart."
                        )
                    elif accepted:
                        touch_done(target_dir)
                        result.mission_completed = True
                        _log(
                            "[agent] Mission validee par la session Review : "
                            "DONE cree par la boucle."
                        )
                    else:
                        _write_review(target_dir, feedback)
                        result.failure_type = "review"
                        _log(
                            "[agent] Session Review : rapport rejete, "
                            "feedback dans REVIEW.md, la boucle repart."
                        )
        except Exception as exc:
            _log(f"[agent] ERREUR inattendue pendant la session Implement : {exc}")
            _record_iteration_exception(target_dir, exc)
            result.exit_code = 1
            result.failure_type = "exception"
            session_failures.append("error")

    # --- Circuit breaker : une seule alimentation par iteration --------
    iteration_failure = next((f for f in session_failures if f), "")
    if iteration_failure:
        breaker.record_failure(iteration_failure)
    else:
        breaker.record_success()

    # --- Finalisation commune -------------------------------------------
    changed = status_files(target_dir)
    result.changed_files = len(changed)
    result.no_op = _is_no_op(changed)

    # Detection de no-op (cdc §3) : apres N iterations consecutives sans
    # travail reel, le planner est somme de declarer la fin de mission
    # ou de justifier la poursuite ; l'utilisateur est notifie.
    consecutive_noops = _count_consecutive_noops(target_dir) + (
        1 if result.no_op else 0
    )
    if result.no_op and consecutive_noops >= _max_noops():
        _notify_webhook(
            {"event": "max_noops_reached", "noops": consecutive_noops}
        )
        update_progress(
            target_dir,
            f"- **Action realisee** : Detection de no-op\n"
            f"- **Resultat** : ECHEC (no-op)\n"
            f"- **Problemes rencontres** : {consecutive_noops} iterations "
            f"no-op consecutives (max {_max_noops()}) : soit la mission est "
            f"terminee, soit le plan doit etre corrige.\n"
            f"- **Solutions envisagees** : Coche la checklist de "
            f"FINISHED_REPORT.md si la mission est finie, ou replanifie "
            f"une tache concrete.\n",
        )
        if result.failure_type == "":
            result.failure_type = "noop"
        _log(
            f"[agent] {consecutive_noops} iterations no-op consecutives : "
            "fin de mission ou justification exigees."
        )

    head_before = head_commit(target_dir)
    committed, detail = stage_and_commit_all(target_dir, f"iteration {_timestamp()}")
    head_after = head_commit(target_dir)
    if not committed:
        _log(
            "[agent] ATTENTION: echec du commit/push automatique de fin "
            f"d'iteration: {sanitize_text(detail)[:500]}"
        )
    elif head_after and head_after != head_before:
        # Tag d'iteration uniquement si un commit a effectivement ete
        # cree (pas sur une iteration no-op sans commit) : le rollback
        # de l'interface et le bisect s'appuient sur ces tags.
        tag_name = f"debuilder/iter-{iteration_number:04d}"
        if tag_iteration(target_dir, tag_name):
            result.tags.append(tag_name)
            _log(f"[agent] Tag d'iteration pose : {tag_name}")
        else:
            _log(f"[agent] ATTENTION: echec de pose du tag {tag_name}")

    result.duration_seconds = round(time.monotonic() - started, 3)
    result.continue_loop = not is_done(target_dir)

    _journal_iteration(target_dir, iteration_number, result)
    return result


_FENCED_BLOCK_RE = re.compile(
    r"`{3,}(TASK|PLAN|SPEC|VERDICT)\s*\n(.*?)\n`{3,}", re.DOTALL
)


def _extract_fenced_block(stdout: str, tag: str) -> str:
    """Extrait le bloc de code `` ```TASK `` ou `` ```PLAN `` de la sortie.

    Accepte 3 backticks ou plus (4 backticks autorisent un bloc de code
    imbrique dans le contrat de tache).

    Args:
        stdout: Sortie complete de la session Plan.
        tag: Marqueur du bloc (``TASK`` ou ``PLAN``).

    Returns:
        Contenu du bloc, ou chaine vide si absent.
    """
    for match in _FENCED_BLOCK_RE.finditer(stdout):
        if match.group(1).upper() == tag:
            return match.group(2).strip()
    return ""


def _materialize_plan_outputs(target_dir: Path, stdout: str) -> bool:
    """Materialise TASK.md, PLAN.md et SPEC_COVERAGE.md depuis la sortie Plan.

    La boucle ne croit pas l'agent sur parole : elle ecrit elle-meme le
    contrat de tache, le plan et la couverture, a partir des blocs
    delimites de la reponse.

    Args:
        target_dir: Repertoire du projet cible.
        stdout: Sortie de la session Plan.

    Returns:
        True si un bloc TASK non vide a ete materialise (condition
        necessaire pour lancer la session Implement).
    """
    task_content = _extract_fenced_block(stdout, "TASK")
    if not task_content:
        return False
    write_state(target_dir, "TASK.md", task_content + "\n")

    plan_content = _extract_fenced_block(stdout, "PLAN")
    if plan_content:
        write_state(target_dir, "PLAN.md", plan_content + "\n")

    spec_content = _extract_fenced_block(stdout, "SPEC")
    if spec_content:
        write_state(target_dir, "SPEC_COVERAGE.md", spec_content + "\n")
    return True


def _run_implement_post_steps(
    target_dir: Path,
    result: IterationResult,
    completed: subprocess.CompletedProcess,
    suggestions_md: str,
    progress_before: str,
) -> None:
    """Etapes apres une session Implement terminee proprement.

    Ordre important : les gates (cases cochees, PROGRESS.md modifie)
    sont verifiees AVANT le fallback de PROGRESS.md, sinon le fallback
    masquerait l'oubli de l'agent.

    Args:
        target_dir: Repertoire du projet cible.
        result: Resultat d'iteration en cours de remplissage.
        completed: Processus OpenCode termine (rc 0).
        suggestions_md: Contenu de SUGGESTIONS.md avant l'iteration.
        progress_before: Contenu de PROGRESS.md avant l'iteration.
    """
    barrier_files = sorted(target_dir.glob("BARRIER_*"))
    if barrier_files:
        _log(f"[agent] {len(barrier_files)} barriere(s) detectee(s), mise en pause...")
        _handle_barriers(target_dir, barrier_files)

    # Gates deterministes executees par la boucle (pas par l'agent).
    gate_failures = _check_task_gates(
        target_dir, progress_before=progress_before
    )
    if gate_failures:
        result.gate_failures = gate_failures
        result.failure_type = "gate"
        _write_gate_failure(target_dir, gate_failures)
        _log(
            "[agent] Gates en echec : "
            + "; ".join(gate_failures)
            + " — la tache n'est pas soldee."
        )
    else:
        _clear_gate_failure(target_dir)

    # Fallback de mise a jour memoire (cdc §4.3) : si l'agent n'a pas
    # mis a jour PROGRESS.md lui-meme (etape obligatoire de la session
    # Implement), l'entree est synthetisee par le LLM a partir des
    # commits git recents et de la fin du transcript — JAMAIS le stdout
    # brut injecte tel quel. Sur echec de la synthese, repli sur une
    # entree heuristique structuree.
    if completed.stdout.strip():
        progress_after = read_state(target_dir, "PROGRESS.md")
        if progress_after == progress_before:
            entry = _synthesize_progress_entry(target_dir)
            if not entry:
                entry = _heuristic_progress_entry(completed)
            update_progress(target_dir, entry)

    # Budget de taille d'ARCHITECTURE.md (cdc §4.2) : les entrees les
    # plus anciennes sont compactees pour ne pas gonfler le prompt des
    # iterations suivantes.
    if compact_architecture(target_dir):
        _log("[agent] ARCHITECTURE.md compacte (budget de taille).")

    # Gate de tests deterministe (TASK.md > AGENTS.md > env).
    if result.failure_type == "":
        _run_test_gate(target_dir, result)
        if result.failure_type == "tests":
            _write_gate_failure(
                target_dir,
                ["commande de test en echec (detail dans PROGRESS.md)"],
            )

    # Purge de SUGGESTIONS.md (cdc §4.4) : uniquement si l'iteration a
    # reussi ET que l'agent a justifie sa decision (acceptee/rejetee/
    # reportee) dans PROGRESS.md. Jamais apres un echec.
    _maybe_clear_suggestions(
        target_dir,
        suggestions_md,
        read_state(target_dir, "PROGRESS.md"),
        success=(result.failure_type == ""),
    )


def _check_task_gates(target_dir: Path, progress_before: str) -> list[str]:
    """Verifie les gates du contrat de tache (boucle, pas l'agent).

    Args:
        target_dir: Repertoire du projet cible.
        progress_before: Contenu de PROGRESS.md avant la session
            Implement.

    Returns:
        Liste des motifs d'echec (vide si toutes les gates passent).
    """
    failures: list[str] = []
    task = parse_task(read_state(target_dir, "TASK.md"))
    if not all_boxes_checked(task):
        failures.append("toutes les cases de TASK.md ne sont pas cochees")
    if read_state(target_dir, "PROGRESS.md") == progress_before:
        failures.append("PROGRESS.md n'a pas ete mis a jour")
    return failures


def _write_gate_failure(target_dir: Path, failures: list[str]) -> None:
    """Consigne les motifs d'echec des gates (injectes a la session suivante)."""
    content = "# Echec des Gates\n\n" + "".join(f"- {f}\n" for f in failures)
    write_state(target_dir, "GATE_FAILURE.md", content)


def _clear_gate_failure(target_dir: Path) -> None:
    try:
        (target_dir / "GATE_FAILURE.md").unlink()
    except FileNotFoundError:
        pass


def _maybe_clear_suggestions(
    target_dir: Path,
    suggestions_md: str,
    progress_md: str,
    success: bool,
) -> None:
    """Vide SUGGESTIONS.md uniquement si l'iteration a reussi et que la
    decision a ete justifiee dans PROGRESS.md (cdc §4.4)."""
    if not suggestions_md.strip():
        return
    if not success:
        _log("[agent] Suggestions conservees : iteration en echec.")
        return
    lowered = progress_md.lower()
    if any(word in lowered for word in ("acceptee", "rejetee", "reportee")):
        clear_suggestions(target_dir)
    else:
        _log(
            "[agent] Suggestions conservees : aucune justification "
            "(acceptee/rejetee/reportee) dans PROGRESS.md."
        )


def _gate_state_summary(target_dir: Path) -> str:
    """Etat des gates de l'iteration precedente, pour la session Plan.

    Anti « plan drift » : le planner verifie que l'iteration precedente
    est reellement terminee avant de rediger un nouveau plan.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Resume factuel (gates, tests, arbre git, no-op) en Markdown.
    """
    parts: list[str] = []
    gate_failure = read_state(target_dir, "GATE_FAILURE.md").strip()
    if gate_failure:
        parts.append("Echec des gates de l'iteration precedente :\n\n" + gate_failure)
    else:
        parts.append("Gates de l'iteration precedente : OK.")

    review_feedback = read_state(target_dir, "REVIEW.md").strip()
    if review_feedback:
        parts.append(
            "Le rapport de fin de mission a ete REJETE par la session "
            "Review. Feedback :\n\n" + review_feedback
        )

    entries = read_entries(target_dir, limit=1)
    if entries:
        tests_passed = entries[-1].get("tests_passed")
        label = {True: "vert", False: "rouge", None: "non evalues"}.get(
            tests_passed, "inconnu"
        )
        parts.append(f"Tests de la derniere iteration : {label}.")

    noops = _count_consecutive_noops(target_dir)
    if noops >= _max_noops():
        parts.append(
            f"ATTENTION : {noops} iterations no-op consecutives (max "
            f"{_max_noops()}) : declare la fin de mission (checklist de "
            f"FINISHED_REPORT.md complete) ou justifie explicitement la "
            f"poursuite dans le plan."
        )
    elif noops:
        parts.append(f"Iterations no-op consecutives : {noops}.")

    dirty = status_files(target_dir)
    if dirty:
        parts.append(
            f"Arbre git SALE ({len(dirty)} fichier(s) non committe(s)) : "
            "verifier que l'iteration precedente est reellement terminee "
            "avant de planifier."
        )
    else:
        parts.append("Arbre git propre.")
    return "\n".join(parts)


def _max_noops() -> int:
    """Nombre de no-op consecutifs avant obligation de conclure."""
    return int(os.environ.get("DEBUILDER_MAX_NOOPS", "3"))


def _count_consecutive_noops(target_dir: Path) -> int:
    """Compte les iterations no-op consecutives (les plus recentes).

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Nombre d'entrees finales du journal marquees no-op.
    """
    entries = read_entries(target_dir)
    count = 0
    for entry in reversed(entries):
        if entry.get("no_op"):
            count += 1
        else:
            break
    return count


def _claim_finished(target_dir: Path) -> bool:
    """True si l'agent revendique la fin de mission.

    La revendication est un fait deterministe : la checklist de
    FINISHED_REPORT.md existe, contient au moins une case, et toutes
    les cases sont cochees. Un rapport a l'etat du template (aucune
    case cochee) n'est pas une revendication.
    """
    finished_md = read_state(target_dir, "FINISHED_REPORT.md")
    if not finished_md.strip():
        return False
    boxes = parse_checkboxes(finished_md)
    return bool(boxes) and all(item.checked for item in boxes)


def _build_review_prompt(
    agents_md: str,
    spec_md: str,
    finished_md: str,
    progress_md: str,
) -> str:
    """Construit le prompt de la session Review (lecture seule).

    La session Review ne cree jamais DONE : elle rend un verdict que la
    boucle applique elle-meme.
    """
    parts = []

    parts.append("## Cahier des Charges\n\n" + (agents_md or "(absent)"))

    if spec_md.strip():
        parts.append(
            "## Couverture du Cahier des Charges (SPEC_COVERAGE.md)\n\n"
            "La fin de mission exige une couverture 100 % (chaque item "
            "du cahier des charges mappe sur une implementation et un "
            "test) ET des tests verts.\n\n" + spec_md
        )

    parts.append(
        "## Rapport de Fin de Mission (FINISHED_REPORT.md)\n\n"
        + (finished_md or "(absent)")
    )

    if progress_md.strip():
        parts.append("## Progression Recente\n\n" + progress_md)

    parts.append(
        "## Instructions\n\n"
        "1. Compare le rapport de fin de mission au cahier des charges "
        "et a la couverture SPEC_COVERAGE : chaque item est-il "
        "reellement realise, valide par un test, et la suite de tests "
        "est-elle verte ?\n"
        "2. Ne te fie pas aux seules declarations du rapport : "
        "controle leur coherence avec SPEC_COVERAGE.md et la "
        "progression.\n"
        "3. Termine ta reponse par EXACTEMENT un bloc de code marque "
        "VERDICT dont la premiere ligne est `ACCEPTE` (mission "
        "validee) ou `REFUSE` (avec les raisons detaillees sur les "
        "lignes suivantes, et ce qu'il reste a faire)."
    )

    return "\n\n".join(parts)


def _run_review_session(
    target_dir: Path,
    model: str,
    agents_md: str,
    spec_md: str,
    finished_md: str,
    progress_md: str,
) -> tuple[bool, str, str]:
    """Lance la session Review et parse son verdict.

    Returns:
        Tuple (accepte, feedback, echec_session) : ``accepte`` n'est
        True que si la session a proprement rendu un verdict ACCEPTE ;
        ``echec_session`` est la typologie de l'echec ("" si la
        session s'est terminee proprement).
    """
    prompt = _build_review_prompt(
        agents_md=agents_md,
        spec_md=spec_md,
        finished_md=finished_md,
        progress_md=progress_md,
    )
    _log("[agent] Session Review (lecture seule)...")
    completed = _run_opencode(target_dir, prompt, model=model, read_only=True)
    failure = _classify_failure(completed)
    _log(f"[agent] Session Review terminee (code={completed.returncode})")
    if failure:
        return False, "", failure

    verdict = _extract_fenced_block(completed.stdout, "VERDICT")
    accepted = verdict.strip().upper().startswith("ACCEPTE")
    if accepted:
        return True, "", ""
    return False, verdict.strip(), ""


def _write_review(target_dir: Path, feedback: str) -> None:
    """Consigne le feedback de rejet dans REVIEW.md."""
    template = (
        Path(__file__).resolve().parent.parent.parent
        / "templates"
        / "REVIEW.md.tmpl"
    ).read_text(encoding="utf-8")
    write_state(
        target_dir,
        "REVIEW.md",
        template.replace("{{ feedback }}", feedback or "(aucun detail)"),
    )


def record_cap_stop(target_dir: Path, reason: str) -> None:
    """Notifie un arret par cap dur (iterations ou budget de temps).

    Appele par agent_loop.sh : consigne l'arret dans PROGRESS.md
    (visible au tableau de bord) et pousse un webhook optionnel.

    Args:
        target_dir: Repertoire du projet cible.
        reason: Motif de l'arret (deja en clair, sans secrets).
    """
    update_progress(
        target_dir,
        f"- **Action realisee** : Arret par cap dur\n"
        f"- **Resultat** : {sanitize_text(reason)}\n"
        f"- **Problemes rencontres** : Aucun.\n"
        f"- **Solutions envisagees** : Relancer la boucle avec des caps "
        f"plus larges, ou valider la fin de mission.\n",
    )
    _notify_webhook({"event": "cap_reached", "reason": sanitize_text(reason)})


def _record_session_failure(
    target_dir: Path,
    failure_type: str,
    completed: subprocess.CompletedProcess,
) -> None:
    """Consigne un echec de session OpenCode dans PROGRESS.md."""
    stderr = completed.stderr.strip() if completed.stderr else ""
    stdout = completed.stdout.strip() if completed.stdout else ""
    detail = (stderr or stdout)[-500:]
    update_progress(
        target_dir,
        f"- **Action realisee** : Tentative d'iteration (session OpenCode)\n"
        f"- **Resultat** : ECHEC ({failure_type}, code {completed.returncode})\n"
        f"- **Problemes rencontres** : {sanitize_text(detail)}\n"
        f"- **Solutions envisagees** : Verifier la cle API et la configuration d'OpenCode.\n",
    )


def _env_iteration_number() -> int:
    raw = os.environ.get("DEBUILDER_ITERATION", "0")
    try:
        return int(raw)
    except ValueError:
        return 0


def _select_model(breaker: CircuitBreaker) -> str:
    """Choisit le modele a utiliser pour l'iteration.

    Basculer sur ``DEBUILDER_MODEL_FALLBACK`` (si defini) tant que le
    circuit breaker est ouvert, puis revenir au modele principal apres
    un succes.

    Args:
        breaker: Etat du circuit breaker.

    Returns:
        Nom du modele opencode (ou chaine vide pour le defaut).
    """
    if breaker.use_fallback():
        fallback = os.environ.get("DEBUILDER_MODEL_FALLBACK", "").strip()
        _log(f"[agent] Circuit breaker ouvert : bascule sur le modele de secours {fallback}.")
        return fallback
    return os.environ.get("DEBUILDER_MODEL", "")


def _maybe_pause(breaker: CircuitBreaker, target_dir: Path) -> None:
    """Marque une pause si le circuit breaker est ouvert.

    Dort par tranches courtes pour rester reactif au kill-switch
    (creation du fichier DONE interrompt la pause).

    Args:
        breaker: Etat du circuit breaker.
        target_dir: Repertoire du projet cible (pour le kill-switch).
    """
    remaining = breaker.pause_remaining()
    while remaining > 0:
        if is_done(target_dir):
            return
        step = min(5.0, remaining)
        time.sleep(step)
        remaining = breaker.pause_remaining()


def _classify_failure(result: subprocess.CompletedProcess) -> str:
    """Classe la sortie d'OpenCode selon la typologie des echecs.

    Args:
        result: Processus OpenCode termine.

    Returns:
        "" (reussite), "timeout" (watchdog), "api" (fournisseur),
        "empty" (aucune sortie produite) ou "error" (autre echec).
    """
    if result.returncode == 0:
        if not (result.stdout or "").strip():
            return "empty"
        return ""
    if result.returncode == -1:
        return "timeout"
    detail = f"{result.stderr or ''} {result.stdout or ''}".lower()
    if any(motif in detail for motif in _API_FAILURE_MOTIFS):
        return "api"
    return "error"


def _is_no_op(changed_files: list[str]) -> bool:
    """True si l'iteration n'a produit aucun changement de code.

    Un diff vide, ou limite aux fichiers d'etat de DeBuilder, ne
    constitue pas un travail reel sur le projet cible.

    Args:
        changed_files: Chemins retournes par ``git status --porcelain``.
    """
    if not changed_files:
        return True
    return all(
        name in _STATE_FILES or name.endswith(".lock") for name in changed_files
    )


def _synthesize_progress_entry(target_dir: Path) -> str | None:
    """Synthese LLM de l'entree PROGRESS.md (repli cdc §4.3).

    Entree = commits git recents (source factuelle des sous-taches) +
    fin du transcript. Jamais le stdout brut.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Entree Markdown structuree, ou None si la synthese est
        indisponible.
    """
    try:
        return synthesize_progress_entry(
            git_context=recent_changes(target_dir),
            transcript_tail=read_log_tail(target_dir, "OPENCODE_LOG.txt", 200),
        )
    except Exception as exc:
        _log(f"[agent] Synthese LLM de PROGRESS.md indisponible : {exc}")
        return None


def _heuristic_progress_entry(completed: subprocess.CompletedProcess) -> str:
    """Repli heuristique : entree structuree sans contenu de sortie brut.

    Ne recopie jamais le stdout : il reste consultable dans
    OPENCODE_LOG.txt, et ne doit pas gonfler les prompts futurs.
    """
    return (
        f"- **Action realisee** : Iteration terminee (code {completed.returncode})\n"
        f"- **Resultat** : Non consigne par l'agent (pas de mise a jour de PROGRESS.md)\n"
        f"- **Problemes rencontres** : Aucun detecte par la boucle\n"
        f"- **Solutions envisagees** : Consulter OPENCODE_LOG.txt pour le detail de l'iteration.\n"
    )


def _run_test_gate(target_dir: Path, result: IterationResult) -> None:
    """Execute la gate de tests deterministe (cahier des charges §5.3).

    La boucle relance elle-meme la commande de test resolue (TASK.md,
    AGENTS.md ou DEBUILDER_TEST_CMD) : le « tests passent » declare par
    l'agent n'est jamais le seul signal. En cas d'echec, l'iteration
    n'est pas soldee : une entree « ECHEC (tests) » est consignee dans
    PROGRESS.md et le motif est journalise.

    Args:
        target_dir: Repertoire du projet cible.
        result: Resultat d'iteration en cours de remplissage.
    """
    test_cmd = resolve_test_command(target_dir)
    if not test_cmd:
        _log(
            "[agent] Gate de tests ignoree : aucune commande de test resolue "
            "(section 'Commande de test' de TASK.md/AGENTS.md, ou DEBUILDER_TEST_CMD)."
        )
        result.tests_passed = None
        return

    _log(f"[agent] Gate de tests : {test_cmd}")
    try:
        gate = run_test_gate(target_dir, test_cmd)
    except Exception as exc:
        _log(f"[agent] ATTENTION: gate de tests inexecutable : {exc}")
        result.tests_passed = None
        return

    result.tests_passed = gate.passed
    result.tests_summary = gate.to_dict()

    if gate.passed:
        label = f"{gate.tests} tests" if gate.tests is not None else "OK"
        _log(f"[agent] Gate de tests : OK ({label})")
        return

    detail = sanitize_text(gate.detail)[:500]
    _log(f"[agent] Gate de tests : ECHEC — {detail}")
    result.failure_type = "tests"
    update_progress(
        target_dir,
        f"- **Action realisee** : Iteration + gate de tests\n"
        f"- **Resultat** : ECHEC (tests)\n"
        f"- **Problemes rencontres** : {detail}\n"
        f"- **Solutions envisagees** : Corriger l'etat casse du depot "
        f"avant de poursuivre ; la suite de tests doit repasser au vert.\n",
    )


def _journal_iteration(
    target_dir: Path,
    iteration_number: int,
    result: IterationResult,
) -> None:
    entry = {
        "iteration": iteration_number,
        "exit_code": result.exit_code,
        "failure_type": result.failure_type,
        "duration_seconds": result.duration_seconds,
        "changed_files": result.changed_files,
        "no_op": result.no_op,
        "tags": result.tags,
    }
    if result.tests_passed is not None:
        entry["tests_passed"] = result.tests_passed
        entry["tests"] = result.tests_summary
    if result.gate_failures:
        entry["gate_failures"] = result.gate_failures
    entry["mission_completed"] = result.mission_completed
    try:
        append_entry(target_dir, entry)
    except Exception as exc:
        _log(f"[agent] ATTENTION: echec d'ecriture du journal d'iterations : {exc}")


def _record_iteration_exception(target_dir: Path, exc: Exception) -> None:
    try:
        update_progress(
            target_dir,
            f"- **Action realisee** : Tentative d'iteration\n"
            f"- **Resultat** : ECHEC (exception inattendue)\n"
            f"- **Problemes rencontres** : {sanitize_text(str(exc))}\n"
            f"- **Solutions envisagees** : Verifier OPENCODE_LOG.txt et l'etat des fichiers.\n",
        )
    except Exception:
        pass


def _build_plan_prompt(
    agents_md: str,
    progress_md: str,
    plan_md: str,
    spec_md: str,
    gate_state: str,
) -> str:
    """Construit le prompt de la session Plan (lecture seule).

    Le planner n'ecrit rien lui-meme : sa reponse doit se terminer par
    deux blocs de code delimites, ```TASK (le contrat de tache) et
    ```PLAN (le plan mis a jour). La boucle materialise ces deux
    fichiers elle-meme.
    """
    parts = []

    if agents_md:
        parts.append("## Objectifs du Projet (cahier des charges)\n\n" + agents_md)

    if spec_md.strip():
        parts.append(
            "## Couverture du Cahier des Charges\n\n"
            "Maintenir ce mapping a jour : chaque item realise et teste "
            "doit y figurer.\n\n" + spec_md
        )

    if plan_md.strip():
        parts.append("## Plan de Developpement (backlog)\n\n" + plan_md)

    if progress_md.strip():
        parts.append(
            "## Progression Recente\n\n" + progress_md
        )

    parts.append(
        "## Etat des Gates de l'Iteration Precedente\n\n"
        + (gate_state or "Aucune information.")
    )

    parts.append(
        "## Instructions\n\n"
        "1. Analyse l'etat actuel du projet : progression recente, plan, "
        "couverture du cahier des charges, etat des gates de l'iteration "
        "precedente.\n"
        "2. Verifie la coherence AVANT de planifier (anti plan-drift) : "
        "si l'iteration precedente n'est pas reellement terminee (gates "
        "en echec, arbre git sale, tests rouges), la tache a planifier "
        "est d'abord de la terminer ou de reparer l'etat, pas d'enchainer "
        "sur la suite.\n"
        "3. Choisis la prochaine tache (une seule) la plus pertinente du "
        "backlog, ou reprend la tache interrompue. Ne planifie qu'une "
        "quantite de travail realisable en une iteration.\n"
        "4. Redige le contrat de tache TASK.md : objectif en une ou deux "
        "phrases, criteres d'acceptation mesurables en cases a cocher, "
        "la commande de test exacte (UNE SEULE LIGNE, sans bloc de code), "
        "et les sous-taches atomiques en cases a cocher.\n"
        "5. Mets a jour le plan (backlog trie par priorite, tache "
        "choisie deplacee) et la couverture SPEC_COVERAGE (mapping cahier "
        "des charges -> implementation + test).\n"
        "6. Termine ta reponse par EXACTEMENT trois blocs de code, dans "
        "cet ordre :\n"
        "   - un bloc marque TASK contenant le contenu complet de TASK.md ;\n"
        "   - un bloc marque PLAN contenant le contenu complet et a jour "
        "de PLAN.md ;\n"
        "   - un bloc marque SPEC contenant le contenu complet et a jour "
        "de SPEC_COVERAGE.md.\n"
        "   Exemple : ```TASK puis le contenu, puis ```, puis ```PLAN puis "
        "le contenu, puis ```, puis ```SPEC puis le contenu, puis ```. Ne "
        "rien mettre apres le bloc SPEC."
    )

    return "\n\n".join(parts)


def _build_implement_prompt(
    task_md: str,
    progress_md: str,
    benchmarks_md: str,
    arch_md: str,
    suggestions_md: str,
    resources_md: str,
    recovery_md: str = "",
) -> str:
    """Construit le prompt de la session Implement.

    La session Implement ne relit pas le cahier des charges : celui-ci a
    deja ete traduit en contrat de tache par la session Plan.
    """
    parts = []

    parts.append(
        "## Contrat de Tache (TASK.md)\n\n"
        "Voici la tache a realiser pour cette iteration. Ne relis pas le "
        "cahier des charges : il a deja ete traduit en tache.\n\n"
        + (task_md or "(TASK.md manquant)")
    )

    if progress_md.strip():
        parts.append(
            "## Progression Recente\n\n" + progress_md
        )

    if benchmarks_md.strip():
        parts.append(
            "## Benchmarks (ne pas regresser)\n\n" + benchmarks_md
        )

    if arch_md.strip():
        parts.append(
            "## Decisions d'Architecture (persistantes)\n\n"
            "Respecte ces decisions structurantes, et ajoute-y toute "
            "nouvelle decision prise pendant cette iteration, en restant "
            "concis.\n\n" + arch_md
        )

    if recovery_md.strip():
        parts.append(
            "## Reprise apres echec\n\n"
            "La session precedente a ete interrompue ou a echoue. "
            "Verifie l'etat reel du repo avant de continuer : "
            "l'agent peut croire avoir termine sa tache alors qu'il a "
            "ete tue.\n\n"
            + recovery_md
        )

    if suggestions_md.strip():
        parts.append(
            "## Suggestion de l'utilisateur\n\n"
            "L'utilisateur a suggere ce qui suit. "
            "Tu es libre de l'accepter, la reporter ou la rejeter, "
            "mais tu DOIS justifier ta decision dans PROGRESS.md.\n\n"
            + suggestions_md
        )

    if resources_md.strip():
        parts.append(
            "## Ressources disponibles (bonus)\n\n"
            "Des ressources supplementaires ont ete mises a disposition.\n\n"
            + resources_md
        )

    parts.append(
        "## Instructions\n\n"
        "1. Lis le contrat de tache ci-dessus et execute les sous-taches "
        "dans l'ordre.\n"
        "2. Pour CHAQUE sous-tache terminee : coche sa case dans TASK.md "
        "(remplace [ ] par [x]) et justifie dans PROGRESS.md ce qui a "
        "ete fait. Ne coche JAMAIS une case sans l'avoir reellement "
        "terminee et testee.\n"
        "3. Commits : un petit commit par sous-tache, au format "
        "Conventional Commits `type(portee): description` avec un type "
        "valide (feat, fix, chore, docs, test, refactor, perf, ci, "
        "build, style). Ne jamais commiter un etat que tu sais casse.\n"
        "4. Lance la commande de test du contrat avant de considerer une "
        "sous-tache comme terminee ; ajoute des tests pour tout nouveau "
        "code.\n"
        "5. DERNIERE ETAPE OBLIGATOIRE : mets a jour PROGRESS.md avec "
        "Action realisee, Resultat obtenu, Problemes rencontres et "
        "solutions, et la prochaine sous-tache prevue. Mets a jour "
        "BENCHMARKS.md si de nouvelles metriques ont ete collectees, et "
        "ARCHITECTURE.md si une decision structurante a ete prise.\n"
        "6. Fin de mission : si tu estimes la Definition of Done "
        "atteinte (tous les items du cahier des charges realises et "
        "valides par tests), coche TOUTES les cases de la checklist de "
        "FINISHED_REPORT.md et complete sa section « Validation par les "
        "Tests » avec les commandes executees et leurs resultats. Ne "
        "cree JAMAIS le fichier DONE : seule la boucle le fait, apres "
        "validation par une session de review.\n"
        "7. Si une suggestion utilisateur est presente, justifie ta "
        "decision (acceptee/rejetee/reportee) dans PROGRESS.md.\n"
        "8. Ne JAMAIS inclure de cles API ou secrets dans les logs/commits.\n"
        "9. Si une ressource te manque, trouve une solution de "
        "contournement et ne bloque jamais. Si tu signales le besoin "
        "dans RESOURCES_NEEDED.md, justifie-y explicitement pourquoi "
        "cette ressource serait un plus et quelle solution palliative tu "
        "as deja mise en place en attendant.\n"
        "10. Pour toute commande longue (entrainement ML, build "
        "volumineux, telechargement de dataset...) : lance-la en "
        "arriere-plan (ex: `nohup ... > train.log 2>&1 &`) et redonne "
        "la main immediatement. Ne bloque JAMAIS cet appel en attendant "
        "sa fin : cette iteration a un delai maximum, et le processus "
        "serait tue avant la fin de l'entrainement. Verifie et rapporte "
        "sa progression aux iterations suivantes.\n"
        "11. Tu as un acces internet via les outils `websearch` "
        "(rechercher) et `webfetch` (lire une URL). Utilise-les des que "
        "ta decision depend d'une information externe susceptible "
        "d'avoir change : documentation ou signature d'API d'une "
        "bibliotheque, version courante d'un paquet, bonne pratique de "
        "l'ecosysteme, dataset ou modele pre-entraine disponible, "
        "message d'erreur inconnu. Ne devine jamais une API ou une "
        "version : verifie-la. Privilegie les sources officielles "
        "(documentation, depot du projet, notes de version), et cite "
        "l'URL dans PROGRESS.md quand une decision s'appuie dessus. "
        "N'inclus jamais de secret ni de code proprietaire du projet "
        "dans une requete. Si la recherche echoue (reseau indisponible, "
        "outil absent), poursuis avec tes connaissances, signale-le "
        "dans PROGRESS.md et ne bloque pas."
    )

    return "\n\n".join(parts)


# Message de reprise adapte au type d'echec (typologie partagee avec le
# circuit breaker, phase 3) : la session suivante ne repart pas de zero
# mais verifie l'etat reel laisse par la session interrompue.
_RECOVERY_MESSAGES = {
    "timeout": (
        "La session precedente a ete tuee par le garde-fou de temps "
        "(inactivite prolongee ou duree maximale atteinte) : elle n'a "
        "probablement pas termine son travail ni tout committe."
    ),
    "api": (
        "La session precedente a echoue sur une erreur API (cle "
        "invalide, quota depasse, provider injoignable) : le travail "
        "prevu a pu etre fait partiellement, pas du tout, ou non "
        "committe."
    ),
    "empty": (
        "La session precedente n'a produit aucune sortie : le modele "
        "n'a rien fait. Aucun travail ne doit etre considere comme "
        "termine."
    ),
    "error": "La session precedente s'est terminee en erreur.",
    "exception": "La session precedente a plante (exception inattendue).",
    "plan": (
        "La session Plan precedente n'a pas produit de contrat de tache "
        "valide (bloc TASK manquant ou vide). Aucun travail n'a ete "
        "lance : reprend la planification."
    ),
    "gate": (
        "Les gates de l'iteration precedente ont echoue (voir "
        "GATE_FAILURE.md) : la tache n'est pas soldee. Termine ou "
        "repare l'etat avant de considerer que le travail est fait."
    ),
    "review": (
        "La session Review a rejete le rapport de fin de mission "
        "(voir REVIEW.md) : la mission n'est pas terminee. Corrige les "
        "manques signales avant de revendiquer a nouveau la fin."
    ),
    "noop": (
        "Les iterations precedentes n'ont produit aucun travail reel "
        "(no-op repetes) : soit la mission est terminee (coche la "
        "checklist de FINISHED_REPORT.md), soit le plan doit etre "
        "corrige pour reprendre un travail utile."
    ),
    "tests": (
        "La gate de tests de l'iteration precedente a echoue : le "
        "depot est dans un etat casse. Corrige le code (ou les tests) "
        "avant de poursuivre, et ne commit jamais un etat que tu sais "
        "casse."
    ),
}

# Types d'echec pour lesquels la fin du transcript apporte une
# information exploitable (le contenu du travail interrompu) : pas pour
# une gate de tests, dont le motif est deja dans PROGRESS.md.
_TRANSCRIPT_FAILURE_TYPES = {"timeout", "api", "empty", "error", "exception"}

# Nombre de lignes du transcript injectees a la session suivante.
_RECOVERY_LOG_LINES = 200


def _recovery_section(target_dir: Path) -> str:
    """Construit la section de reprise apres echec pour le prompt.

    S'appuie sur la derniere entree d'``ITERATIONS.jsonl`` : si
    l'iteration precedente a echoue, la session suivante recoit un
    message adapte au type d'echec et, le cas echeant, la fin du
    transcript d'OpenCode (l'agent peut croire avoir termine sa tache
    alors qu'il a ete tue).

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Contenu Markdown de la section, ou chaine vide si l'iteration
        precedente a reussi (ou n'existe pas).
    """
    entries = read_entries(target_dir, limit=1)
    if not entries:
        return ""
    failure = entries[-1].get("failure_type", "")
    if not failure:
        return ""

    message = _RECOVERY_MESSAGES.get(failure, _RECOVERY_MESSAGES["error"])
    if failure in _TRANSCRIPT_FAILURE_TYPES:
        tail = read_log_tail(target_dir, "OPENCODE_LOG.txt", _RECOVERY_LOG_LINES)
        if tail.strip():
            message += (
                "\n\n### Fin du transcript de la session interrompue\n\n"
                "```\n" + sanitize_text(tail) + "\n```\n"
            )
    return message


def _run_opencode(
    target_dir: Path,
    prompt: str,
    model: str | None = None,
    read_only: bool = False,
) -> subprocess.CompletedProcess:
    import shutil

    if model is None:
        model = os.environ.get("DEBUILDER_MODEL", "")
    bin_path = shutil.which("opencode") or "/usr/local/bin/opencode"

    # Le prompt (AGENTS.md + PROGRESS.md + BENCHMARKS.md + ...) n'a
    # pas de taille bornee et peut depasser ARG_MAX si on le passe
    # directement en argument positionnel (execve leve alors
    # `[Errno 7] Argument list too long`). On l'ecrit dans un fichier
    # temporaire et on l'attache via --file : la taille des fichiers
    # d'etat ne peut alors plus jamais faire planter l'appel.
    prompt_file = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".md",
        prefix="debuilder-prompt-",
        delete=False,
        encoding="utf-8",
    )
    try:
        prompt_file.write(prompt)
    finally:
        prompt_file.close()

    # --auto : approuve automatiquement les permissions OpenCode.
    # Sans ce flag, une action necessitant confirmation bloque le
    # process en attente d'une reponse sur stdin, qui est ferme
    # (DEVNULL) puisque la boucle tourne sans surveillance. Conforme
    # a l'exigence d'autonomie/non-blocage du cahier des charges.
    # Pas de --print-logs/--log-level : ces flags font ecrire dans
    # OPENCODE_LOG.txt les logs internes d'OpenCode (tracking git,
    # session/stream, permissions evaluees) en plus de la sortie
    # normale de la commande, ce qu'un usage manuel de `opencode run`
    # n'affiche jamais. Le watchdog ci-dessous (timeout d'inactivite +
    # duree totale) gere deja les blocages sans avoir besoin de ce
    # heartbeat ; le retirer garde OPENCODE_LOG.txt au niveau de
    # verbosite d'un usage manuel et limite sa croissance.
    cmd = [
        bin_path, "run",
        "Suis les instructions du fichier joint.",
        "--file", prompt_file.name,
        "--dir", str(target_dir),
        "--auto",
    ]
    if model:
        cmd.extend(["--model", model])

    # opencode se fie a $PWD plutot qu'au cwd reel du processus :
    # sans cette correction, il travaillerait dans le repertoire
    # de DeBuilder (celui de la boucle) au lieu du projet cible.
    env = {**os.environ, "PWD": str(target_dir)}
    env.update(_web_tools_env(env, read_only=read_only))

    label = " (lecture seule)" if read_only else ""
    _log(f"[agent] opencode run --model {model or '(default)'}{label} [...]")

    log_file = target_dir / "OPENCODE_LOG.txt"
    _rotate_log_if_large(log_file)

    try:
        return _exec_opencode(cmd, env, target_dir, model, prompt, log_file)
    finally:
        try:
            os.unlink(prompt_file.name)
        except OSError:
            pass


def _web_tools_env(
    base_env: dict[str, str],
    read_only: bool = False,
) -> dict[str, str]:
    """Calcule les variables d'env de la session OpenCode.

    L'agent tourne sans supervision : pour ne pas rester sur des
    connaissances figees (API obsoletes, versions de paquets,
    ressources disponibles), il doit pouvoir consulter le web via les
    outils `websearch` et `webfetch` d'OpenCode.

    Args:
        base_env: Environnement de depart (typiquement ``os.environ``
            enrichi), utilise pour respecter un reglage deja pose par
            l'utilisateur.
        read_only: True pour la session Plan : refus FORCE des outils
            d'ecriture (edit/write/bash) via la config inline, meme si
            l'utilisateur les avait autorises ailleurs.

    Returns:
        Les variables a surcharger dans l'env du sous-processus
        OpenCode (dictionnaire vide si l'utilisateur a desactive les
        outils web et que la session n'est pas en lecture seule).
    """
    web_enabled = base_env.get("DEBUILDER_WEB_TOOLS", "1").strip().lower() not in _FALSY
    if not web_enabled and not read_only:
        return {}

    overrides: dict[str, str] = {}

    if web_enabled:
        # Un backend explicitement choisi par l'utilisateur (Exa,
        # Parallel, cle API dediee...) prime : ne rien imposer ici.
        if not any(base_env.get(var, "").strip() for var in _WEBSEARCH_BACKEND_VARS):
            overrides["OPENCODE_ENABLE_EXA"] = "1"

    config_content = _merge_config_content(
        base_env.get("OPENCODE_CONFIG_CONTENT", ""), read_only=read_only
    )
    if config_content is not None:
        overrides["OPENCODE_CONFIG_CONTENT"] = config_content

    return overrides


def _merge_config_content(existing: str, read_only: bool = False) -> str | None:
    """Accorde les permissions webfetch/websearch a OpenCode.

    Passe par ``OPENCODE_CONFIG_CONTENT`` (config inline, priorite la
    plus haute dans l'ordre de fusion d'OpenCode) plutot que par un
    `opencode.json` ecrit dans le projet cible : la configuration reste
    ainsi propre a DeBuilder, sans polluer ni le depot cible ni la
    config globale de la machine.

    `--auto` approuve deja les permissions non explicitement refusees,
    mais un `deny` present dans la config du projet cible (que l'agent
    peut lui-meme y ecrire par erreur) couperait l'acces au web sans
    cette surcharge.

    En mode ``read_only`` (session Plan), les outils d'ecriture
    ``edit``/``write``/``bash`` sont FORCEMENT refuses : le planner ne
    doit jamais pouvoir modifier le code ni executer de commandes.

    Args:
        existing: Valeur actuelle de ``OPENCODE_CONFIG_CONTENT``.
        read_only: Refuse forcement les outils d'ecriture.

    Returns:
        Le JSON fusionne, ou None s'il ne faut pas toucher a la valeur
        existante (JSON invalide : l'ecraser ferait perdre la config
        posee par l'utilisateur — sauf en lecture seule, ou les refus
        doivent etre poses a tout prix).
    """
    config: dict = {}
    if existing.strip():
        try:
            parsed = json.loads(existing)
        except json.JSONDecodeError:
            _log(
                "[agent] OPENCODE_CONFIG_CONTENT n'est pas un JSON valide : "
                "permissions web laissees en l'etat."
            )
            if not read_only:
                return None
        else:
            if not isinstance(parsed, dict):
                _log(
                    "[agent] OPENCODE_CONFIG_CONTENT n'est pas un objet JSON : "
                    "permissions web laissees en l'etat."
                )
                if not read_only:
                    return None
            else:
                config = parsed

    permission = config.get("permission")
    if not isinstance(permission, dict):
        permission = {}
    for tool in ("webfetch", "websearch"):
        permission.setdefault(tool, "allow")
    if read_only:
        # Session Plan : refus stricts, ils ecrasent toute valeur
        # anterieure (y compris un `allow` de l'utilisateur).
        for tool in ("edit", "write", "bash"):
            permission[tool] = "deny"
    config["permission"] = permission

    return json.dumps(config)


def _exec_opencode(
    cmd: list[str],
    env: dict[str, str],
    target_dir: Path,
    model: str,
    prompt: str,
    log_file: Path,
) -> subprocess.CompletedProcess:
    with open(log_file, "a") as lf:
        lf.write(f"\n=== Iteration {_timestamp()} ===\n")
        lf.write(f"Model: {model}\n")
        lf.write(f"Prompt length: {len(prompt)} chars\n\n")

        proc = subprocess.Popen(
            cmd,
            cwd=str(target_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

        # proc.wait(timeout=...) seul ne suffit pas : si OpenCode ne
        # produit plus aucune sortie (bloque en attente d'une
        # confirmation qu'il ne recevra jamais), la boucle de lecture
        # ci-dessous reste figee indefiniment et le wait() n'est
        # jamais atteint. Ce watchdog tue le groupe de processus s'il
        # reste silencieux trop longtemps, ou au bout d'une duree
        # totale absolue, mais laisse tourner une iteration active
        # (qui continue de produire de la sortie).
        timed_out = threading.Event()
        stopped = threading.Event()
        activity = {"last_output": time.monotonic(), "start": time.monotonic()}

        def _watchdog() -> None:
            while not stopped.wait(1):
                now = time.monotonic()
                idle = now - activity["last_output"]
                elapsed = now - activity["start"]
                if idle >= _OPENCODE_INACTIVITY_TIMEOUT_SECONDS or elapsed >= _OPENCODE_MAX_SECONDS:
                    timed_out.set()
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    return

        watchdog = threading.Thread(target=_watchdog, daemon=True)
        watchdog.start()
        try:
            stdout_lines: list[str] = []
            for line in proc.stdout:
                activity["last_output"] = time.monotonic()
                # Sanitization au point de capture : cette meme sortie
                # est ensuite injectee dans PROGRESS.md et commitee sur
                # le depot cible (potentiellement pushee sur GitHub).
                clean = sanitize_text(strip_ansi(line))
                stdout_lines.append(clean)
                lf.write(clean)
                lf.flush()
            proc.wait()
        finally:
            stopped.set()

    stdout_text = "".join(stdout_lines)
    if timed_out.is_set():
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=-1,
            stdout=stdout_text,
            stderr=(
                f"Timeout : aucune sortie pendant "
                f"{_OPENCODE_INACTIVITY_TIMEOUT_SECONDS}s, ou duree totale "
                f"depassant {_OPENCODE_MAX_SECONDS}s (processus tue)"
            ),
        )
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout=stdout_text,
        stderr="",
    )


def _rotate_log_if_large(log_file: Path, max_bytes: int = _MAX_LOG_BYTES) -> None:
    """Tronque OPENCODE_LOG.txt s'il devient trop volumineux.

    Args:
        log_file: Chemin du fichier de log.
        max_bytes: Taille maximale avant troncature.
    """
    if not log_file.exists() or log_file.stat().st_size <= max_bytes:
        return
    content = log_file.read_text(encoding="utf-8", errors="replace")
    kept = content[-(max_bytes // 2):]
    cut = kept.find("\n=== Iteration ")
    if cut > 0:
        kept = kept[cut:]
    log_file.write_text(
        "[... historique tronque pour limiter la taille du log ...]\n" + kept,
        encoding="utf-8",
    )


def _handle_barriers(target_dir: Path, barrier_files: list[Path]) -> None:
    for bf in barrier_files:
        while bf.exists():
            if is_done(target_dir):
                return
            time.sleep(5)


def _log(message: str) -> None:
    print(message, flush=True)


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
