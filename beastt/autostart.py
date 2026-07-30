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
    return (
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.CurrentDirectory = "{root}"\r\n'
        f'shell.Run """{py}"" ""{main}"" {args}", 0, False\r\n'
    )


def install(args: str = "--wake --my-voice --service") -> bool:
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

    print("[autostart] Installed! BEASTT will now start automatically when you log in.")
    print(f"            Launcher: {path}")
    print(f"            Command:  main.py {args}")
    print("\n            Start it right now without rebooting:")
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
