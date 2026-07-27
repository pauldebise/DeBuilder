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
from pathlib import Path

from src.core.state import (
    clear_suggestions,
    is_done,
    read_state,
    update_progress,
)
from src.core.git import stage_and_commit_all
from src.core.secrets import sanitize_text
from src.utils.text import strip_ansi

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


def run_iteration(target_dir: Path) -> bool:
    """Execute une iteration complete de l'agent.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        True si l'agent doit continuer, False si arret demande.
    """
    if is_done(target_dir):
        return False

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
        )

        _log(f"[agent] Lancement d'OpenCode...")
        result = _run_opencode(target_dir, prompt)
        _log(f"[agent] OpenCode termine (code={result.returncode})")

        if result.returncode != 0 and result.stderr:
            _log(f"[agent] Erreur OpenCode: {result.stderr[:500]}")

        barrier_files = sorted(target_dir.glob("BARRIER_*"))
        if barrier_files:
            _log(f"[agent] {len(barrier_files)} barriere(s) detectee(s), mise en pause...")
            _handle_barriers(target_dir, barrier_files)

        _update_state_files(target_dir, result, suggestions_md, progress_md)
    except Exception as exc:
        # Une iteration ne doit jamais tuer la boucle autonome : sur
        # un pod sans surveillance, un crash non rattrape ici arrete
        # l'agent de facon definitive jusqu'a intervention manuelle.
        _log(f"[agent] ERREUR inattendue pendant l'iteration : {exc}")
        _record_iteration_exception(target_dir, exc)

    committed, detail = stage_and_commit_all(target_dir, f"iteration {_timestamp()}")
    if not committed:
        _log(
            "[agent] ATTENTION: echec du commit/push automatique de fin "
            f"d'iteration: {sanitize_text(detail)[:500]}"
        )

    return not is_done(target_dir)


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


def _run_opencode(target_dir: Path, prompt: str) -> subprocess.CompletedProcess:
    import shutil

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
