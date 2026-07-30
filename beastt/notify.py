"""Small audible/visual cues so BEASTT works without a visible terminal.

When BEASTT runs as a background service there's no window to look at, so it
needs to acknowledge you audibly -- like the chime Google Assistant or Siri
plays when it starts listening.
"""

from __future__ import annotations

import sys


def _beep(freq: int, ms: int) -> None:
    try:
        if sys.platform == "win32":
            import winsound

            winsound.Beep(freq, ms)
        else:
            # A bell is the portable best-effort fallback.
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception:
        pass


def chime_wake() -> None:
    """Rising two-tone chime: 'I'm listening'."""
    _beep(660, 90)
    _beep(880, 110)


def chime_sleep() -> None:
    """Falling two-tone chime: 'going back to standby'."""
    _beep(760, 90)
    _beep(520, 110)


def chime_ready() -> None:
    """Single soft tone: service started and is standing by."""
    _beep(880, 120)


def toast(title: str, message: str) -> None:
    """Best-effort desktop notification; silently ignored if unavailable."""
    if sys.platform != "win32":
        return
    try:
        # PowerShell balloon tip -- no extra Python dependency needed.
        import subprocess

        script = (
            "[reflection.assembly]::loadwithpartialname('System.Windows.Forms')"
            ">$null;$n=New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon=[System.Drawing.SystemIcons]::Information;"
            f"$n.BalloonTipTitle='{title}';$n.BalloonTipText='{message}';"
            "$n.Visible=$true;$n.ShowBalloonTip(4000);Start-Sleep -s 4;$n.Dispose()"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass
