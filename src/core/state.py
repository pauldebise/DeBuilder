"""Gestion des fichiers d'etat du projet cible.

Les fichiers d'etat (AGENTS.md, PROGRESS.md, BENCHMARKS.md,
SUGGESTIONS.md, RESOURCES_NEEDED.md, TASK.md, PLAN.md,
ARCHITECTURE.md, SPEC_COVERAGE.md, FINISHED_REPORT.md, DONE) sont
stockes dans le repertoire du projet cible et synchronisent l'agent
et l'interface GUI via le systeme de fichiers.

Toute lecture/ecriture doit passer par le mecanisme de
verrouillage (filelock) pour eviter les race conditions.
"""

import os
import re
from pathlib import Path

from src.core.filelock import file_lock

STATE_FILES = [
    "AGENTS.md",
    "PROGRESS.md",
    "BENCHMARKS.md",
    "SUGGESTIONS.md",
    "RESOURCES_NEEDED.md",
    "TASK.md",
    "PLAN.md",
    "ARCHITECTURE.md",
    "SPEC_COVERAGE.md",
    "FINISHED_REPORT.md",
    "REVIEW.md",
    "DONE",
]

_PROGRESS_SEPARATOR = "\n## Prochaine Sous-Tache Prevue\n"
_ITERATION_HEADER_RE = re.compile(r"^## (.+)$", re.MULTILINE)
# Section persistante de PROGRESS.md (jamais tronquee par la fenetre glissante).
_ARCH_MARKER = "## Decisions d'Architecture"
# Titre de la section des entrees dans ARCHITECTURE.md.
_ARCH_DECISIONS_MARKER = "## Decisions"
# Longueur max d'une entree compactee d'ARCHITECTURE.md (une ligne).
_ARCH_COMPACTED_ENTRY_CHARS = 200

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


def init_project_state(
    target_dir: Path,
    instructions: str = "",
    hardware_info: str = "",
    fresh_repo: bool = False,
) -> None:
    """Initialise les fichiers d'etat dans le repertoire cible.

    Args:
        target_dir: Chemin du repertoire du projet cible.
        instructions: Objectif initial (cahier des charges).
        hardware_info: Infos materielles pour l'agent.
        fresh_repo: True si le depot vient d'etre cree par DeBuilder
            (session vierge, jamais un clone) : installe alors le hook
            pre-commit de tests.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    agents_content = _render_agents_template(instructions, hardware_info)
    progress_content = _render_progress_template()

    _write_file(target_dir / "AGENTS.md", agents_content)
    _write_file(target_dir / "PROGRESS.md", progress_content)
    _write_file(target_dir / "BENCHMARKS.md", "# Benchmarks\n\n")
    _touch(target_dir / "SUGGESTIONS.md")
    _touch(target_dir / "RESOURCES_NEEDED.md")
    _write_file(target_dir / "TASK.md", _render_template("TASK.md.tmpl"))
    _write_file(target_dir / "PLAN.md", _render_template("PLAN.md.tmpl"))
    _write_file(target_dir / "ARCHITECTURE.md", _render_template("ARCHITECTURE.md.tmpl"))
    _write_file(target_dir / "SPEC_COVERAGE.md", _render_template("SPEC_COVERAGE.md.tmpl"))
    _write_file(target_dir / "FINISHED_REPORT.md", _render_template("FINISHED_REPORT.md.tmpl"))

    if fresh_repo:
        install_test_hook(target_dir)


def install_test_hook(target_dir: Path) -> bool:
    """Installe le hook pre-commit de tests (session vierge uniquement).

    Garanties (cahier des charges §5.4) :
    - jamais en ecrasant un hook existant (depot deja configure) ;
    - sans effet si ``.git/hooks`` n'existe pas.

    Args:
        target_dir: Chemin du depot Git cible.

    Returns:
        True si le hook a ete installe, False sinon.
    """
    hooks_dir = target_dir / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return False
    hook_path = hooks_dir / "pre-commit"
    if hook_path.exists():
        return False

    template = (_TEMPLATES_DIR / "pre-commit.tmpl").read_text(encoding="utf-8")
    _write_file(hook_path, template)
    try:
        hook_path.chmod(hook_path.stat().st_mode | 0o111)
    except OSError:
        pass
    return True


def read_state(target_dir: Path, filename: str) -> str:
    """Lit un fichier d'etat avec verrouillage.

    Args:
        target_dir: Chemin du repertoire du projet cible.
        filename: Nom du fichier d'etat a lire.

    Returns:
        Contenu du fichier. Retourne "" si le fichier n'existe pas.
    """
    filepath = target_dir / filename
    with file_lock(filepath):
        if not filepath.exists():
            return ""
        return filepath.read_text(encoding="utf-8")


def write_state(target_dir: Path, filename: str, content: str) -> None:
    """Ecrit un fichier d'etat avec verrouillage.

    Args:
        target_dir: Chemin du repertoire du projet cible.
        filename: Nom du fichier d'etat a ecrire.
        content: Contenu a ecrire.
    """
    filepath = target_dir / filename
    with file_lock(filepath):
        _write_file(filepath, content)


def append_state(target_dir: Path, filename: str, content: str) -> None:
    """Ajoute du contenu a un fichier d'etat avec verrouillage.

    Args:
        target_dir: Chemin du repertoire du projet cible.
        filename: Nom du fichier d'etat.
        content: Contenu a ajouter.
    """
    filepath = target_dir / filename
    with file_lock(filepath):
        existing = ""
        if filepath.exists():
            existing = filepath.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
        _write_file(filepath, existing + content)


def update_progress(target_dir: Path, new_entry: str, max_iterations: int = 2) -> None:
    """Met a jour PROGRESS.md avec une fenetre glissante.

    Conserve uniquement les `max_iterations` dernieres iterations.

    Args:
        target_dir: Chemin du repertoire du projet cible.
        new_entry: Contenu de la nouvelle iteration (format Markdown).
        max_iterations: Nombre maximum d'iterations conservees.
    """
    filepath = target_dir / "PROGRESS.md"
    with file_lock(filepath):
        current = ""
        if filepath.exists():
            current = filepath.read_text(encoding="utf-8")

        separator_index = current.find(_PROGRESS_SEPARATOR)

        if separator_index == -1:
            header = "# Journal de Progression\n\n"
            existing_iterations = []
            next_task = ""
        else:
            header_and_body = current[:separator_index]
            next_task = current[separator_index + len(_PROGRESS_SEPARATOR):].strip()

            lines = header_and_body.strip().split("\n")
            if lines and lines[0].startswith("# Journal"):
                header = lines[0] + "\n\n"
                existing_iterations = _parse_iterations(lines[1:])
            else:
                header = "# Journal de Progression\n\n"
                existing_iterations = _parse_iterations(lines)

        all_iterations = [new_entry.strip()] + existing_iterations
        kept = all_iterations[:max_iterations]

        body_parts = [header]
        for i, iteration in enumerate(kept):
            if i == 0:
                label = "Derniere Iteration (N)"
            elif i == 1:
                label = "Iteration Precedente (N-1)"
            else:
                label = f"Iteration (N-{i})"
            if not iteration.startswith("##"):
                iteration = f"## {label}\n{iteration}"
            else:
                iteration = re.sub(r"^##[^\n]*", f"## {label}", iteration, count=1)
            body_parts.append(iteration + "\n")

        body_parts.append(_PROGRESS_SEPARATOR.lstrip("\n"))
        if next_task:
            body_parts.append(next_task + "\n")
        else:
            body_parts.append("\n")

        _write_file(filepath, "".join(body_parts))


def touch_done(target_dir: Path) -> None:
    """Cree le fichier DONE pour signaler l'arret de l'agent.

    Args:
        target_dir: Chemin du repertoire du projet cible.
    """
    write_state(target_dir, "DONE", "")


def is_done(target_dir: Path) -> bool:
    """Verifie si le fichier DONE existe.

    Args:
        target_dir: Chemin du repertoire du projet cible.

    Returns:
        True si le kill-switch est active.
    """
    return (target_dir / "DONE").exists()


def clear_suggestions(target_dir: Path) -> None:
    """Vide le fichier SUGGESTIONS.md apres traitement.

    Args:
        target_dir: Chemin du repertoire du projet cible.
    """
    write_state(target_dir, "SUGGESTIONS.md", "")


def repair_progress(target_dir: Path) -> bool:
    """Repare PROGRESS.md malforme a partir du template (cdc §4.1).

    Un fichier est considere malforme si le separateur de structure
    (« Prochaine Sous-Tache Prevue ») est absent, ou si la section
    persistante « Decisions d'Architecture » est tronquee. La
    reparation preserve le contenu existant au mieux : les entrees
    d'iterations et la prochaine tache sont conservees telles quelles,
    seule la structure manquante est restauree.

    Args:
        target_dir: Chemin du repertoire du projet cible.

    Returns:
        True si une reparation a eu lieu, False si le fichier etait
        deja valide (ou absent : rien a reparer, il sera recree par
        ``init_project_state``).
    """
    filepath = target_dir / "PROGRESS.md"
    with file_lock(filepath):
        if not filepath.exists():
            return False
        current = filepath.read_text(encoding="utf-8")
        if _progress_is_valid(current):
            return False
        _write_file(filepath, _rebuild_progress(current))
    return True


def _progress_is_valid(content: str) -> bool:
    return _PROGRESS_SEPARATOR in content and _ARCH_MARKER in content


def _rebuild_progress(current: str) -> str:
    template = _render_progress_template()

    if _PROGRESS_SEPARATOR in current:
        header_body, rest = current.split(_PROGRESS_SEPARATOR, 1)
        header_body = header_body.strip()
    else:
        header_body = current.strip()
        rest = ""

    if _ARCH_MARKER in rest:
        next_task = rest.split(_ARCH_MARKER, 1)[0].strip()
        arch_section = _ARCH_MARKER + rest.split(_ARCH_MARKER, 1)[1].strip()
    else:
        next_task = rest.strip()
        arch_section = (
            _ARCH_MARKER + template.split(_ARCH_MARKER, 1)[1].strip()
        )

    if not header_body.startswith("# "):
        header_body = "# Journal de Progression\n\n" + header_body

    return (
        header_body
        + "\n\n## Prochaine Sous-Tache Prevue\n"
        + (next_task + "\n" if next_task else "")
        + "\n"
        + arch_section
        + "\n"
    )


def compact_architecture(target_dir: Path, max_chars: int | None = None) -> bool:
    """Compacte ARCHITECTURE.md au-dela de son budget de taille (cdc §4.2).

    Les entrees les plus recentes (en tete de la section « Decisions »)
    sont conservees intactes ; les plus anciennes sont compactees en
    une ligne chacune sous « Historique compacte », pour que ce fichier
    ne gonfle pas indefiniment le prompt de chaque iteration.

    Args:
        target_dir: Chemin du repertoire du projet cible.
        max_chars: Budget de taille (defaut :
            ``DEBUILDER_ARCH_MAX_CHARS``, 4000).

    Returns:
        True si une compaction a eu lieu, False sinon.
    """
    if max_chars is None:
        max_chars = int(os.environ.get("DEBUILDER_ARCH_MAX_CHARS", "4000"))

    filepath = target_dir / "ARCHITECTURE.md"
    with file_lock(filepath):
        if not filepath.exists():
            return False
        content = filepath.read_text(encoding="utf-8")
        if len(content) <= max_chars:
            return False
        _write_file(filepath, _compact_arch_content(content, max_chars))
    return True


def _compact_arch_content(content: str, max_chars: int) -> str:
    marker_index = content.find(_ARCH_DECISIONS_MARKER)
    if marker_index == -1:
        # Aucune section Decisions : rien de structurant a compacter.
        return content

    line_end = content.find("\n", marker_index)
    if line_end == -1:
        line_end = len(content)
    header = content[:line_end].rstrip("\n")
    decisions_body = content[line_end + 1:]

    entries = [e.strip() for e in decisions_body.split("\n\n") if e.strip()]

    budget = max_chars - len(header) - len("## Historique compacte (entrees anciennes, une ligne chacune)")
    kept: list[str] = []
    for entry in entries:
        if not kept and len(entry) > budget:
            # Une entree geante (anomalie) : on la tronque plutot que
            # de tout vider.
            kept.append(entry[:max(budget, 200)])
            break
        if len(entry) <= budget:
            kept.append(entry)
            budget -= len(entry) + 2
        else:
            break

    compacted = [
        re.sub(r"\s+", " ", entry).strip()[:_ARCH_COMPACTED_ENTRY_CHARS]
        for entry in entries[len(kept):]
    ]

    parts = [header]
    if kept:
        parts.append("\n\n".join(kept))
    if compacted:
        parts.append(
            "## Historique compacte (entrees anciennes, une ligne chacune)\n"
            + "\n".join(compacted)
        )
    return "\n\n".join(parts) + "\n"


def _write_file(filepath: Path, content: str) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")


def _touch(filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.touch(exist_ok=True)


def _render_agents_template(instructions: str, hardware_info: str) -> str:
    if not hardware_info:
        hardware_info = "Non audite."
    template = (_TEMPLATES_DIR / "AGENTS.md.tmpl").read_text(encoding="utf-8")
    return template.replace("{{ instructions }}", instructions).replace(
        "{{ hardware_info }}", hardware_info
    )


def _render_progress_template() -> str:
    return (_TEMPLATES_DIR / "PROGRESS.md.tmpl").read_text(encoding="utf-8")


def _render_template(filename: str) -> str:
    return (_TEMPLATES_DIR / filename).read_text(encoding="utf-8")


def _parse_iterations(lines: list[str]) -> list[str]:
    iterations: list[str] = []
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if _ITERATION_HEADER_RE.match(stripped) and current:
            body = "\n".join(current).strip()
            if body:
                iterations.append(body)
            current = []
        current.append(line)
    if current:
        body = "\n".join(current).strip()
        if body:
            iterations.append(body)
    return iterations
