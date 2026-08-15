"""Notice when the code on disk is newer than the code that is running.

Reported from a real machine, minutes after an update that installed cleanly:

    I couldn't attach pasted-2026-08-15-10-50-20-09.png: I can't read '.png'.
    I handle PDF, Word, Excel, PowerPoint, CSV, JSON, text and source files.

Both halves of that were true at the same time. The filename was invented by the
*new* pasting code, and the refusal came from the *old* uploading code. The
interface reads its Javascript, HTML and CSS off disk on every single request, so
the front end updates the moment the files change; its Python was read into
memory when the process started and stays there until the process ends. Update a
running server and you get a new front end talking to an old back end, which
produces errors describing a version of the program that no longer exists
anywhere on disk. There is nothing to search for and nothing to fix.

So: hash the package's Python at startup, hash it again when asked, and say
plainly that a restart is owed. Deliberately not a reload -- swapping modules
under a live server is a far bigger promise than it looks (open sockets, threads
mid-reply, two copies of a class failing isinstance) and the honest fix takes two
seconds.

One known imprecision, chosen on purpose. This compares every .py file in the
package, not only the ones this process actually imported, so changing a module
that was never loaded here also asks for a restart. Being exact would mean
walking sys.modules and would go wrong in the more expensive direction: modules
imported lazily -- `imageread` among them -- are absent at check time and would
be reported as fine, which is the same silent-stale trap this module exists to
close. Over-reporting costs two seconds. Under-reporting costs an evening.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Dict, List, Optional

#: The package whose code is being watched -- beastt/, wherever it is installed.
PACKAGE_ROOT = Path(__file__).resolve().parent

#: How many changed filenames a report will list before it stops naming them.
#: An update touching thirty files should not push a wall of text at anyone.
MAX_NAMED = 8

_baseline: Optional[Dict[str, str]] = None


def code_files() -> List[Path]:
    """Every Python file in the package, sorted.

    Only .py: the static assets next door are read from disk per request, so
    they are never stale and must not trigger a restart notice.
    """
    try:
        return sorted(p for p in PACKAGE_ROOT.rglob("*.py") if p.is_file())
    except OSError:
        return []


def snapshot() -> Dict[str, str]:
    """Relative path -> hash of contents, for the code on disk right now.

    Contents, not timestamps. The question is "is this different code", not "was
    this file written again": every updater rewrites files, so does a git
    checkout, so does an editor saving an unmodified buffer, and a notice that
    fired after each of those would be ignored inside a week.

    There was a stat cache here -- size and mtime as a key, to skip re-reading
    while nothing moved. It came out because it was wrong, not because it was
    unnecessary: two writes close together share an mtime on some filesystems
    (measured, on the one this is developed on), so a same-size edit inside one
    tick was invisible. Correctness of the answer beats saving a millisecond in
    a check that runs three times a minute. Reading forty small files costs
    nothing measurable; missing an update costs an evening of confusion.
    """
    found: Dict[str, str] = {}
    for path in code_files():
        try:
            found[path.relative_to(PACKAGE_ROOT).as_posix()] = hashlib.sha256(
                path.read_bytes()).hexdigest()
        except (OSError, ValueError):
            # A file that vanished or turned unreadable mid-walk. Skipping it
            # makes it look deleted, which is the truthful reading, and matters
            # far less than raising out of a status request.
            continue

    return found


def remember() -> Dict[str, str]:
    """Record the code as it is now. Called once, as the server starts."""
    global _baseline

    _baseline = snapshot()
    return _baseline


def changed() -> List[str]:
    """Files whose contents differ from what was running at startup.

    Empty until `remember` has been called -- with nothing to compare against,
    the honest answer is "no evidence of a change", not "everything changed".
    """
    if _baseline is None:
        return []

    current = snapshot()
    return sorted(name for name in set(_baseline) | set(current)
                  if _baseline.get(name) != current.get(name))


def restart_hint() -> str:
    """How to restart *this* process, in the terms of how it was started.

    Worth the branch: the two cases have no overlap. A server launched by the
    wake word runs under pythonw with no console, so "press Ctrl+C" names a
    window that does not exist -- which is exactly the sort of instruction that
    makes someone give up and assume the update failed.
    """
    # sys.platform, not os.name: patching the latter in a test changes which
    # class pathlib.Path instantiates and breaks everything downstream of it.
    if sys.platform != "win32":
        return "stop it and start it again to pick up the update"

    # Split by hand rather than with Path: this decides on a Windows path, and
    # on any other platform Path would treat the backslashes as ordinary
    # characters and hand back the whole string as the filename.
    executable = sys.executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if executable.startswith("pythonw"):
        return ("it is running without a window, so there is nothing to Ctrl+C: "
                "run  taskkill /F /IM pythonw.exe  in a terminal, then start "
                "BEASTT again from your Startup shortcut")

    return "press Ctrl+C in its terminal, then run  python main.py --ui  again"


def report(name: str = "This") -> Dict:
    """What the interface should be told. `{"stale": False}` when all is well.

    Phrased to explain the symptom rather than announce the event, because the
    reason anyone needs this message is that the program is already misbehaving
    in a way that makes no sense.
    """
    files = changed()
    if not files:
        return {"stale": False}

    count = len(files)
    return {
        "stale": True,
        "count": count,
        "changed": files[:MAX_NAMED],
        "detail": (
            f"{name} was updated on disk, but this window is still running the "
            f"code from before it "
            f"({count} file{'s' if count != 1 else ''} changed). Until it "
            f"restarts you may see errors that no longer match the code"
        ),
        "fix": restart_hint(),
    }
