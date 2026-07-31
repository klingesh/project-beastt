"""Run shell commands, carefully.

Letting a voice assistant execute commands is genuinely risky: speech recognition
misfires, and a model can be talked into suggesting something harmful. The design
here is deliberately conservative:

  1. A denylist of irreversible or system-altering operations is refused outright
     and cannot be confirmed past.
  2. A small allowlist of read-only commands (git status, ls, python --version...)
     runs immediately.
  3. Everything else requires an explicit "yes" from the user, and the exact
     command is shown first.
  4. Commands never run through a shell, so pipes, redirection, and chaining
     can't smuggle in extra work. Output is captured with a timeout.

This is not a sandbox. It reduces the obvious footguns; it does not make
arbitrary execution safe.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from .paths import project_root

# --- 1. Never run these ----------------------------------------------------
_FORBIDDEN = [
    (r"\brm\s+(-[a-z]*\s+)*-?[rf]", "recursive delete"),
    (r"\brmdir\s+/s", "recursive directory delete"),
    (r"\bdel\s+/[sfq]", "forced delete"),
    (r"\bformat\b", "disk format"),
    (r"\bmkfs\b", "filesystem creation"),
    (r"\bdiskpart\b", "disk partitioning"),
    (r"\bfdisk\b", "disk partitioning"),
    (r"\bshutdown\b|\breboot\b|\bhalt\b", "shutting the machine down"),
    (r"\breg\s+delete\b", "registry deletion"),
    (r"\bregedit\b", "registry editing"),
    (r"\bnet\s+user\b", "user account changes"),
    (r"\bicacls\b|\btakeown\b|\bchown\b\s+-R", "permission changes"),
    (r"\bchmod\s+777\b", "unsafe permissions"),
    (r":\(\)\s*\{.*\};:", "fork bomb"),
    (r"\bdd\s+if=", "raw disk writes"),
    (r">\s*/dev/sd", "raw disk writes"),
    (r"\b(curl|wget|iwr|invoke-webrequest)\b.*\|\s*(ba)?sh", "piping the internet into a shell"),
    (r"\bgit\s+push\b.*--force|\bgit\s+push\s+-f\b", "force push"),
    (r"\bgit\s+reset\s+--hard\b", "discarding work irreversibly"),
    (r"\bgit\s+clean\s+-[a-z]*f", "deleting untracked files"),
    (r"\bpip\s+uninstall\b", "uninstalling packages"),
    (r"\bnpm\s+(uninstall|unpublish)\b", "uninstalling packages"),
    (r"\btaskkill\b.*\/f", "force-killing processes"),
    (r"\bkill\s+-9\b", "force-killing processes"),
    (r"\bsudo\b|\brunas\b", "elevated privileges"),
    (r"\beval\b|\bexec\b", "dynamic execution"),
    (r"\bschtasks\b|\bcrontab\b", "scheduled task changes"),
    (r"\bnetsh\b|\bufw\b|\biptables\b", "firewall/network changes"),
]

# --- 2. Safe to run without asking ------------------------------------------
# Matched on the first one or two words, so arguments can't widen the meaning.
_ALLOWED = {
    ("git", "status"), ("git", "log"), ("git", "diff"), ("git", "branch"),
    ("git", "remote"), ("git", "show"), ("git", "config"),
    ("dir",), ("ls",), ("pwd",), ("cd",), ("tree",), ("whoami",), ("date",),
    ("type",), ("cat",), ("head",), ("tail",), ("find",), ("where",), ("which",),
    ("echo",), ("hostname",), ("ver",), ("systeminfo",),
    ("python", "--version"), ("python", "-V"), ("py", "--version"),
    ("pip", "list"), ("pip", "show"), ("pip", "--version"), ("pip", "freeze"),
    ("node", "--version"), ("npm", "--version"), ("npm", "list"),
    ("ollama", "list"), ("ollama", "ps"),
    ("nvidia-smi",), ("tasklist",),
}

_MAX_OUTPUT = 4000
_TIMEOUT = 120


class Blocked(Exception):
    """Raised when a command is refused outright."""


def _tokens(command: str) -> List[str]:
    try:
        return shlex.split(command, posix=(os.name != "nt"))
    except ValueError:
        return command.split()


def check(command: str) -> Tuple[str, str]:
    """Classify a command.

    Returns (verdict, reason) where verdict is "blocked", "allowed", or "confirm".
    """
    text = " ".join(str(command or "").split())
    if not text:
        return "blocked", "there's no command there"

    lowered = text.lower()
    for pattern, reason in _FORBIDDEN:
        if re.search(pattern, lowered):
            return "blocked", reason

    # Chaining or redirection could hide a second command, so always confirm.
    if re.search(r"[;&|>]|\$\(|`", text):
        return "confirm", "it chains or redirects"

    tokens = _tokens(text)
    if not tokens:
        return "blocked", "I couldn't parse that"
    head1 = (tokens[0].lower(),)
    head2 = tuple(t.lower() for t in tokens[:2])
    if head2 in _ALLOWED or head1 in _ALLOWED:
        return "allowed", "read-only"
    return "confirm", "it isn't on my read-only list"


def run(command: str, cwd: Optional[Path] = None) -> str:
    """Execute a command and return its combined output.

    Raises Blocked if the command is on the denylist. Never uses a shell.
    """
    verdict, reason = check(command)
    if verdict == "blocked":
        raise Blocked(reason)

    tokens = _tokens(command)
    workdir = Path(cwd) if cwd else project_root()
    try:
        completed = subprocess.run(
            tokens,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            shell=False,
        )
    except FileNotFoundError:
        return f"I couldn't find '{tokens[0]}' on this system."
    except subprocess.TimeoutExpired:
        return f"That took longer than {_TIMEOUT} seconds, so I stopped it."
    except Exception as exc:
        return f"Couldn't run it ({exc.__class__.__name__}: {exc})."

    output = (completed.stdout or "") + (completed.stderr or "")
    output = output.strip() or "(no output)"
    if len(output) > _MAX_OUTPUT:
        output = output[:_MAX_OUTPUT] + f"\n... (truncated, {len(output)} chars total)"
    if completed.returncode != 0:
        output = f"(exit code {completed.returncode})\n{output}"
    return output
