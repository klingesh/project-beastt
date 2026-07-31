"""Clone and update repositories locally.

Private repositories need the personal access token, but a token embedded in a
remote URL would be written into `.git/config` and stay on disk. So the token is
supplied only for the duration of the clone and the remote is then rewritten to
a plain URL.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from .code import workspace

_TIMEOUT = 300


def _git(args: List[str], cwd: Optional[Path] = None) -> Tuple[int, str]:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=_TIMEOUT, shell=False,
        )
    except FileNotFoundError:
        return 1, "Git doesn't seem to be installed. Get it from https://git-scm.com/downloads"
    except subprocess.TimeoutExpired:
        return 1, "That took too long and I stopped it."
    except Exception as exc:
        return 1, f"{exc.__class__.__name__}: {exc}"
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()


def _scrub(text: str, token: str) -> str:
    """Never echo the token back, even inside git's error messages."""
    return text.replace(token, "***") if token else text


def local_path(repo: str) -> Path:
    name = repo.rstrip("/").split("/")[-1]
    name = re.sub(r"\.git$", "", name)
    return workspace() / re.sub(r"[^A-Za-z0-9._-]", "_", name)


def clone(owner: str, repo: str, token: str = "", branch: str = "") -> Tuple[bool, str, Optional[Path]]:
    """Clone a repository into the workspace. Returns (ok, message, path)."""
    if "/" in repo:
        owner, repo = repo.split("/", 1)
    repo = re.sub(r"\.git$", "", repo.strip())
    target = local_path(repo)

    if target.exists():
        return True, f"{repo} is already here at {target} -- pulling instead.", target

    plain_url = f"https://github.com/{owner}/{repo}.git"
    auth_url = (
        f"https://x-access-token:{token}@github.com/{owner}/{repo}.git" if token else plain_url
    )

    args = ["clone", "--depth", "1"]
    if branch:
        args += ["--branch", branch]
    args += [auth_url, str(target)]

    code, output = _git(args)
    if code != 0:
        return False, _scrub(output, token)[:400], None

    # Replace the tokenised remote so no credential is left in .git/config.
    if token:
        _git(["remote", "set-url", "origin", plain_url], cwd=target)

    files = sorted(p.name for p in target.iterdir() if not p.name.startswith("."))
    listing = ", ".join(files[:10]) + ("..." if len(files) > 10 else "")
    return True, f"Cloned {owner}/{repo} to {target}\nTop level: {listing}", target


def pull(repo: str, token: str = "") -> Tuple[bool, str]:
    """Update an already-cloned repository."""
    target = local_path(repo)
    if not (target / ".git").exists():
        return False, (
            f"I don't have {repo} locally yet. Say \"clone {repo}\" first."
        )

    code, output = _git(["pull", "--ff-only"], cwd=target)
    if code != 0:
        # A tokenised pull for private repos, without persisting the credential.
        if token:
            url_code, url_out = _git(["remote", "get-url", "origin"], cwd=target)
            match = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+)", url_out or "")
            if url_code == 0 and match:
                owner, name = match.group(1), re.sub(r"\.git$", "", match.group(2))
                auth = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
                code, output = _git(["pull", "--ff-only", auth], cwd=target)
        if code != 0:
            return False, _scrub(output, token)[:400]
    return True, f"{target.name} is up to date.\n{_scrub(output, token)[:300]}"


def status(repo: str) -> Tuple[bool, str]:
    target = local_path(repo)
    if not (target / ".git").exists():
        return False, f"I don't have {repo} locally."
    code, output = _git(["status", "--short", "--branch"], cwd=target)
    return code == 0, output[:600] or "(clean)"
