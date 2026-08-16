"""Parsing du contrat de tache (TASK.md).

Le contrat est redige par la session Plan (sous forme d'un bloc de
code ```TASK) et materialise par la boucle dans TASK.md. La session
Implement coche les cases au fur et a mesure ; les gates deterministes
de la boucle relisent ce fichier pour verifier que toutes les cases
sont cochees.
"""

import re
from dataclasses import dataclass, field

# En-tetes reconnus dans TASK.md (case-insensibles, accents exclus).
_SECTION_RE = re.compile(
    r"^#+\s*(objectif|criteres d'acceptation|commande de test|sous-taches)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_TEST_COMMAND_RE = re.compile(
    r"^#+\s*commande de test\s*$", re.IGNORECASE | re.MULTILINE
)
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+)$")
_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

_SECTION_KEYS = {
    "objectif": "objective",
    "criteres d'acceptation": "acceptance",
    "commande de test": "test_command",
    "sous-taches": "subtasks",
}


@dataclass
class CheckItem:
    """Une case a cocher de TASK.md."""

    text: str
    checked: bool


@dataclass
class ParsedTask:
    """Contrat de tache parse."""

    objective: str = ""
    acceptance: list[CheckItem] = field(default_factory=list)
    test_command: str = ""
    subtasks: list[CheckItem] = field(default_factory=list)


def parse_task(markdown: str) -> ParsedTask:
    """Parse le contenu de TASK.md.

    Args:
        markdown: Contenu Markdown du contrat de tache.

    Returns:
        Contrat de tache structure (sections absentes = champs vides).
    """
    task = ParsedTask()
    sections = _split_sections(markdown)

    objective_body = sections.get("objective", "")
    task.objective = objective_body.strip()

    task.acceptance = _parse_checkboxes(sections.get("acceptance", ""))

    task.test_command = extract_test_command(markdown)

    task.subtasks = _parse_checkboxes(sections.get("subtasks", ""))
    return task


def all_boxes_checked(task: ParsedTask) -> bool:
    """True si TASK.md contient au moins une case et que toutes sont cochees.

    Un TASK.md sans aucune case est un contrat invalide : la gate doit
    echouer plutot que de passer par vacuite.
    """
    boxes = task.acceptance + task.subtasks
    return bool(boxes) and all(item.checked for item in boxes)


def extract_test_command(markdown: str) -> str:
    """Extrait la commande de la section « Commande de test ».

    Meme convention que pour AGENTS.md : premier bloc de code delimite
    par des triples backticks, sinon la premiere ligne non vide de la
    section.

    Args:
        markdown: Contenu Markdown (TASK.md, AGENTS.md...).

    Returns:
        La commande extraite, ou chaine vide.
    """
    match = _TEST_COMMAND_RE.search(markdown)
    if not match:
        return ""
    start = match.end()
    end = len(markdown)
    next_section = re.search(r"^#+", markdown[start:], re.MULTILINE)
    if next_section:
        end = start + next_section.start()
    body = markdown[start:end]

    fence = _FENCE_RE.search(body)
    if fence:
        return fence.group(1).strip()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _split_sections(markdown: str) -> dict[str, str]:
    """Decoupe le Markdown en corps de sections par titre reconnu."""
    matches = list(_SECTION_RE.finditer(markdown))
    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        key = _SECTION_KEYS.get(match.group(1).lower())
        if key is None:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[match.end():end]
        sections[key] = _strip_trailing_heading(body)
    return sections


def _strip_trailing_heading(body: str) -> str:
    """Coupe le corps de section au prochain titre (#) non reconnu."""
    cut = re.search(r"^#", body, re.MULTILINE)
    if cut:
        body = body[:cut.start()]
    return body


def _parse_checkboxes(body: str) -> list[CheckItem]:
    items: list[CheckItem] = []
    for line in body.splitlines():
        match = _CHECKBOX_RE.match(line)
        if match:
            items.append(
                CheckItem(
                    text=match.group(2).strip(),
                    checked=match.group(1).lower() == "x",
                )
            )
    return items
