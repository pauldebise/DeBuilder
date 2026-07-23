"""Operations Git sur le depot du projet cible.

Toutes les operations Git (commit, push, rollback) sont effectuees
exclusivement dans le repertoire du projet cible, jamais dans
le depot DeBuilder.
"""

import subprocess
from pathlib import Path

# Fichiers operationnels de DeBuilder : jamais des livrables du projet
# cible, ne doivent donc jamais etre commites (DONE commite ferait
# demarrer toute future session avec le kill-switch actif).
_DEBUILDER_IGNORE_PATTERNS = ["DONE", "BARRIER_*", "*.lock", "OPENCODE_LOG.txt"]


def _run(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
    )


def commit_all(repo_dir: Path, message: str) -> bool:
    """Commit tous les changements dans le depot cible.

    Args:
        repo_dir: Chemin du depot Git cible.
        message: Message de commit.

    Returns:
        True si le commit a reussi ou s'il n'y a rien a commiter.
    """
    add_result = _run(repo_dir, "add", "-A")
    if add_result.returncode != 0:
        return False

    diff_result = _run(repo_dir, "diff", "--cached", "--quiet")
    if diff_result.returncode == 0:
        return True

    commit_result = _run(repo_dir, "commit", "-m", message)
    return commit_result.returncode == 0


def push(repo_dir: Path) -> bool:
    """Push les commits sur le remote.

    Args:
        repo_dir: Chemin du depot Git cible.

    Returns:
        True si le push a reussi, False sinon.
    """
    result = _run(repo_dir, "push")
    return result.returncode == 0


def stage_and_commit_all(repo_dir: Path, message: str) -> bool:
    """Stage tous les changements, commit et push.

    Utilise par l'agent apres chaque iteration pour garantir
    la persistance du travail meme en cas d'echec.

    Args:
        repo_dir: Chemin du depot Git cible.
        message: Message de commit.

    Returns:
        True si l'operation complete a reussi.
    """
    add_result = _run(repo_dir, "add", "-A")
    if add_result.returncode != 0:
        return False

    diff_result = _run(repo_dir, "diff", "--cached", "--quiet")
    if diff_result.returncode == 0:
        return True

    commit_result = _run(repo_dir, "commit", "-m", message)
    if commit_result.returncode != 0:
        return False

    remote_result = _run(repo_dir, "remote")
    if remote_result.stdout.strip():
        push_result = _run(repo_dir, "push")
        return push_result.returncode == 0
    return True


def rollback_last(repo_dir: Path) -> bool:
    """Annule le dernier commit (git reset --hard HEAD~1).

    Preserve la stabilite de la session et de DeBuilder.

    Args:
        repo_dir: Chemin du depot Git cible.

    Returns:
        True si le rollback a reussi, False sinon.
    """
    result = _run(repo_dir, "reset", "--hard", "HEAD~1")
    return result.returncode == 0


def clone_repo(url: str, target_dir: Path) -> bool:
    """Clone un depot Git dans le repertoire cible.

    Args:
        url: URL du depot a cloner.
        target_dir: Chemin ou cloner le depot.

    Returns:
        True si le clone a reussi, False sinon.
    """
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", url, str(target_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def init_repo(target_dir: Path) -> bool:
    """Initialise un nouveau depot Git dans le repertoire cible.

    Args:
        target_dir: Chemin ou initialiser le depot.

    Returns:
        True si l'initialisation a reussi.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    result = _run(target_dir, "init")
    return result.returncode == 0


def ensure_gitignore(repo_dir: Path) -> None:
    """S'assure que les fichiers operationnels de DeBuilder sont ignores.

    N'ecrase jamais un .gitignore existant (cas d'un depot clone) :
    ajoute uniquement les entrees manquantes.

    Args:
        repo_dir: Chemin du depot cible.
    """
    gitignore_path = repo_dir / ".gitignore"
    existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""

    missing = [p for p in _DEBUILDER_IGNORE_PATTERNS if p not in existing]
    if not missing:
        return

    if existing and not existing.endswith("\n"):
        existing += "\n"

    addition = "\n# DeBuilder : fichiers operationnels, jamais versionnes\n"
    addition += "\n".join(missing) + "\n"

    gitignore_path.write_text(existing + addition, encoding="utf-8")


def configure_git(
    repo_dir: Path,
    user_name: str = "DeBuilder Agent",
    user_email: str = "agent@debuilder.local",
    token: str = "",
    remote_url: str = "",
) -> None:
    """Configure les identifiants Git et le remote avec token.

    Args:
        repo_dir: Chemin du depot cible.
        user_name: Nom de l'auteur des commits.
        user_email: Email de l'auteur.
        token: Token GitHub pour authentification push.
        remote_url: URL du remote a configurer si depot vierge.
    """
    _run(repo_dir, "config", "user.name", user_name)
    _run(repo_dir, "config", "user.email", user_email)

    if remote_url and token:
        auth_url = remote_url.replace("https://", f"https://{token}@")
        remotes = _run(repo_dir, "remote")
        if "origin" in remotes.stdout:
            _run(repo_dir, "remote", "set-url", "origin", auth_url)
        else:
            _run(repo_dir, "remote", "add", "origin", auth_url)
