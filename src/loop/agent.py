"""Logique d'iteration de l'agent DeBuilder.

Ce module contient la fonction run_iteration() appelee
par agent_loop.sh a chaque tour de boucle.

L'agent n'a aucune memoire interne entre deux iterations:
il reconstruit son contexte depuis les fichiers d'etat.
"""

import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from src.core.circuit_breaker import CircuitBreaker
from src.core.state import (
    clear_suggestions,
    is_done,
    read_state,
    update_progress,
)
from src.core.git import head_commit, stage_and_commit_all, status_files, tag_iteration
from src.core.iterations import append_entry, read_entries
from src.core.secrets import sanitize_text
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
# L'agent est instruit (cf. _build_prompt) de lancer les commandes
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

# Longueur max du fallback ecrit dans PROGRESS.md quand l'agent ne l'a
# pas mis a jour lui-meme (cf. _update_state_files) : la sortie brute
# complete est deja dans OPENCODE_LOG.txt, l'injecter en entier ferait
# regonfler indefiniment le prompt des iterations suivantes.
_MAX_FALLBACK_PROGRESS_CHARS = 4000

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
    continue_loop: bool = True

    def __bool__(self) -> bool:
        """Compat : ``bool(result)`` vaut l'ancien booleen de boucle."""
        return self.continue_loop

    def __post_init__(self) -> None:
        if self.tags is None:
            self.tags = []


def run_iteration(
    target_dir: Path,
    iteration_number: int | None = None,
) -> IterationResult:
    """Execute une iteration complete de l'agent.

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

    try:
        agents_md = read_state(target_dir, "AGENTS.md")
        progress_md = read_state(target_dir, "PROGRESS.md")
        benchmarks_md = read_state(target_dir, "BENCHMARKS.md")
        suggestions_md = read_state(target_dir, "SUGGESTIONS.md")
        resources_md = read_state(target_dir, "RESOURCES_NEEDED.md")

        prompt = _build_prompt(
            agents_md=agents_md,
            progress_md=progress_md,
            benchmarks_md=benchmarks_md,
            suggestions_md=suggestions_md,
            resources_md=resources_md,
            recovery_md=_recovery_section(target_dir),
        )

        _log(f"[agent] Lancement d'OpenCode...")
        completed = _run_opencode(target_dir, prompt, model=model)
        _log(f"[agent] OpenCode termine (code={completed.returncode})")

        result.exit_code = completed.returncode
        result.failure_type = _classify_failure(completed)

        if completed.returncode != 0 and completed.stderr:
            _log(f"[agent] Erreur OpenCode: {completed.stderr[:500]}")

        # Alimente le circuit breaker avec le resultat de la session
        # OpenCode uniquement (pas la gate de tests : un code qui ne
        # passe pas les tests n'est pas une panne d'API).
        if result.failure_type == "":
            breaker.record_success()
        else:
            breaker.record_failure(result.failure_type)

        barrier_files = sorted(target_dir.glob("BARRIER_*"))
        if barrier_files:
            _log(f"[agent] {len(barrier_files)} barriere(s) detectee(s), mise en pause...")
            _handle_barriers(target_dir, barrier_files)

        _update_state_files(target_dir, completed, suggestions_md, progress_md)
    except Exception as exc:
        # Une iteration ne doit jamais tuer la boucle autonome : sur
        # un pod sans surveillance, un crash non rattrape ici arrete
        # l'agent de facon definitive jusqu'a intervention manuelle.
        _log(f"[agent] ERREUR inattendue pendant l'iteration : {exc}")
        _record_iteration_exception(target_dir, exc)
        result.exit_code = 1
        result.failure_type = "exception"
        breaker.record_failure("error")

    # Gate de tests deterministe : uniquement si la session s'est
    # terminee proprement (une session morte laisse le repo dans un
    # etat imprevisible, y lancer les tests serait un faux signal).
    if result.failure_type == "":
        _run_test_gate(target_dir, result)

    # Capture AVANT le commit de fin d'iteration : le diff de
    # l'iteration (base de la detection de no-op, phase 6) doit
    # refleter le travail de la session, pas le commit lui-meme.
    changed = status_files(target_dir)
    result.changed_files = len(changed)
    result.no_op = _is_no_op(changed)

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


def _build_prompt(
    agents_md: str,
    progress_md: str,
    benchmarks_md: str,
    suggestions_md: str,
    resources_md: str,
    recovery_md: str = "",
) -> str:
    parts = []

    if agents_md:
        parts.append("## Objectifs et Contexte\n\n" + agents_md)

    if benchmarks_md.strip():
        parts.append(
            "## Benchmarks (ne pas regresser)\n\n" + benchmarks_md
        )

    if progress_md.strip():
        parts.append(
            "## Progression Recente\n\n"
            "Voici l'etat d'avancement des dernieres iterations:\n\n"
            + progress_md
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
        "1. Analyse l'etat actuel du projet et la progression recente.\n"
        "2. Determine la prochaine action a realiser.\n"
        "3. Execute cette action dans le repertoire de travail.\n"
        "4. Mets a jour PROGRESS.md avec :\n"
        "   - Action realisee\n"
        "   - Resultat obtenu\n"
        "   - Problemes rencontres et solutions\n"
        "   - Prochaine sous-tache prevue\n"
        "   Si tu prends une decision d'architecture structurante (stack, "
        "schema de donnees, convention...), note-la aussi dans la section "
        "'Decisions d'Architecture' de PROGRESS.md (jamais tronquee par la "
        "fenetre glissante), en restant concis.\n"
        "5. Si une suggestion utilisateur est presente, "
        "justifie ta decision (acceptee/rejetee/reportee) dans PROGRESS.md.\n"
        "6. Mets a jour BENCHMARKS.md si de nouvelles metriques "
        "ont ete collectees (temps, scores, utilisation hardware).\n"
        "7. Ne JAMAIS inclure de cles API ou secrets dans les logs/commits.\n"
        "8. Si une ressource te manque, trouve une solution de contournement "
        "et ne bloque jamais. Si tu signales le besoin dans "
        "RESOURCES_NEEDED.md, justifie-y explicitement pourquoi cette "
        "ressource serait un plus et quelle solution palliative tu as deja "
        "mise en place en attendant.\n"
        "9. Pour toute commande longue (entrainement ML, build volumineux, "
        "telechargement de dataset...) : lance-la en arriere-plan "
        "(ex: `nohup ... > train.log 2>&1 &`) et redonne la main immediatement. "
        "Ne bloque JAMAIS cet appel en attendant sa fin : cette iteration a "
        "un delai maximum, et le processus serait tue avant la fin de "
        "l'entrainement. Verifie et rapporte sa progression (via son fichier "
        "de log ou TensorBoard) aux iterations suivantes.\n"
        "10. Tu as un acces internet via les outils `websearch` "
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
    env.update(_web_tools_env(env))

    _log(f"[agent] opencode run --model {model or '(default)'} [...]")

    log_file = target_dir / "OPENCODE_LOG.txt"
    _rotate_log_if_large(log_file)

    try:
        return _exec_opencode(cmd, env, target_dir, model, prompt, log_file)
    finally:
        try:
            os.unlink(prompt_file.name)
        except OSError:
            pass


def _web_tools_env(base_env: dict[str, str]) -> dict[str, str]:
    """Calcule les variables d'env activant la recherche en ligne.

    L'agent tourne sans supervision : pour ne pas rester sur des
    connaissances figees (API obsoletes, versions de paquets,
    ressources disponibles), il doit pouvoir consulter le web via les
    outils `websearch` et `webfetch` d'OpenCode.

    Args:
        base_env: Environnement de depart (typiquement ``os.environ``
            enrichi), utilise pour respecter un reglage deja pose par
            l'utilisateur.

    Returns:
        Les variables a surcharger dans l'env du sous-processus
        OpenCode (dictionnaire vide si l'utilisateur a desactive la
        fonctionnalite via ``DEBUILDER_WEB_TOOLS=0``).
    """
    if base_env.get("DEBUILDER_WEB_TOOLS", "1").strip().lower() in _FALSY:
        return {}

    overrides: dict[str, str] = {}

    # Un backend explicitement choisi par l'utilisateur (Exa, Parallel,
    # cle API dediee...) prime : ne rien imposer dans ce cas.
    if not any(base_env.get(var, "").strip() for var in _WEBSEARCH_BACKEND_VARS):
        overrides["OPENCODE_ENABLE_EXA"] = "1"

    config_content = _merge_config_content(base_env.get("OPENCODE_CONFIG_CONTENT", ""))
    if config_content is not None:
        overrides["OPENCODE_CONFIG_CONTENT"] = config_content

    return overrides


def _merge_config_content(existing: str) -> str | None:
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

    Args:
        existing: Valeur actuelle de ``OPENCODE_CONFIG_CONTENT``.

    Returns:
        Le JSON fusionne, ou None s'il ne faut pas toucher a la valeur
        existante (JSON invalide : l'ecraser ferait perdre la config
        posee par l'utilisateur).
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
            return None
        if not isinstance(parsed, dict):
            _log(
                "[agent] OPENCODE_CONFIG_CONTENT n'est pas un objet JSON : "
                "permissions web laissees en l'etat."
            )
            return None
        config = parsed

    permission = config.get("permission")
    if not isinstance(permission, dict):
        permission = {}
    for tool in ("webfetch", "websearch"):
        permission.setdefault(tool, "allow")
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


def _update_state_files(
    target_dir: Path,
    result: subprocess.CompletedProcess,
    suggestions_md: str,
    progress_before: str,
) -> None:
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        stdout = result.stdout.strip() if result.stdout else ""
        detail = (stderr or stdout)[-500:]
        update_progress(
            target_dir,
            f"- **Action realisee** : Tentative d'iteration\n"
            f"- **Resultat** : ECHEC (code {result.returncode})\n"
            f"- **Problemes rencontres** : {detail}\n"
            f"- **Solutions envisagees** : Verifier la cle API et la configuration d'OpenCode.\n",
        )
    elif result.stdout.strip():
        # L'agent est deja instruit de mettre a jour PROGRESS.md
        # lui-meme pendant la session OpenCode (consigne #4 du
        # prompt). S'il l'a fait, ne pas ecraser son entree structuree
        # avec le transcript brut (verbeux, --log-level DEBUG). On ne
        # retombe sur un extrait de la sortie brute (tronque : le
        # detail complet est deja dans OPENCODE_LOG.txt) que si
        # PROGRESS.md n'a pas bouge, en garde-fou.
        progress_after = read_state(target_dir, "PROGRESS.md")
        if progress_after == progress_before:
            update_progress(
                target_dir, result.stdout.strip()[-_MAX_FALLBACK_PROGRESS_CHARS:]
            )

    if suggestions_md.strip():
        clear_suggestions(target_dir)


def _log(message: str) -> None:
    print(message, flush=True)


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
