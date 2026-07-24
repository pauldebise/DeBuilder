"""Resume en langage humain des logs bruts d'OpenCode.

Reutilise le fournisseur et la cle deja configures pour l'agent
(``DEBUILDER_MODEL`` + la cle du fournisseur associe, injectee comme
variable d'environnement au demarrage de la session) afin de
transformer les logs bruts en 2-4 phrases comprehensibles pour un
utilisateur qui supervise sans lire le log brut.

Si aucune cle reconnue n'est disponible, ou si l'appel echoue (reseau
coupe sur un pod isole, quota, etc.), un resume heuristique base sur
des motifs reconnus dans le log prend le relais : gratuit et
fonctionnel hors ligne.
"""

import hashlib
import os
import re

import httpx

from src.core.secrets import sanitize_text

_TIMEOUT = 12.0
_MAX_LOG_CHARS = 8000

_SYSTEM_PROMPT = (
    "Tu observes les logs bruts d'un agent de developpement IA autonome. "
    "Resume en 2 a 4 phrases simples, en francais, ce que l'agent est en "
    "train de faire ou vient de faire (derniere action, resultat, erreur "
    "eventuelle). Pas de jargon technique inutile : le lecteur supervise "
    "l'agent sans lire le log brut. Ne mentionne jamais de cle API, token "
    "ou secret meme si tu en vois un fragment."
)

# Fournisseurs opencode connus (cf. src/gui/config.py) : la cle et le
# modele sont deja valides au demarrage de la session, on les reutilise
# tels quels plutot que de deviner un identifiant de modele "mini" qui
# pourrait ne pas exister chez le fournisseur.
_PROVIDERS = {
    "deepseek": {
        "env_key": "DEEPSEEK_API_KEY",
        "url": "https://api.deepseek.com/chat/completions",
        "kind": "openai",
    },
    "openai": {
        "env_key": "OPENAI_API_KEY",
        "url": "https://api.openai.com/v1/chat/completions",
        "kind": "openai",
    },
    "anthropic": {
        "env_key": "ANTHROPIC_API_KEY",
        "url": "https://api.anthropic.com/v1/messages",
        "kind": "anthropic",
    },
}

# cache_key -> (hash du dernier log resume, resume). Evite de rappeler
# le LLM a chaque tick de polling si le log n'a pas change.
_cache: dict[str, tuple[str, str]] = {}


def summarize_logs(raw_log: str, cache_key: str) -> str:
    """Resume un extrait de log en langage humain.

    Args:
        raw_log: Extrait brut du log (ex: les 200 dernieres lignes).
        cache_key: Cle de cache (ex: chemin du repertoire cible), pour
            eviter un appel LLM redondant si le contenu n'a pas change.

    Returns:
        Resume en langage humain (via LLM ou heuristique de repli).
    """
    if not raw_log.strip():
        return "*En attente de la premiere action de l'agent...*"

    log_hash = hashlib.sha256(raw_log.encode("utf-8", errors="ignore")).hexdigest()
    cached = _cache.get(cache_key)
    if cached and cached[0] == log_hash:
        return cached[1]

    summary = _summarize_with_llm(raw_log) or _summarize_heuristic(raw_log)
    _cache[cache_key] = (log_hash, summary)
    return summary


def _summarize_with_llm(raw_log: str) -> str | None:
    provider = _active_provider()
    if not provider:
        return None

    clean_log = sanitize_text(raw_log)[-_MAX_LOG_CHARS:]

    try:
        if provider["kind"] == "anthropic":
            return _call_anthropic(provider, clean_log)
        return _call_openai_compatible(provider, clean_log)
    except (httpx.HTTPError, KeyError, ValueError, IndexError):
        return None


def _active_provider() -> dict | None:
    model = os.environ.get("DEBUILDER_MODEL", "")
    if "/" not in model:
        return None
    prefix, model_name = model.split("/", 1)
    entry = _PROVIDERS.get(prefix)
    if not entry:
        return None
    api_key = os.environ.get(entry["env_key"])
    if not api_key:
        return None
    return {**entry, "api_key": api_key, "model": model_name}


def _call_openai_compatible(provider: dict, clean_log: str) -> str | None:
    resp = httpx.post(
        provider["url"],
        headers={
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
        },
        json={
            "model": provider["model"],
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": clean_log},
            ],
            "max_tokens": 220,
            "temperature": 0.3,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _call_anthropic(provider: dict, clean_log: str) -> str | None:
    resp = httpx.post(
        provider["url"],
        headers={
            "x-api-key": provider["api_key"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": provider["model"],
            "max_tokens": 220,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": clean_log}],
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"].strip()


_ITERATION_RE = re.compile(r"^=== Iteration (.+?) ===$", re.MULTILINE)
_ERROR_RE = re.compile(r"error|erreur|failed|echec", re.IGNORECASE)


def _summarize_heuristic(raw_log: str) -> str:
    """Resume sans appel LLM, par extraction de motifs (gratuit, hors ligne)."""
    iterations = _ITERATION_RE.findall(raw_log)
    lines = [line for line in raw_log.splitlines() if line.strip()]
    recent_lines = lines[-8:]

    parts = []
    if iterations:
        parts.append(f"Iteration en cours depuis {iterations[-1]}.")
    if any(_ERROR_RE.search(line) for line in recent_lines):
        parts.append(
            "Des erreurs sont visibles dans la sortie recente : "
            "consultez les logs bruts pour le detail."
        )
    else:
        parts.append("Aucune erreur detectee dans la sortie recente.")
    if recent_lines:
        parts.append(f"Derniere sortie : « {recent_lines[-1].strip()[:160]} »")

    return " ".join(parts)
