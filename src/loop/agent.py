"""Logique d'iteration de l'agent DeBuilder.

Ce module contient la fonction run_iteration() appelee
par agent_loop.sh a chaque tour de boucle.

L'agent n'a aucune memoire interne entre deux iterations:
il reconstruit son contexte depuis les fichiers d'etat.
"""

import os
import subprocess
import time
from pathlib import Path

from src.core.state import (
    clear_suggestions,
    is_done,
    read_state,
    update_progress,
)
from src.core.git import stage_and_commit_all


def run_iteration(target_dir: Path) -> bool:
    """Execute une iteration complete de l'agent.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        True si l'agent doit continuer, False si arret demande.
    """
    if is_done(target_dir):
        return False

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

    stage_and_commit_all(target_dir, f"iteration {_timestamp()}")

    return not is_done(target_dir)


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
    cmd = [bin_path]
    if model:
        cmd.extend(["--model", model])
    cmd.extend(["--prompt", prompt])

    return subprocess.run(
        cmd,
        cwd=str(target_dir),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
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
    if result.stdout.strip():
        update_progress(target_dir, result.stdout.strip())
    elif result.returncode != 0:
        error_msg = result.stderr.strip() or f"OpenCode a echoue (code {result.returncode})"
        update_progress(
            target_dir,
            f"- **Action realisee** : Tentative d'iteration\n"
            f"- **Resultat** : ECHEC\n"
            f"- **Problemes rencontres** : {error_msg[:500]}\n"
            f"- **Solutions envisagees** : Verifier la cle API et la configuration.\n",
        )

    if suggestions_md.strip():
        clear_suggestions(target_dir)


def _log(message: str) -> None:
    print(message, flush=True)


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
