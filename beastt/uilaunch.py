"""Bringing the chat interface up when the wake word is heard.

Running as a background service, being called by name did almost nothing
observable: a beep, and then a spoken question about whether to talk by voice or
by text. There is no console to watch, so someone who called out and got silence
could not tell whether they had been heard at all.

This is the other half of the answer. Say the name, get greeted by name, and have
the interface you were going to use anyway open in front of you.

The interface may or may not already be running, and starting a second copy would
just fail to bind the port -- so the state is checked first, by connecting to it.
That is the only reliable test: a PID file can be stale, and the process list says
nothing about whether the socket is actually accepting.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Optional, Tuple

DEFAULT_PORT = 8765
#: How long to wait for a freshly started server to accept a connection. Loading
#: the module tree takes a second or two on a cold start.
START_TIMEOUT = 15.0
#: A connect attempt against a local port either answers at once or is not there.
PROBE_TIMEOUT = 0.4


def url_for(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def is_running(port: int = DEFAULT_PORT, timeout: float = PROBE_TIMEOUT) -> bool:
    """Is something already accepting connections on that port?

    Deliberately a socket connect rather than a PID file or a process scan. The
    PID file can outlive the process, and a process being alive says nothing
    about whether its socket is up yet -- which matters here, because the whole
    point is to open a browser at something that will answer.
    """
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _console_free_python() -> Path:
    """pythonw.exe where it exists, so no console flashes up.

    The service already runs under pythonw, but this is also reachable from a
    normal terminal, where sys.executable is python.exe.
    """
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return windowless if windowless.exists() else exe


def start(port: int = DEFAULT_PORT, timeout: float = START_TIMEOUT) -> bool:
    """Start the interface in the background. True once it answers.

    `--no-browser` because the caller opens it: the server would otherwise open
    one on startup and this would open a second.
    """
    from .paths import project_root

    command = [
        str(_console_free_python()), str(project_root() / "main.py"),
        "--ui", "--no-browser", "--port", str(int(port)), "--quiet",
    ]
    flags = 0
    for name in ("CREATE_NO_WINDOW", "DETACHED_PROCESS"):
        flags |= getattr(subprocess, name, 0)
    try:
        subprocess.Popen(command, cwd=str(project_root()), creationflags=flags)
    except Exception as exc:
        print(f"[ui] Couldn't start the interface ({exc.__class__.__name__}: {exc})")
        return False

    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        if is_running(port):
            return True
        time.sleep(0.4)
    print(f"[ui] Started the interface but it didn't answer on port {port} in "
          f"{timeout:.0f}s.")
    return False


def open_in_browser(port: int = DEFAULT_PORT) -> bool:
    try:
        return bool(webbrowser.open(url_for(port)))
    except Exception as exc:
        print(f"[ui] Couldn't open a browser ({exc.__class__.__name__})")
        return False


def ensure(port: int = DEFAULT_PORT, on_step=None) -> Tuple[bool, str]:
    """Make sure the interface is up and in front of the user.

    Returns (ok, something to say). The message is spoken, so it is phrased for
    the ear and says which case happened -- "already open" and "just started it"
    feel different to someone waiting, and a failure has to be admitted rather
    than leaving them looking at nothing.
    """
    port = int(port or DEFAULT_PORT)

    def step(message: str) -> None:
        print(f"[ui] {message}")
        if on_step:
            try:
                on_step(message)
            except Exception:
                pass

    if is_running(port):
        step(f"Already running on {url_for(port)}")
        opened = open_in_browser(port)
        if opened:
            return True, "The chat is already open -- I've brought it to the front."
        return True, (f"The chat is running at localhost port {port}, but I "
                      f"couldn't open your browser. Have a look yourself.")

    step("Not running yet -- starting it")
    if not start(port):
        return False, ("I couldn't get the chat interface open. Try running "
                       "python main.py --ui yourself and tell me what it says.")

    step(f"Up on {url_for(port)}")
    if open_in_browser(port):
        return True, "I've opened the chat in your browser."
    return True, (f"The chat is ready at localhost port {port} -- I couldn't "
                  f"open the browser, so you'll need to go there yourself.")
