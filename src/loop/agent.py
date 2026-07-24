"""Logique d'iteration de l'agent DeBuilder.

Ce module contient la fonction run_iteration() appelee
par agent_loop.sh a chaque tour de boucle.

L'agent n'a aucune memoire interne entre deux iterations:
il reconstruit son contexte depuis les fichiers d'etat.
"""

import os
import signal
import subprocess
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

# Duree max d'une iteration OpenCode. Sur un pod sans surveillance,
# un blocage (ex: attente d'une confirmation interactive) ne doit
# jamais figer la boucle indefiniment.
_OPENCODE_TIMEOUT_SECONDS = 600


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

        _update_state_files(target_dir, result, suggestions_md)
    except Exception as exc:
        # Une iteration ne doit jamais tuer la boucle autonome : sur
        # un pod sans surveillance, un crash non rattrape ici arrete
        # l'agent de facon definitive jusqu'a intervention manuelle.
        _log(f"[agent] ERREUR inattendue pendant l'iteration : {exc}")
        _record_iteration_exception(target_dir, exc)

    if not stage_and_commit_all(target_dir, f"iteration {_timestamp()}"):
        _log("[agent] ATTENTION: echec du commit/push automatique de fin d'iteration.")

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
        "5. Si une suggestion utilisateur est presente, "
        "justifie ta decision (acceptee/rejetee/reportee) dans PROGRESS.md.\n"
        "6. Mets a jour BENCHMARKS.md si de nouvelles metriques "
        "ont ete collectees (temps, scores, utilisation hardware).\n"
        "7. Ne JAMAIS inclure de cles API ou secrets dans les logs/commits.\n"
        "8. Si une ressource te manque, trouve une solution de contournement "
        "et ne bloque jamais. Signale le besoin dans RESOURCES_NEEDED.md."
    )

    return "\n\n".join(parts)


def _run_opencode(target_dir: Path, prompt: str) -> subprocess.CompletedProcess:
    import shutil

    model = os.environ.get("DEBUILDER_MODEL", "")
    bin_path = shutil.which("opencode") or "/usr/local/bin/opencode"
    # --auto : approuve automatiquement les permissions OpenCode.
    # Sans ce flag, une action necessitant confirmation bloque le
    # process en attente d'une reponse sur stdin, qui est ferme
    # (DEVNULL) puisque la boucle tourne sans surveillance. Conforme
    # a l'exigence d'autonomie/non-blocage du cahier des charges.
    cmd = [bin_path, "run", prompt, "--dir", str(target_dir), "--auto"]
    if model:
        cmd.extend(["--model", model])

    # opencode se fie a $PWD plutot qu'au cwd reel du processus :
    # sans cette correction, il travaillerait dans le repertoire
    # de DeBuilder (celui de la boucle) au lieu du projet cible.
    env = {**os.environ, "PWD": str(target_dir)}

    _log(f"[agent] opencode run --model {model or '(default)'} [...]")

    log_file = target_dir / "OPENCODE_LOG.txt"
    _rotate_log_if_large(log_file)

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
        # jamais atteint. Ce minuteur tue le groupe de processus au
        # bout de _OPENCODE_TIMEOUT_SECONDS quoi qu'il arrive.
        timed_out = threading.Event()

        def _kill_on_timeout() -> None:
            timed_out.set()
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass

        timer = threading.Timer(_OPENCODE_TIMEOUT_SECONDS, _kill_on_timeout)
        timer.start()
        try:
            stdout_lines: list[str] = []
            for line in proc.stdout:
                # Sanitization au point de capture : cette meme sortie
                # est ensuite injectee dans PROGRESS.md et commitee sur
                # le depot cible (potentiellement pushee sur GitHub).
                clean = sanitize_text(strip_ansi(line))
                stdout_lines.append(clean)
                lf.write(clean)
                lf.flush()
            proc.wait()
        finally:
            timer.cancel()

    stdout_text = "".join(stdout_lines)
    if timed_out.is_set():
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=-1,
            stdout=stdout_text,
            stderr=f"Timeout apres {_OPENCODE_TIMEOUT_SECONDS}s (processus tue)",
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
        update_progress(target_dir, result.stdout.strip())

    if suggestions_md.strip():
        clear_suggestions(target_dir)


def _log(message: str) -> None:
    print(message, flush=True)


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
