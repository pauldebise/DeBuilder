"""Tests pour le parsing du contrat de tache (src/utils/task_parser.py)."""

from src.utils.task_parser import (
    CheckItem,
    all_boxes_checked,
    extract_test_command,
    parse_checkboxes,
    parse_task,
)

_TASK_MD = """# Tache de l'Iteration

## Objectif

Implementer l'authentification.

## Criteres d'Acceptation

- [ ] login fonctionne
- [x] logout fonctionne

## Commande de Test

python -m pytest -q

## Sous-Taches

- [x] creer le modele utilisateur
- [ ] ajouter la session
"""


def test_parse_task_objective():
    task = parse_task(_TASK_MD)
    assert task.objective == "Implementer l'authentification."


def test_parse_task_acceptance_criteria():
    task = parse_task(_TASK_MD)
    assert task.acceptance == [
        CheckItem(text="login fonctionne", checked=False),
        CheckItem(text="logout fonctionne", checked=True),
    ]


def test_parse_task_subtasks():
    task = parse_task(_TASK_MD)
    assert task.subtasks == [
        CheckItem(text="creer le modele utilisateur", checked=True),
        CheckItem(text="ajouter la session", checked=False),
    ]


def test_parse_task_test_command_plain_line():
    assert parse_task(_TASK_MD).test_command == "python -m pytest -q"


def test_parse_task_test_command_fenced_block():
    md = "## Commande de Test\n\n```\npytest -x\n```\n"
    assert parse_task(md).test_command == "pytest -x"


def test_parse_task_empty_sections():
    md = "# Tache\n\n## Objectif\n\nAucun.\n"
    task = parse_task(md)
    assert task.acceptance == []
    assert task.subtasks == []
    assert task.test_command == ""


def test_all_boxes_checked_true():
    md = "# Tache\n\n## Sous-Taches\n\n- [x] a\n- [x] b\n"
    assert all_boxes_checked(parse_task(md)) is True


def test_all_boxes_checked_false_when_one_unchecked():
    md = "# Tache\n\n## Sous-Taches\n\n- [x] a\n- [ ] b\n"
    assert all_boxes_checked(parse_task(md)) is False


def test_all_boxes_checked_false_when_no_boxes():
    md = "# Tache\n\n## Sous-Taches\n\n(vide)\n"
    assert all_boxes_checked(parse_task(md)) is False


def test_all_boxes_checked_includes_acceptance():
    md = "# Tache\n\n## Criteres d'Acceptation\n\n- [ ] critere\n\n## Sous-Taches\n\n- [x] a\n"
    assert all_boxes_checked(parse_task(md)) is False


def test_extract_test_command_stops_at_next_heading():
    md = "## Commande de Test\n\n## Sous-Taches\n\n- [ ] a\n"
    assert extract_test_command(md) == ""


def test_extract_test_command_missing_section():
    assert extract_test_command("# Objectif\nrien\n") == ""


def test_extract_test_command_case_insensitive():
    md = "## commande de test\n\nmake test\n"
    assert extract_test_command(md) == "make test"


def test_parse_checkboxes_scans_whole_document():
    md = (
        "# Rapport\n\n"
        "## Checklist du Cahier des Charges\n\n"
        "- [x] item 1\n"
        "- [ ] item 2\n\n"
        "## Autre section\n\n- [x] autre\n"
    )

    boxes = parse_checkboxes(md)

    assert boxes == [
        CheckItem(text="item 1", checked=True),
        CheckItem(text="item 2", checked=False),
        CheckItem(text="autre", checked=True),
    ]
