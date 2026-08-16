"""Utilitaires de parsing Markdown.

Extraction de donnees structurees depuis les fichiers d'etat
Markdown (PROGRESS.md, BENCHMARKS.md).
"""

import re
from typing import Any

_SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_TABLE_SEP_RE = re.compile(r"^\|(?:[-:\s]+\|)+\s*$")
_COVERAGE_SUMMARY_RE = re.compile(
    r"couverture\s*:\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE
)
_ALERT_KEYWORDS = [
    "stagnation",
    "bottleneck",
    "goulot",
    "erreur critique",
    "error",
    "failed",
    "echec",
    "regression",
    "saturation",
    "bloquant",
]


def parse_progress(content: str) -> dict[str, Any]:
    """Parse le contenu de PROGRESS.md en structure exploitable.

    Args:
        content: Contenu brut de PROGRESS.md.

    Returns:
        Dictionnaire avec les sections cles de progression.
    """
    sections = _split_sections(content)
    result: dict[str, Any] = {
        "latest_iteration": "",
        "previous_iterations": [],
        "next_task": "",
        "alerts": [],
    }

    iteration_sections = []
    for title, body in sections:
        if "prochaine sous-tache" in title.lower() or "prochaine" in title.lower():
            result["next_task"] = body.strip()
        elif "iteration" in title.lower():
            iteration_sections.append((title, body.strip()))

    if iteration_sections:
        result["latest_iteration"] = iteration_sections[0][1]
        result["previous_iterations"] = [body for _, body in iteration_sections[1:]]

    return result


def parse_coverage(content: str) -> dict[str, int] | None:
    """Couverture du cahier des charges depuis SPEC_COVERAGE.md.

    Deux sources, par ordre de preference :
    1. la ligne de bilan « Couverture : X / N ... » maintenue par la
       session Plan (source de verite : N est le nombre total d'items
       du cahier des charges) ;
    2. en repli, le comptage des lignes du tableau dont les colonnes
       d'implementation et de test sont renseignees (les lignes n'y
       figurant qu'une fois l'item traite, ce repli vaut surtout pour
       les fichiers d'etat crees avant l'introduction du bilan).

    Args:
        content: Contenu brut de SPEC_COVERAGE.md.

    Returns:
        ``{"done": x, "total": n, "percent": p}`` ou None si le
        fichier ne fournit aucune information exploitable.
    """
    summary = _COVERAGE_SUMMARY_RE.search(content)
    if summary:
        done, total = int(summary.group(1)), int(summary.group(2))
        if total > 0:
            done = min(done, total)
            return {"done": done, "total": total, "percent": round(done * 100 / total)}

    done = 0
    total = 0
    for table in _extract_tables(content):
        for row in table:
            impl_cell = _cell_like(row, "implement")
            test_cell = _cell_like(row, "test")
            if impl_cell is None and test_cell is None:
                continue
            total += 1
            if impl_cell and test_cell:
                done += 1
    if total == 0:
        return None
    return {"done": done, "total": total, "percent": round(done * 100 / total)}


def _cell_like(row: dict[str, str], fragment: str) -> str | None:
    """Cellule de la ligne dont l'entete contient ``fragment``.

    Returns:
        La valeur nettoyee ("" si vide), ou None si la colonne
        n'existe pas du tout dans cette ligne.
    """
    for header, value in row.items():
        if fragment in header.lower():
            return (value or "").strip()
    return None


def parse_benchmarks(content: str) -> list[dict[str, str]]:
    """Extrait les tableaux de metriques depuis BENCHMARKS.md.

    Args:
        content: Contenu brut de BENCHMARKS.md.

    Returns:
        Liste de dictionnaires representant les lignes de benchmark.
    """
    tables = _extract_tables(content)
    results: list[dict[str, str]] = []
    for table in tables:
        results.extend(table)
    return results


def parse_alerts(content: str) -> list[dict[str, str]]:
    """Extrait les alertes watchdog depuis PROGRESS.md.

    Args:
        content: Contenu brut de PROGRESS.md.

    Returns:
        Liste des alertes detectees avec leur contexte.
    """
    alerts: list[dict[str, str]] = []
    for keyword in _ALERT_KEYWORDS:
        pattern = re.compile(
            r"^.*" + re.escape(keyword) + r".*$",
            re.MULTILINE | re.IGNORECASE,
        )
        for match in pattern.finditer(content):
            line = match.group().strip()
            context_start = max(0, match.start() - 80)
            context_end = min(len(content), match.end() + 80)
            context = content[context_start:context_end].strip()
            alerts.append({
                "keyword": keyword,
                "line": line,
                "context": context,
                "position": match.start(),
            })
    return alerts


def _split_sections(content: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    matches = list(_SECTION_RE.finditer(content))
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start:end]
        sections.append((title, body))
    return sections


def _extract_tables(content: str) -> list[list[dict[str, str]]]:
    tables: list[list[dict[str, str]]] = []
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        header_match = _TABLE_ROW_RE.match(lines[i].strip())
        if header_match and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            headers = [h.strip() for h in header_match.group(1).split("|")]
            rows: list[dict[str, str]] = []
            j = i + 2
            while j < len(lines):
                row_match = _TABLE_ROW_RE.match(lines[j].strip())
                if not row_match:
                    break
                cells = [c.strip() for c in row_match.group(1).split("|")]
                row_dict = {}
                for idx, header in enumerate(headers):
                    if idx < len(cells):
                        row_dict[header] = cells[idx]
                if row_dict:
                    rows.append(row_dict)
                j += 1
            if rows:
                tables.append(rows)
            i = j
        else:
            i += 1
    return tables
