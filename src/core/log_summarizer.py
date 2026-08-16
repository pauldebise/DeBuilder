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
from dataclasses import dataclass

import httpx

from src.core.secrets import sanitize_text

_TIMEOUT = 12.0
_MAX_LOG_CHARS = 8000

_SYSTEM_PROMPT = (
    "Tu observes les logs bruts d'un agent de developpement IA autonome. "
    "Resume en exactement 2 phrases courtes et simples, en francais, ce que "
    "l'agent est en train de faire ou vient de faire (derniere action, "
    "resultat, erreur eventuelle). Pas de jargon technique inutile : le "
    "lecteur supervise l'agent sans lire le log brut. Ne mentionne jamais "
    "de cle API, token ou secret meme si tu en vois un fragment."
)

_PROGRESS_SYSTEM_PROMPT = (
    "Tu rediges l'entree de journal d'une iteration d'un agent de "
    "developpement autonome, en Markdown strict, en francais sans accents "
    "ni caracteres speciaux. Quatre puces exactement :\n"
    "- **Action realisee** : ...\n"
    "- **Resultat** : ...\n"
    "- **Problemes rencontres** : ...\n"
    "- **Solutions envisagees** : ...\n"
    "Base-toi uniquement sur le contexte fourni (commits git recents et fin "
    "du transcript). Reste factuel et bref : une phrase par puce. Ne "
    "mentionne jamais de cle API, token ou secret."
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

@dataclass(frozen=True)
class LogSummary:
    """Resultat d'un resume : texte affiche + avertissement optionnel.

    ``warning`` n'est renseigne que si le resume LLM a ete tente et a
    echoue (provider configure mais appel en erreur) : l'absence de
    provider configure est un etat normal, pas une erreur, donc ne
    produit pas d'avertissement.
    """

    text: str
    warning: str | None = None


# cache_key -> (hash du dernier log resume, resume). Evite de rappeler
# le LLM a chaque tick de polling si le log n'a pas change.
_cache: dict[str, tuple[str, LogSummary]] = {}


def summarize_logs(raw_log: str, cache_key: str) -> LogSummary:
    """Resume un extrait de log en langage humain.

    Args:
        raw_log: Extrait brut du log (ex: les 200 dernieres lignes).
        cache_key: Cle de cache (ex: chemin du repertoire cible), pour
            eviter un appel LLM redondant si le contenu n'a pas change.

    Returns:
        Resume en langage humain (via LLM ou heuristique de repli),
        avec un avertissement si le repli heuristique fait suite a un
        echec de l'appel LLM.
    """
    if not raw_log.strip():
        return LogSummary("*En attente de la premiere action de l'agent...*")

    log_hash = hashlib.sha256(raw_log.encode("utf-8", errors="ignore")).hexdigest()
    cached = _cache.get(cache_key)
    if cached and cached[0] == log_hash:
        return cached[1]

    text, llm_error = _summarize_with_llm(raw_log)
    if text is None:
        warning = (
            f"Resume LLM indisponible ({llm_error}) : resume heuristique utilise a la place."
            if llm_error
            else None
        )
        summary = LogSummary(_summarize_heuristic(raw_log), warning)
    else:
        summary = LogSummary(text)

    _cache[cache_key] = (log_hash, summary)
    return summary


def _summarize_with_llm(raw_log: str) -> tuple[str | None, str | None]:
    """Tente un resume via LLM.

    Returns:
        Tuple ``(texte, raison_echec)``. Si aucun provider n'est
        configure, ``(None, None)`` (etat normal, pas une erreur). Si
        l'appel echoue, ``(None, raison)`` avec une description courte
        exploitable pour un avertissement utilisateur.
    """
    provider = _active_provider()
    if not provider:
        return None, None

    clean_log = sanitize_text(raw_log)[-_MAX_LOG_CHARS:]

    try:
        text = _chat(provider, _SYSTEM_PROMPT, clean_log)
    except httpx.HTTPStatusError as exc:
        return None, f"erreur HTTP {exc.response.status_code}"
    except httpx.HTTPError as exc:
        return None, type(exc).__name__
    except (KeyError, ValueError, IndexError):
        return None, "reponse du fournisseur illisible"

    # Certains modeles (notamment les modeles "raisonneurs") peuvent
    # consommer tout le budget de tokens en reflexion interne et
    # renvoyer un champ content vide : sans ce garde-fou, un resume
    # vide serait mis en cache et affiche tel quel au lieu de basculer
    # sur le repli heuristique.
    if not text or not text.strip():
        return None, "reponse vide du fournisseur"
    return text, None


def synthesize_progress_entry(git_context: str, transcript_tail: str) -> str | None:
    """Synthese LLM d'une entree PROGRESS.md structuree (cdc §4.3).

    Repli de la boucle quand la session Implement n'a pas mis a jour
    PROGRESS.md elle-meme : l'entree est generee par le LLM a partir
    des commits git recents et de la fin du transcript — jamais a
    partir du stdout brut injecte tel quel.

    Args:
        git_context: Sortie de ``git log --stat`` (commits recents).
        transcript_tail: Fin du transcript d'OpenCode (deja sanitisée
            a la capture).

    Returns:
        L'entree Markdown structuree, ou None si aucun provider n'est
        configure ou si l'appel echoue (le repli heuristique prend
        alors le relais).
    """
    provider = _active_provider()
    if not provider:
        return None

    user_content = sanitize_text(
        "## Commits recents de l'iteration\n\n"
        + (git_context or "(aucun commit)")
        + "\n\n## Fin du transcript\n\n"
        + (transcript_tail or "(sortie vide)")
    )[-(2 * _MAX_LOG_CHARS):]

    try:
        text = _chat(provider, _PROGRESS_SYSTEM_PROMPT, user_content)
    except (httpx.HTTPError, KeyError, ValueError, IndexError):
        return None
    if not text or not text.strip():
        return None
    return text.strip()


def _chat(provider: dict, system_prompt: str, user_content: str) -> str:
    """Un tour de chat avec le fournisseur de l'agent (openai ou anthropic)."""
    if provider["kind"] == "anthropic":
        return _call_anthropic(provider, user_content, system_prompt)
    return _call_openai_compatible(provider, user_content, system_prompt)


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


def _call_openai_compatible(
    provider: dict, clean_log: str, system_prompt: str = _SYSTEM_PROMPT
) -> str | None:
    resp = httpx.post(
        provider["url"],
        headers={
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
        },
        json={
            "model": provider["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": clean_log},
            ],
            "temperature": 0.3,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _call_anthropic(
    provider: dict, clean_log: str, system_prompt: str = _SYSTEM_PROMPT
) -> str | None:
    resp = httpx.post(
        provider["url"],
        headers={
            "x-api-key": provider["api_key"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": provider["model"],
            # L'API Anthropic exige max_tokens (pas de valeur "illimitee"
            # possible) ; une valeur large ici n'est qu'un garde-fou, le
            # prompt systeme impose deja un resume tres court.
            "max_tokens": 1024,
            "system": system_prompt,
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
