"""Fixtures globales de la suite de tests.

Isole l'etat persistant de DeBuilder (``DEBUILDER_STATE_DIR``, ou
``~/.debuilder`` par defaut) dans un repertoire temporaire propre a
chaque test : sans cela, le circuit breaker et le suivi de session
ecriraient dans le vrai repertoire utilisateur pendant les tests.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_debuilder_state(monkeypatch, tmp_path):
    monkeypatch.setenv("DEBUILDER_STATE_DIR", str(tmp_path / "debuilder-state"))
