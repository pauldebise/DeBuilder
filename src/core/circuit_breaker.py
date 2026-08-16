"""Circuit breaker API (cahier des charges §7).

Empeche la boucle autonome de retenter indefiniment sur une API morte
(cle epuisee, quota depasse, provider injoignable) : apres K echecs
API consecutifs, le circuit s'ouvre et la boucle se met en pause
automatique de duree croissante avant la prochaine iteration, avec
bascule optionnelle sur un modele de secours
(``DEBUILDER_MODEL_FALLBACK``).

L'etat est persiste dans ``$DEBUILDER_STATE_DIR/circuit_breaker.json``
(defaut ``~/.debuilder``) : un redemarrage du pod ne remet pas le
compteur a zero.
"""

import json
import os
import time
from pathlib import Path

import httpx

from src.core.filelock import file_lock
from src.core.session import _state_dir

_MAX_PAUSE_MULTIPLIER = 16.0


class CircuitBreaker:
    """Compteur d'echecs API et etat du circuit (ouvert/ferme)."""

    def __init__(self, state_dir: Path | None = None):
        self.state_dir = state_dir or _state_dir()
        self.path = self.state_dir / "circuit_breaker.json"
        self._data = self._defaults()
        self._load()

    @property
    def max_failures(self) -> int:
        """Nombre d'echecs API consecutifs avant ouverture (defaut 3)."""
        return int(os.environ.get("DEBUILDER_CB_MAX_FAILURES", "3"))

    @property
    def base_pause(self) -> float:
        """Duree de pause initiale en secondes (defaut 600)."""
        return float(os.environ.get("DEBUILDER_CB_PAUSE_SECONDS", "600"))

    def _defaults(self) -> dict:
        return {
            "api_failures": 0,
            "trip_count": 0,
            "tripped": False,
            "tripped_at": None,
            "pause_until": None,
            "pause_seconds": self.base_pause,
            "using_fallback": False,
            "last_failure_type": None,
            "updated_at": None,
        }

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with file_lock(self.path):
                raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(raw, dict):
            self._data.update({k: raw[k] for k in self._data if k in raw})

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._data["updated_at"] = time.time()
        with file_lock(self.path):
            self.path.write_text(json.dumps(self._data), encoding="utf-8")

    def record_success(self) -> None:
        """Un succes remet le compteur et le circuit a zero."""
        data = self._defaults()
        data["updated_at"] = time.time()
        self._data = data
        self._save()

    def record_failure(self, failure_type: str) -> None:
        """Enregistre un echec d'iteration.

        Seuls les echecs de type ``api`` incrementent le compteur
        d'echecs consecutifs ; tout autre resultat (succes ou echec
        d'une autre nature) rompt la serie.

        Args:
            failure_type: ``api``, ``timeout``, ``empty``, ``error``,
                ``tests``, ``exception``...
        """
        if failure_type == "api":
            self._data["api_failures"] += 1
            if self._data["api_failures"] >= self.max_failures:
                self._trip()
        else:
            self._data["api_failures"] = 0
        self._data["last_failure_type"] = failure_type
        self._save()

    def _trip(self) -> None:
        now = time.time()
        self._data["trip_count"] += 1
        self._data["tripped"] = True
        self._data["tripped_at"] = now
        pause = float(self._data["pause_seconds"])
        self._data["pause_until"] = now + pause
        # Duree croissante : doublee a chaque ouverture, plafonnee.
        self._data["pause_seconds"] = min(
            pause * 2, self.base_pause * _MAX_PAUSE_MULTIPLIER
        )
        self._data["using_fallback"] = bool(
            os.environ.get("DEBUILDER_MODEL_FALLBACK", "").strip()
        )
        self._data["api_failures"] = 0
        _notify_webhook(
            {"event": "circuit_breaker_tripped", "breaker": self.to_dict()}
        )

    def should_pause(self) -> bool:
        """True si la boucle doit marquer une pause avant la prochaine iteration."""
        return self._data["tripped"] and self.pause_remaining() > 0

    def pause_remaining(self) -> float:
        """Secondes restantes de pause (0 si aucune)."""
        until = self._data.get("pause_until")
        if not until:
            return 0.0
        return max(0.0, float(until) - time.time())

    def use_fallback(self) -> bool:
        """True si l'iteration doit utiliser le modele de secours."""
        return self._data["using_fallback"] and bool(
            os.environ.get("DEBUILDER_MODEL_FALLBACK", "").strip()
        )

    def to_dict(self) -> dict:
        """Etat lisible pour le tableau de bord et les webhooks."""
        data = dict(self._data)
        data["pause_remaining_seconds"] = round(self.pause_remaining(), 1)
        data["max_failures"] = self.max_failures
        data["fallback_model"] = os.environ.get("DEBUILDER_MODEL_FALLBACK", "")
        return data


def load_breaker_state(state_dir: Path | None = None) -> dict:
    """Etat du breaker pour le tableau de bord (lecture seule)."""
    return CircuitBreaker(state_dir=state_dir).to_dict()


def _notify_webhook(payload: dict) -> None:
    """POST JSON optionnel vers ``DEBUILDER_WEBHOOK_URL`` (fire-and-forget).

    N'echoue jamais : une alerte ne doit pas casser la boucle autonome.
    """
    url = os.environ.get("DEBUILDER_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        httpx.post(url, json=payload, timeout=5.0)
    except Exception:
        pass
