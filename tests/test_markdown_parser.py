"""Tests pour le module markdown_parser.py."""

from src.utils.markdown_parser import (
    parse_alerts,
    parse_benchmarks,
    parse_progress,
)


PROGRESS_CONTENT = """# Journal de Progression

## Derniere Iteration (N)
- **Action realisee** : Implemented login
- **Resultat** : Tests pass

## Iteration Precedente (N-1)
- **Action realisee** : Set up project
- **Resultat** : Skeleton ready

## Prochaine Sous-Tache Prevue
Ajouter l'authentification OAuth.
"""

BENCHMARKS_CONTENT = """# Benchmarks

| Modele | Score | Temps (s) |
|--------|-------|-----------|
| CNN    | 0.92  | 45.3      |
| LSTM   | 0.95  | 120.1     |

## Autres

| Parametre | Valeur |
|-----------|--------|
| batch_size | 32    |
| epochs    | 50    |
"""

ALERT_CONTENT = """## Derniere Iteration (N)
- **Action** : Training
- **Problemes** : stagnation de la loss constatee
- **Note** : possible bottleneck dans le data loader
"""


def test_parse_progress():
    result = parse_progress(PROGRESS_CONTENT)
    assert "Implemented login" in result["latest_iteration"]
    assert len(result["previous_iterations"]) == 1
    assert "Set up project" in result["previous_iterations"][0]
    assert "OAuth" in result["next_task"]


def test_parse_progress_empty():
    result = parse_progress("")
    assert result["latest_iteration"] == ""
    assert result["next_task"] == ""


def test_parse_benchmarks():
    results = parse_benchmarks(BENCHMARKS_CONTENT)
    assert len(results) >= 3
    models = [r["Modele"] for r in results if "Modele" in r]
    assert "CNN" in models
    assert "LSTM" in models
    vals = [r for r in results if "Parametre" in r]
    assert len(vals) >= 1


def test_parse_benchmarks_empty():
    results = parse_benchmarks("")
    assert results == []


def test_parse_alerts():
    alerts = parse_alerts(ALERT_CONTENT)
    keywords = {a["keyword"] for a in alerts}
    assert "stagnation" in keywords
    assert "bottleneck" in keywords


def test_parse_alerts_empty():
    alerts = parse_alerts("No issues found.")
    assert alerts == []


def test_parse_progress_no_next_task():
    content = """# Journal

## Derniere Iteration (N)
- Did stuff
"""
    result = parse_progress(content)
    assert "Did stuff" in result["latest_iteration"]
    assert result["next_task"] == ""
