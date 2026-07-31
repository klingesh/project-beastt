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


# --- shell built-ins --------------------------------------------------------
# On Windows these are part of cmd.exe rather than programs on disk, so running
# them without a shell fails with "not found". The common ones are implemented
# directly in Python, which keeps them working without introducing a shell.
def _is_flag(arg: str) -> bool:
    """A switch rather than a path.

    Unix flags start with '-'. Windows switches look like '/s' or '/q' -- a
    single short token -- whereas '/tmp/evil' is an absolute path and must not be
    mistaken for one, or it would be silently ignored instead of refused.
    """
    if arg.startswith("-"):
        return True
    # Only Windows uses slash switches, and they're one or two characters --
    # anything longer (e.g. /etc) is a path and must be range-checked, not skipped.
    return os.name == "nt" and bool(re.fullmatch(r"/[a-zA-Z?]{1,2}", arg))


def _resolve_inside(cwd: Path, name: str) -> Path:
    """Resolve a path and refuse anything outside the project folder."""
    candidate = Path(name)
    target = (candidate if candidate.is_absolute() else cwd / candidate).resolve()
    root = cwd.resolve()
    if target != root and root not in target.parents:
        raise Blocked("a path outside the project folder")
    return target


def _bi_mkdir(args: List[str], cwd: Path) -> str:
    paths = [a for a in args if not _is_flag(a)]
    if not paths:
        return "Which folder should I create?"
    made = []
    for name in paths:
        target = _resolve_inside(cwd, name)
        if target.exists():
            made.append(f"{name} already exists")
            continue
        target.mkdir(parents=True, exist_ok=True)
        made.append(f"created {name}")
    return "\n".join(made) or "(nothing to do)"


def _bi_list(args: List[str], cwd: Path) -> str:
    targets = [a for a in args if not _is_flag(a)]
    base = _resolve_inside(cwd, targets[0]) if targets else cwd
    if not base.exists():
        return f"'{base.name}' doesn't exist."
    if base.is_file():
        return f"{base.name}  ({base.stat().st_size} bytes)"
    rows = []
    for entry in sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if entry.is_dir():
            rows.append(f"  <DIR>  {entry.name}")
        else:
            rows.append(f"  {entry.stat().st_size:>8}  {entry.name}")
    return "\n".join(rows) or "(empty folder)"


def _bi_pwd(args: List[str], cwd: Path) -> str:
    return str(cwd)


def _bi_echo(args: List[str], cwd: Path) -> str:
    return " ".join(args)


def _bi_read(args: List[str], cwd: Path) -> str:
    names = [a for a in args if not _is_flag(a)]
    if not names:
        return "Which file should I read?"
    target = _resolve_inside(cwd, names[0])
    if not target.is_file():
        return f"'{names[0]}' isn't a file I can read."
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Couldn't read it ({exc})."
    return text


def _bi_cd(args: List[str], cwd: Path) -> str:
    # Each command runs in its own process, so a directory change wouldn't
    # persist. Say so rather than appearing to succeed.
    return (
        f"I always work from {cwd}. Give me a path in the command itself, "
        "for example \"run ls beastt_workspace\"."
    )


_BUILTINS = {
    "mkdir": _bi_mkdir, "md": _bi_mkdir,
    "dir": _bi_list, "ls": _bi_list,
    "pwd": _bi_pwd, "cwd": _bi_pwd,
    "echo": _bi_echo,
    "type": _bi_read, "cat": _bi_read,
    "cd": _bi_cd, "chdir": _bi_cd,
}

# Other cmd.exe built-ins we can hand to `cmd /c`. Only reached after the safety
# checks, and only when no shell metacharacters are present.
_CMD_BUILTINS = {
    "cls", "copy", "move", "ren", "rename", "ver", "vol", "date", "time",
    "tree", "where", "set", "assoc", "ftype", "path",
}


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
    head = tokens[0].lower()

    # Shell built-ins: handled in Python so they work without a shell.
    handler = _BUILTINS.get(head)
    if handler is not None:
        output = handler(tokens[1:], workdir)
        if len(output) > _MAX_OUTPUT:
            output = output[:_MAX_OUTPUT] + "\n... (truncated)"
        return output

    # Remaining cmd.exe built-ins need cmd itself. Refuse if anything could be
    # interpreted as a second command.
    if os.name == "nt" and head in _CMD_BUILTINS:
        if any(re.search(r"[;&|<>`$]", token) for token in tokens):
            raise Blocked("shell metacharacters in a built-in command")
        tokens = ["cmd", "/c", *tokens]

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
        hint = ""
        if os.name == "nt":
            hint = " (if it's a cmd built-in, tell me and I'll add it)"
        return f"I couldn't find '{tokens[0]}' on this system.{hint}"
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
