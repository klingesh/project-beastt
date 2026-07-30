"""Install BEASTT so it starts with Windows and runs invisibly in the background.

Approach: drop a tiny .vbs launcher in the user's Startup folder. VBScript can
start a process with a hidden window, which is what keeps BEASTT from flashing
up a console. It launches `pythonw.exe` (the windowless Python) so nothing is
ever shown.

No admin rights needed -- this is per-user. On non-Windows platforms the helper
explains the equivalent manual step instead of guessing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_LAUNCHER_NAME = "BEASTT.vbs"


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def launcher_path() -> Path:
    return _startup_dir() / _LAUNCHER_NAME


def _pythonw() -> str:
    """Path to the windowless Python interpreter, falling back to python.exe."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def project_root() -> Path:
    # beastt/autostart.py -> project root is one level up from the package.
    return Path(__file__).resolve().parent.parent


def _vbs_contents(args: str) -> str:
    py = _pythonw().replace('"', '""')
    root = str(project_root()).replace('"', '""')
    main = str(project_root() / "main.py").replace('"', '""')
    fail_log = str(project_root() / "beastt_memory" / "launch_error.txt").replace('"', '""')
    # On error, leave a breadcrumb: a silent failure is otherwise invisible.
    return (
        'On Error Resume Next\r\n'
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.CurrentDirectory = "{root}"\r\n'
        f'shell.Run """{py}"" ""{main}"" {args}", 0, False\r\n'
        'If Err.Number <> 0 Then\r\n'
        '  Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
        f'  Set f = fso.CreateTextFile("{fail_log}", True)\r\n'
        '  f.WriteLine "Launch failed: " & Err.Number & " " & Err.Description\r\n'
        f'  f.WriteLine "Command: {py} {main} {args}"\r\n'
        '  f.Close\r\n'
        'End If\r\n'
    )


def launch_now() -> bool:
    """Start the hidden background BEASTT immediately, without waiting for login."""
    path = launcher_path()
    if sys.platform != "win32" or not path.exists():
        return False
    try:
        import subprocess

        subprocess.Popen(
            ["wscript", str(path)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        return True
    except Exception as exc:
        print(f"[autostart] Couldn't launch it now ({exc}).")
        print(f'            Start it manually with:  wscript "{path}"')
        return False


def install(args: str = "--wake --my-voice --service", start_now: bool = True) -> bool:
    """Create the startup launcher. Returns True on success."""
    if sys.platform != "win32":
        print("[autostart] Automatic setup currently supports Windows only.")
        print("            On Linux/macOS, run BEASTT from your session autostart:")
        print(f"            {_pythonw()} {project_root() / 'main.py'} {args}")
        return False

    startup = _startup_dir()
    if not startup.exists():
        print(f"[autostart] Couldn't find the Startup folder at:\n            {startup}")
        return False

    path = launcher_path()
    try:
        path.write_text(_vbs_contents(args), encoding="utf-8")
    except Exception as exc:
        print(f"[autostart] Couldn't write the launcher ({exc}).")
        return False

    print("[autostart] Installed! BEASTT will start automatically when you log in.")
    print(f"            Launcher: {path}")
    print(f"            Command:  main.py {args}")

    if start_now and launch_now():
        print("\n[autostart] Started it in the background now, too.")
        print("            Give it ~20s to load, listen for the chime, then call BEASTT.")
        print("            Check it with:  python main.py --status")
    else:
        print("\n            Start it without rebooting:")
        print(f'            wscript "{path}"')

    print("\n            To remove it later:  python main.py --uninstall-startup")
    return True


def uninstall() -> bool:
    path = launcher_path()
    if not path.exists():
        print("[autostart] Nothing to remove -- BEASTT isn't set to auto-start.")
        return False
    try:
        path.unlink()
    except Exception as exc:
        print(f"[autostart] Couldn't remove the launcher ({exc}).")
        return False
    print("[autostart] Removed. BEASTT will no longer start with Windows.")
    return True


def status() -> None:
    path = launcher_path()
    if path.exists():
        print(f"[autostart] Enabled -- launcher at {path}")
    else:
        print("[autostart] Disabled. Enable with:  python main.py --install-startup")
