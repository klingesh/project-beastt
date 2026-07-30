"""Filesystem locations, anchored to the project rather than the current directory.

The background service can be launched with an arbitrary working directory (the
Windows shell may start it in system32), so any path resolved relative to the
CWD would put logs, memory, and voiceprints in the wrong place -- or fail to
write at all. Everything therefore resolves against the project root.
"""

from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    # beastt/paths.py -> project root is the package's parent.
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Directory for logs, memory, voiceprints. Created on demand."""
    path = project_root() / "beastt_memory"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return path


def resolve(user_path: str) -> Path:
    """Resolve a configured path, treating relative paths as project-relative."""
    path = Path(user_path)
    if path.is_absolute():
        return path
    return project_root() / path


def log_file() -> Path:
    return data_dir() / "beastt.log"


def pid_file() -> Path:
    return data_dir() / "beastt.pid"
