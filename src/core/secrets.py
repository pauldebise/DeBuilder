"""Gestion securisee des secrets (cles API).

Les secrets sont exclusivement stockes comme variables
d'environnement ephemeres. Ils ne sont jamais ecrits sur le disque.
La sanitization est assuree dans les logs et commits.
"""

import os


def inject_secrets(secrets: dict[str, str]) -> None:
    """Injecte les secrets comme variables d'environnement ephemeres.

    Args:
        secrets: Dictionnaire cle/valeur des secrets a injecter.
    """
    ...


def get_secret(key: str) -> str | None:
    """Recupere un secret depuis l'environnement.

    Args:
        key: Nom de la variable d'environnement.

    Returns:
        La valeur du secret ou None si absent.
    """
    ...


def sanitize_text(text: str, secrets: dict[str, str] | None = None) -> str:
    """Masque les secrets presents dans un texte (logs, commits).

    Args:
        text: Texte potentiellement contenant des secrets.
        secrets: Dictionnaire optionnel de secrets a masquer.
                 Si None, utilise les variables d'environnement.

    Returns:
        Texte avec les secrets masques.
    """
    ...
