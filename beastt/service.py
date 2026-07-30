"""Run BEASTT as a background service (no visible window).

When launched by the autostart shim there is no console, so:
  * stdout/stderr are redirected to a rotating-ish log file, and print() calls
    throughout the app keep working instead of crashing on a missing stream.
  * The standby loop is supervised: if it raises, we log it and restart rather
    than silently dying, since nobody is watching a terminal.
"""

from __future__ import annotations

import datetime
import io
import os
import sys
import time
from pathlib import Path

from .config import Config
from .notify import chime_ready, toast

_LOG_DIR = Path("beastt_memory")
_LOG_FILE = _LOG_DIR / "beastt.log"
_MAX_LOG_BYTES = 1_000_000


class _Tee(io.TextIOBase):
    """Write to the log file, and to the real stream when one exists."""

    def __init__(self, stream, handle):
        self._stream = stream
        self._handle = handle

    def write(self, text):
        # Written verbatim: the app's own output is already readable, and
        # per-chunk timestamps interleave badly with partial writes.
        try:
            self._handle.write(text)
            self._handle.flush()
        except Exception:
            pass
        try:
            if self._stream is not None:
                self._stream.write(text)
                self._stream.flush()
        except Exception:
            pass
        return len(text)

    def flush(self):
        for target in (self._handle, self._stream):
            try:
                if target is not None:
                    target.flush()
            except Exception:
                pass


def _setup_logging():
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        if _LOG_FILE.exists() and _LOG_FILE.stat().st_size > _MAX_LOG_BYTES:
            _LOG_FILE.replace(_LOG_FILE.with_suffix(".log.old"))
        handle = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)
    except Exception:
        return None
    sys.stdout = _Tee(sys.__stdout__, handle)
    sys.stderr = _Tee(sys.__stderr__, handle)
    return handle


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_service(config: Config) -> None:
    """Supervised standby loop for background operation."""
    _setup_logging()
    print(f"\n===== BEASTT service started {_stamp()} (pid {os.getpid()}) =====")
    print(f"[service] Log file: {_LOG_FILE.resolve()}")

    toast("BEASTT", f'Standby. Just call "{config.name}".')
    chime_ready()

    # Imported here so logging is already redirected.
    from .cli import _run_standby

    backoff = 5
    while True:
        try:
            _run_standby(config, verbose=True)
            print("[service] Standby loop ended; shutting down.")
            return
        except KeyboardInterrupt:
            print("[service] Interrupted; shutting down.")
            return
        except Exception as exc:  # keep the assistant alive
            print(f"[service] {_stamp()} crashed with {exc.__class__.__name__}: {exc}")
            import traceback

            traceback.print_exc()
            print(f"[service] Restarting in {backoff}s...")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
