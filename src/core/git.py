"""Operations Git sur le depot du projet cible.

Toutes les operations Git (commit, push, rollback) sont effectuees
exclusivement dans le repertoire du projet cible, jamais dans
le depot DeBuilder.
"""

import os
import subprocess
from pathlib import Path

# Fichiers operationnels de DeBuilder : jamais des livrables du projet
# cible, ne doivent donc jamais etre commites (DONE commite ferait
# demarrer toute future session avec le kill-switch actif).
_DEBUILDER_IGNORE_PATTERNS = [
    "DONE",
    "BARRIER_*",
    "*.lock",
    "OPENCODE_LOG.txt",
    "ITERATIONS.jsonl",
]


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


def push(repo_dir: Path) -> tuple[bool, str]:
    """Push les commits sur le remote.

    Definit systematiquement l'upstream (-u) : sur un depot flambant
    neuf (init_repo + premier commit), la branche locale n'a encore
    aucun suivi distant et un `git push` nu echoue silencieusement
    avec "no upstream branch".

    Args:
        repo_dir: Chemin du depot Git cible.

    Returns:
        Tuple (succes, detail). detail est vide en cas de succes, ou
        contient la sortie git en cas d'echec (auth, remote injoignable,
        etc.) pour permettre un diagnostic sans acces au pod.
    """
    branch_result = _run(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch_result.stdout.strip()
    if not branch or branch == "HEAD":
        return False, "impossible de determiner la branche courante"
    result = _run(repo_dir, "push", "-u", "origin", branch)
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr.strip() or result.stdout.strip())


def stage_and_commit_all(repo_dir: Path, message: str) -> tuple[bool, str]:
    """Stage tous les changements, commit et push.

    Utilise par l'agent apres chaque iteration pour garantir
    la persistance du travail meme en cas d'echec.

    Le push est tente meme quand il n'y a rien de nouveau a commiter
    ici : l'agent (via OpenCode) commite frequemment lui-meme pendant
    l'iteration, ce qui ne laisse alors plus rien a commiter pour ce
    wrapper. Sans ce push inconditionnel, ces commits resteraient
    indefiniment locaux et ne remonteraient jamais sur GitHub.

    Args:
        repo_dir: Chemin du depot Git cible.
        message: Message de commit.

    Returns:
        Tuple (succes, detail). detail est vide en cas de succes, ou
        contient la raison de l'echec (add/commit/push) sinon.
    """
    add_result = _run(repo_dir, "add", "-A")
    if add_result.returncode != 0:
        return False, add_result.stderr.strip()

    diff_result = _run(repo_dir, "diff", "--cached", "--quiet")
    if diff_result.returncode != 0:
        commit_result = _run(repo_dir, "commit", "-m", message)
        if commit_result.returncode != 0:
            return False, commit_result.stderr.strip()

    remote_result = _run(repo_dir, "remote")
    if remote_result.stdout.strip():
        if os.environ.get("DEBUILDER_GH_TOKEN", "").strip():
            return push(repo_dir)
        return True, ""
    return True, ""


def status_files(repo_dir: Path) -> list[str]:
    """Liste les fichiers du depot cible avec des changements en cours.

    Equivalent de `git status --porcelain` limite aux chemins.

    Args:
        repo_dir: Chemin du depot Git cible.

    Returns:
        Chemins des fichiers modifies/ajoutes/supprimes. Liste vide si
        le depot est propre ou si le repertoire n'est pas un depot Git.
    """
    result = _run(repo_dir, "status", "--porcelain")
    if result.returncode != 0:
        return []
    files: list[str] = []
    for line in result.stdout.splitlines():
        path = line[3:].strip().strip('"')
        if path:
            files.append(path)
    return files


def recent_changes(repo_dir: Path, commits: int = 5) -> str:
    """Contexte des commits recents (``git log --stat``).

    Source factuelle pour la synthese LLM de l'entree PROGRESS.md (cdc
    §4.3) : les petits commits de sous-tache poses par la session
    Implement decrivent precisement le travail de l'iteration.

    Args:
        repo_dir: Chemin du depot Git cible.
        commits: Nombre de commits a inclure.

    Returns:
        Sortie de ``git log`` (une ligne par commit + stat), ou chaine
        vide si le depot n'a pas de commit.
    """
    result = _run(
        repo_dir,
        "log",
        f"-n{commits}",
        "--oneline",
        "--stat",
        "--no-decorate",
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def diff_size(repo_dir: Path, base: str = "HEAD") -> dict:
    """Taille du diff (lignes ajoutees/retirees) depuis une reference.

    Exploitee par le journal ITERATIONS.jsonl (cdc §5.2) : mesure le
    travail produit par une iteration, y compris les commits poses par
    l'agent pendant la session (``git diff <base>`` couvre tout ce qui
    separe la reference de l'etat courant du working tree).

    Args:
        repo_dir: Chemin du depot Git cible.
        base: Reference de depart (SHA ou ref) ; chaine vide si le
            depot n'a pas encore de commit (compteurs a zero).

    Returns:
        Dictionnaire ``{"added": int, "removed": int}`` (lignes).
    """
    if not base:
        return {"added": 0, "removed": 0}
    result = _run(repo_dir, "diff", "--numstat", base)
    if result.returncode != 0:
        return {"added": 0, "removed": 0}
    added = 0
    removed = 0
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            added += int(parts[0]) if parts[0] != "-" else 0
            removed += int(parts[1]) if parts[1] != "-" else 0
        except ValueError:
            continue
    return {"added": added, "removed": removed}


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


def head_commit(repo_dir: Path) -> str:
    """Retourne le hash du commit courant.

    Args:
        repo_dir: Chemin du depot Git cible.

    Returns:
        SHA de HEAD, ou chaine vide si le depot n'a pas encore de
        commit (ex: depot venant d'etre initialise).
    """
    result = _run(repo_dir, "rev-parse", "HEAD")
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def tag_iteration(repo_dir: Path, tag_name: str) -> bool:
    """Pose un tag leger sur le commit courant (HEAD).

    Args:
        repo_dir: Chemin du depot Git cible.
        tag_name: Nom du tag (ex: ``debuilder/iter-0012``).

    Returns:
        True si le tag a ete pose, False sinon.
    """
    result = _run(repo_dir, "tag", tag_name)
    return result.returncode == 0


def rollback_to_tag(repo_dir: Path, tag_name: str) -> bool:
    """Reinitialise le depot sur le tag donne (git reset --hard <tag>).

    Args:
        repo_dir: Chemin du depot Git cible.
        tag_name: Nom du tag vers lequel revenir.

    Returns:
        True si le rollback a reussi, False sinon.
    """
    result = _run(repo_dir, "reset", "--hard", tag_name)
    return result.returncode == 0


def list_iteration_tags(repo_dir: Path) -> list[str]:
    """Liste les tags d'iteration DeBuilder, du plus recent au plus ancien.

    Args:
        repo_dir: Chemin du depot Git cible.

    Returns:
        Noms des tags ``debuilder/iter-*``.
    """
    result = _run(repo_dir, "tag", "--sort=-creatordate", "--list", "debuilder/iter-*")
    if result.returncode != 0:
        return []
    return [tag for tag in result.stdout.splitlines() if tag.strip()]


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
