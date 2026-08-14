"""A window that shows what the background service is doing.

Asked for as "open cmd prompt", and the reason is worth stating: running as a
service there is nothing to look at, so the only way to tell whether the
assistant was alive and hearing anything was to stop it and run it in a terminal
by hand -- which is also the one arrangement where it demonstrably works, because
running it by hand is what makes it visible.

So: a console that follows the service's own log. Note what it deliberately is
*not*. Opening a second `--wake` would put two processes on one microphone and
make waking worse; opening a `--text` chat starts a whole second assistant. What
is actually wanted is to *see*, and seeing needs no second listener.

The log is the same file `--status` prints the tail of, written by the service
through a tee, so this shows the wake attempts as they happen -- including the
misses, which are the interesting ones when the answer is "it never hears me".
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator, List, Optional

#: How often to look for new output. The log is written line by line, so this is
#: purely how laggy the window feels.
POLL_SECONDS = 0.4
#: Lines of history to show before following, so the window is not blank on a
#: quiet system.
BACKLOG_LINES = 40


def tail(path, count: int = BACKLOG_LINES) -> List[str]:
    """The last `count` lines, or [] if there is nothing to read.

    Reads the whole file, which is fine: the service caps it at a megabyte and
    rotates, so there is a hard bound on the work.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    return lines[-max(0, count):] if count else lines


def follow(path, poll: float = POLL_SECONDS,
           stop=None) -> Iterator[str]:
    """Yield lines as they are written, indefinitely.

    Handles the service's rotation. At a megabyte it renames the log and starts a
    new one, so the file we are holding a position in either shrinks or is
    replaced -- and a follower that kept seeking to its old offset would sit in
    silence for ever afterwards, looking exactly like a dead service.
    """
    position = 0
    try:
        position = os.path.getsize(path)
    except OSError:
        position = 0

    while True:
        if stop is not None and stop():
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            time.sleep(poll)
            continue

        if size < position:
            # Rotated (or truncated): start again from the beginning.
            position = 0
            yield "--- log rotated ---"

        if size > position:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(position)
                    fresh = handle.read()
                    position = handle.tell()
            except OSError:
                time.sleep(poll)
                continue
            for line in fresh.splitlines():
                yield line
        else:
            time.sleep(poll)


def run(config=None, path=None, once: bool = False) -> int:
    """Print the recent log and then follow it. Blocks until Ctrl+C."""
    from .paths import log_file

    target = Path(path) if path else log_file()
    name = getattr(config, "name", "BEASTT") if config else "BEASTT"

    print("=" * 62)
    print(f"  {name} -- live activity log")
    print("=" * 62)
    print(f"  {target}")
    print("  This is a viewer. Closing it does not stop the assistant.")
    print("  Ctrl+C to close.\n")

    if not target.exists():
        print("  (no log yet -- it appears once the background service starts)")
        print("  Start it with:  python main.py --wake --service\n")

    for line in tail(target):
        print(line)
    if once:
        return 0

    print("\n--- following ---\n")
    try:
        for line in follow(target):
            print(line, flush=True)
    except KeyboardInterrupt:
        print("\nClosed the viewer. The assistant is still running.")
    return 0


def _console_python() -> Path:
    """python.exe, not pythonw.exe: this window is the entire point."""
    exe = Path(sys.executable)
    console = exe.with_name("python.exe")
    return console if console.exists() else exe


def open_window(config=None) -> bool:
    """Open a separate console following the log. True if it was launched."""
    from .paths import project_root

    command = [str(_console_python()), str(project_root() / "main.py"),
               "--watch-log"]
    try:
        subprocess.Popen(
            command,
            cwd=str(project_root()),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        return True
    except Exception as exc:
        print(f"[log] Couldn't open a log window ({exc.__class__.__name__}: {exc})")
        return False
