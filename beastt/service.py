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
from .paths import data_dir, log_file, pid_file, project_root

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
    log = log_file()
    try:
        data_dir()
        if log.exists() and log.stat().st_size > _MAX_LOG_BYTES:
            log.replace(log.with_suffix(".log.old"))
        handle = open(log, "a", encoding="utf-8", buffering=1)
    except Exception:
        return None
    sys.stdout = _Tee(sys.__stdout__, handle)
    sys.stderr = _Tee(sys.__stderr__, handle)
    return handle


def _write_pid() -> None:
    """Record our PID so --status can reliably tell whether we're alive."""
    try:
        pid_file().write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


def _clear_pid() -> None:
    try:
        pid_file().unlink(missing_ok=True)
    except Exception:
        pass


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_service(config: Config) -> None:
    """Supervised standby loop for background operation."""
    # The shell may launch us from system32; work from the project instead so
    # relative paths and the .env file behave the same as a foreground run.
    try:
        os.chdir(project_root())
    except Exception:
        pass

    _setup_logging()
    _write_pid()
    print(f"\n===== BEASTT service started {_stamp()} (pid {os.getpid()}) =====")
    print(f"[service] Working dir: {os.getcwd()}")
    print(f"[service] Log file:    {log_file()}")
    print(f"[service] Python:      {sys.executable}")

    toast("BEASTT", f'Standby. Just call "{config.name}".')
    chime_ready()

    # Imported here so logging is already redirected.
    from .cli import _run_standby

    backoff = 5
    try:
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
    finally:
        _clear_pid()
        print(f"[service] Stopped {_stamp()}.")
