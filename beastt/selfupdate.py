"""Keep the assistant's own code up to date.

Most installations are a downloaded copy rather than a git checkout, so updating
means fetching the current file list from GitHub and rewriting changed files --
the same thing update.py does, available from inside a conversation.

Safety notes:
  * A backup of every replaced file is kept, so a bad update can be rolled back.
  * Personal data (.env, beastt_memory/, beastt_output/, beastt_workspace/) is
    never touched.
  * Code changes only take effect on restart; the assistant says so rather than
    pretending otherwise.
"""

from __future__ import annotations

import json
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .paths import data_dir, project_root

OWNER = "klingesh"
REPO = "project-beastt"
BRANCH = "feat/beastt-ai-companion"

_UA = {"User-Agent": "beastt-selfupdate/1.0", "Accept": "*/*"}
_SKIP_EXACT = {".env"}
_SKIP_PREFIX = ("beastt_memory/", "beastt_output/", "beastt_workspace/", ".git/", "jarvis/")
#: Python, config, and the web interface's assets. The web types matter: without
#: them an update leaves the chat interface with no page to serve.
_KEEP_SUFFIX = (
    ".py", ".txt", ".md", ".example", ".gitignore",
    ".html", ".css", ".js", ".json", ".svg", ".ico",
)

VERSION_FILE = "version.json"


def _get(url: str, as_json: bool = False):
    # Bypass the raw.githubusercontent CDN cache, which otherwise serves a stale
    # copy for a few minutes after a push.
    separator = "&" if "?" in url else "?"
    url = f"{url}{separator}_={int(time.time() * 1000)}"
    request = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
    return json.loads(payload) if as_json else payload


def _state() -> Dict:
    path = data_dir() / VERSION_FILE
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: Dict) -> None:
    try:
        (data_dir() / VERSION_FILE).write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


# --- checking ---------------------------------------------------------------
def latest_commit() -> Optional[Dict]:
    """The newest commit on the tracked branch."""
    url = (
        f"https://api.github.com/repos/{OWNER}/{REPO}/commits"
        f"?sha={urllib.parse.quote(BRANCH)}&per_page=1"
    )
    try:
        data = _get(url, as_json=True)
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    entry = data[0]
    return {
        "sha": entry.get("sha", "")[:40],
        "message": (entry.get("commit", {}).get("message") or "").splitlines()[0][:120],
        "when": entry.get("commit", {}).get("author", {}).get("date", ""),
    }


def check() -> str:
    """Report whether an update is available."""
    commit = latest_commit()
    if commit is None:
        return "I couldn't reach GitHub to check for updates."
    known = _state().get("sha")
    if known and known == commit["sha"]:
        return f"I'm up to date (latest change: {commit['message']})."
    if known:
        return (
            f"There's a newer version available:\n  {commit['message']}\n"
            "Say \"update yourself\" and I'll fetch it."
        )
    return (
        f"Latest published change: {commit['message']}\n"
        "I don't have a record of my own version yet -- say \"update yourself\" "
        "and I'll sync and start tracking it."
    )


# --- updating ---------------------------------------------------------------
def _wanted(path: str) -> bool:
    if path in _SKIP_EXACT or any(path.startswith(p) for p in _SKIP_PREFIX):
        return False
    return path.endswith(_KEEP_SUFFIX)


def _file_list() -> List[str]:
    url = (
        f"https://api.github.com/repos/{OWNER}/{REPO}/git/trees/"
        f"{urllib.parse.quote(BRANCH)}?recursive=1"
    )
    tree = _get(url, as_json=True)
    return [
        node["path"] for node in tree.get("tree", [])
        if node.get("type") == "blob" and _wanted(node["path"])
    ]


def _backup_dir() -> Path:
    path = data_dir() / "backups" / time.strftime("%Y%m%d_%H%M%S")
    path.mkdir(parents=True, exist_ok=True)
    return path


def update() -> str:
    """Fetch and apply the latest code. Returns a report."""
    root = project_root()
    try:
        paths = _file_list()
    except Exception as exc:
        return f"I couldn't fetch the file list ({exc.__class__.__name__})."
    if not paths:
        return "GitHub returned no files, so I've left everything alone."

    backup = None
    changed, added, failed = [], [], []

    for relative in sorted(paths):
        raw = (
            f"https://raw.githubusercontent.com/{OWNER}/{REPO}/"
            f"{urllib.parse.quote(BRANCH)}/{urllib.parse.quote(relative)}"
        )
        try:
            content = _get(raw)
        except Exception:
            failed.append(relative)
            continue

        target = root / relative
        if target.exists():
            if target.read_bytes() == content:
                continue
            # Keep a copy before overwriting, so an update can be undone.
            if backup is None:
                backup = _backup_dir()
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
            changed.append(relative)
        else:
            added.append(relative)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    commit = latest_commit()
    if commit and not failed:
        _save_state({"sha": commit["sha"], "message": commit["message"],
                     "applied": time.strftime("%Y-%m-%d %H:%M:%S")})

    if not changed and not added:
        report = "I was already up to date -- nothing changed."
    else:
        report = (
            f"Updated {len(changed)} file(s)"
            + (f" and added {len(added)}" if added else "")
            + "."
        )
        shown = (changed + added)[:8]
        report += "\n" + "\n".join(f"  {name}" for name in shown)
        if len(changed) + len(added) > len(shown):
            report += f"\n  ...and {len(changed) + len(added) - len(shown)} more"
        if backup is not None:
            report += f"\nPrevious versions saved in {backup}"
        report += "\nRestart me for the changes to take effect."

    if failed:
        report += f"\nI couldn't download {len(failed)} file(s); try again shortly."
    return report


def rollback() -> str:
    """Restore the most recent backup."""
    backups = data_dir() / "backups"
    if not backups.exists():
        return "I don't have any backups to roll back to."
    snapshots = sorted(p for p in backups.iterdir() if p.is_dir())
    if not snapshots:
        return "I don't have any backups to roll back to."

    latest = snapshots[-1]
    root = project_root()
    restored = 0
    for path in latest.rglob("*"):
        if path.is_file():
            target = root / path.relative_to(latest)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            restored += 1
    return (
        f"Restored {restored} file(s) from {latest.name}. Restart me to use them."
    )


def model_update(model: str) -> str:
    """Pull the newest build of the configured model."""
    import subprocess

    try:
        done = subprocess.run(
            ["ollama", "pull", model], capture_output=True, text=True, timeout=1800
        )
    except FileNotFoundError:
        return "Ollama isn't installed, so I can't update the model."
    except Exception as exc:
        return f"Couldn't update the model ({exc.__class__.__name__})."
    if done.returncode != 0:
        return f"Model update failed: {(done.stderr or '').strip()[:150]}"
    output = (done.stdout or "").lower()
    if "up to date" in output or "already" in output:
        return f"{model} is already the latest build."
    return f"{model} updated to the latest build."
