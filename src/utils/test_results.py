"""Gate de tests deterministe executee par la boucle DeBuilder.

La boucle ne croit pas l'agent sur parole (cahier des charges §5.3) :
apres chaque session Implement, elle relance elle-meme la commande de
test du projet et parse le resultat (JUnit XML quand c'est possible,
sinon code de sortie seul).

La commande de test est resolue dans cet ordre :
1. section « Commande de test » de ``TASK.md`` (a partir de la phase 5),
2. section « Commande de test » d'``AGENTS.md``,
3. variable d'environnement ``DEBUILDER_TEST_CMD``.

Aucune commande resolue : la gate est ignoree (avec avertissement).
"""

import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.core.state import read_state
from src.utils.task_parser import extract_test_command

# Duree maximale de la gate : une suite de tests qui ne termine pas ne
# doit pas figer la boucle autonome.
_DEFAULT_TIMEOUT_SECONDS = 1800


@dataclass
class TestResults:
    """Resultat parse d'une execution de la suite de tests.

    Attributes:
        passed: True si la suite est verte.
        tests: Nombre de tests executes (None si inconnu).
        failures: Nombre d'echecs (None si inconnu).
        errors: Nombre d'erreurs (None si inconnu).
        skipped: Nombre de tests ignores (None si inconnu).
        returncode: Code de sortie de la commande (None si elle n'a pas
            pu etre lancee).
        ran: False si la commande n'existe pas.
        detail: Dernieres lignes de sortie (motif pour PROGRESS.md).
    """

    passed: bool
    tests: int | None = None
    failures: int | None = None
    errors: int | None = None
    skipped: int | None = None
    returncode: int | None = None
    ran: bool = True
    detail: str = ""

    def to_dict(self) -> dict:
        """Metriques brutes pour le journal ITERATIONS.jsonl."""
        data: dict = {}
        for key in ("tests", "failures", "errors", "skipped"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data


def parse_junit(path: Path | None) -> TestResults | None:
    """Parse un fichier JUnit XML (pytest ``--junitxml``).

    Args:
        path: Chemin du fichier XML.

    Returns:
        Resultats du testsuite racine, ou None si le fichier est
        absent, vide ou malforme.
    """
    if path is None or not path.exists():
        return None
    try:
        import xml.etree.ElementTree as ET

        tree = ET.parse(str(path))
    except (ET.ParseError, OSError):
        return None

    root = tree.getroot()
    if root.tag == "testsuites":
        suites = [child for child in root if child.tag == "testsuite"]
    elif root.tag == "testsuite":
        suites = [root]
    else:
        return None
    if not suites:
        return None

    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    seen_tests = False
    for suite in suites:
        if suite.get("tests") is not None:
            seen_tests = True
        for key in totals:
            try:
                totals[key] += int(suite.get(key, 0) or 0)
            except ValueError:
                continue

    return TestResults(
        passed=(totals["failures"] == 0 and totals["errors"] == 0),
        tests=totals["tests"] if seen_tests else None,
        failures=totals["failures"],
        errors=totals["errors"],
        skipped=totals["skipped"],
    )


def resolve_test_command(target_dir: Path) -> str:
    """Resout la commande de test du projet cible.

    Args:
        target_dir: Repertoire du projet cible.

    Returns:
        Commande de test, ou chaine vide si aucune source ne la
        definit (la gate sera alors ignoree).
    """
    for filename in ("TASK.md", "AGENTS.md"):
        content = read_state(target_dir, filename)
        if not content:
            continue
        command = extract_test_command(content)
        if command:
            return command
    return os.environ.get("DEBUILDER_TEST_CMD", "").strip()


def run_test_gate(
    target_dir: Path,
    cmd: str,
    timeout: int | None = None,
) -> TestResults:
    """Execute la commande de test et parse son resultat.

    Pour une commande pytest, ``--junitxml=<fichier temporaire>`` est
    ajoute automatiquement (sauf s'il est deja present) : les compteurs
    (tests/failures/errors/skipped) sont alors lus du XML au lieu du
    seul code de sortie. Le fichier temporaire est supprime ensuite.

    Args:
        target_dir: Repertoire du projet cible (cwd de la commande).
        cmd: Commande shell a executer.
        timeout: Duree maximale en secondes (defaut : 1800, ou
            ``DEBUILDER_TEST_GATE_TIMEOUT``).

    Returns:
        Resultat structure de la gate.
    """
    if timeout is None:
        timeout = int(os.environ.get("DEBUILDER_TEST_GATE_TIMEOUT", str(_DEFAULT_TIMEOUT_SECONDS)))

    tokens = shlex.split(cmd)
    if not tokens:
        return TestResults(passed=False, ran=False, detail="commande vide")

    junit_path: Path | None = None
    command = tokens
    if _is_pytest_command(tokens) and not any(
        token.startswith("--junitxml") for token in tokens
    ):
        fd, tmp_name = tempfile.mkstemp(prefix="debuilder-junit-", suffix=".xml")
        os.close(fd)
        junit_path = Path(tmp_name)
        command = tokens + [f"--junitxml={junit_path}"]

    try:
        proc = subprocess.run(
            command,
            cwd=str(target_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return TestResults(
            passed=False,
            ran=False,
            detail=f"commande introuvable: {tokens[0]}",
        )
    except subprocess.TimeoutExpired:
        return TestResults(
            passed=False,
            detail=f"gate de tests expiree apres {timeout}s (processus tue)",
        )

    try:
        parsed = parse_junit(junit_path) if junit_path is not None else None
    finally:
        if junit_path is not None:
            try:
                junit_path.unlink()
            except OSError:
                pass

    detail = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if len(detail) > 2000:
        detail = detail[-2000:]

    if parsed is not None:
        parsed.returncode = proc.returncode
        parsed.detail = detail
        parsed.passed = proc.returncode == 0 and parsed.passed
        return parsed

    return TestResults(
        passed=proc.returncode == 0,
        returncode=proc.returncode,
        detail=detail,
    )


def _is_pytest_command(tokens: list[str]) -> bool:
    """True si la commande lance pytest (pour injecter --junitxml)."""
    if not tokens:
        return False
    executable = Path(tokens[0]).name
    if executable.startswith("pytest"):
        return True
    return (
        "python" in executable
        and len(tokens) >= 3
        and tokens[1] == "-m"
        and tokens[2].startswith("pytest")
    )
