"""Diagnostics: is BEASTT actually running, and is it set up correctly?

When BEASTT runs headless there's no window to inspect, so `--status` answers
the practical questions: is autostart installed, is a background process alive,
is the voiceprint enrolled, are the voice packages importable, and what does the
tail of the log say?
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .config import Config
from .paths import log_file, pid_file, resolve


def _ok(flag: bool) -> str:
    return "OK " if flag else "NO "


def _pid_alive(pid: int) -> bool:
    """Is a process with this PID running? (No wmic -- it's gone on new Windows.)"""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=20,
            ).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _running() -> list:
    """Return PIDs of live BEASTT services.

    Primary source is the PID file the service writes. We fall back to scanning
    the process list with PowerShell, because `wmic` has been removed from
    current Windows builds.
    """
    pids = []

    pf = pid_file()
    if pf.exists():
        try:
            pid = int(pf.read_text(encoding="utf-8").strip())
            if _pid_alive(pid):
                pids.append(str(pid))
        except Exception:
            pass
    if pids:
        return pids

    try:
        if sys.platform == "win32":
            script = (
                "Get-CimInstance Win32_Process -Filter "
                "\"Name='pythonw.exe' or Name='python.exe'\" "
                "| Where-Object { $_.CommandLine -like '*main.py*--wake*' } "
                "| Select-Object -ExpandProperty ProcessId"
            )
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, text=True, timeout=30,
            ).stdout
            pids = [line.strip() for line in out.splitlines() if line.strip().isdigit()]
        else:
            out = subprocess.run(["ps", "-eo", "pid,args"],
                                 capture_output=True, text=True, timeout=20).stdout
            for line in out.splitlines():
                if "main.py" in line and "--wake" in line:
                    pids.append(line.split()[0])
    except Exception:
        pass
    return pids


def report() -> None:
    config = Config.load()
    from . import autostart

    print(f"\n=== {config.name} status ===\n")

    # 0. Identity -- the most common source of "it won't wake" confusion is a
    #    stale name in .env, which makes it answer to something else entirely.
    print(f"[   ] Assistant name: {config.name}   (calls you: {config.user_name})")
    try:
        print(f"[   ] Wake words:     {', '.join(config.wake_words())}")
    except Exception:
        pass
    env = resolve(".env")
    if env.exists():
        print(f"[   ] Settings from:  {env}   <- this overrides defaults")
    else:
        print("[   ] No .env file (using defaults)")
    print()

    # 1. Autostart
    launcher = autostart.launcher_path()
    installed = launcher.exists()
    print(f"[{_ok(installed)}] Autostart installed")
    if installed:
        print(f"       {launcher}")
    else:
        print("       Enable with: python main.py --install-startup --my-voice")

    # 2. Is it running?
    pids = _running()
    print(f"[{_ok(bool(pids))}] Background BEASTT running" + (f" (pid {', '.join(pids)})" if pids else ""))
    if not pids and installed:
        print("       Start it now without rebooting:")
        print(f'       wscript "{launcher}"')

    # 3. Voice packages
    for module, hint in (
        ("sounddevice", "pip install sounddevice"),
        ("whisper", "pip install openai-whisper"),
        ("pyttsx3", "pip install pyttsx3"),
    ):
        try:
            __import__(module)
            print(f"[OK ] {module} available")
        except Exception:
            print(f"[NO ] {module} missing -- {hint}")

    # 4. Voiceprint
    vp = resolve(config.voiceprint_path)
    legacy = vp.with_suffix(".npy")
    has_vp = vp.exists() or legacy.exists()
    print(f"[{_ok(has_vp)}] Voiceprint enrolled")
    if not has_vp:
        print("       Run: python main.py --enroll")

    # 5. Brain
    try:
        from .brain.ollama_brain import OllamaBrain

        brain = OllamaBrain(config.model, config.ollama_url)
        print(f"[{_ok(brain.server_running())}] Ollama reachable at {config.ollama_url}")
        print(f"[{_ok(brain.is_available())}] Model '{config.model}' pulled")
    except Exception as exc:
        print(f"[NO ] Couldn't check Ollama ({exc})")

    # 6. Memory
    mem = resolve(config.memory_path)
    print(f"[{_ok(mem.exists())}] Long-term memory file ({mem})")

    # 7. Log tail
    log = log_file()
    print(f"\n--- last lines of {log} ---")
    if log.exists():
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-20:]:
                print(f"  {line}")
            if not lines:
                print("  (log is empty -- the service may not have started)")
        except Exception as exc:
            print(f"  (couldn't read log: {exc})")
    else:
        print("  (no log yet -- the background service has not run)")

    print(f"\nWorking directory: {os.getcwd()}")
    print(f"Python: {sys.executable}\n")
