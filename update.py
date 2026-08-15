#!/usr/bin/env python3
"""Update BEASTT to the latest code from GitHub.

Downloading files one-by-one with curl is easy to get wrong -- a single missed
file causes a ModuleNotFoundError at runtime. This script fetches the whole
file list from GitHub and downloads everything, so the local copy can't end up
half-updated.

Usage:
    python update.py                 # update from the default branch
    python update.py --branch main   # update from another branch
    python update.py --dry-run       # show what would change

Your personal files are never touched: .env, beastt_memory/ (memory,
voiceprint, logs).
"""

from __future__ import annotations

import argparse
import os
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OWNER = "klingesh"
REPO = "project-beastt"
DEFAULT_BRANCH = "feat/beastt-ai-companion"

# Only code/config is synced; anything personal stays local. beastt_output/ and
# beastt_workspace/ hold generated documents, cloned repositories and generated
# code -- none of it ours to overwrite, and the in-app updater has always skipped
# them. This list matching that one is the point.
SKIP_EXACT = {".env"}
SKIP_PREFIX = ("beastt_memory/", "beastt_output/", "beastt_workspace/",
               ".git/", "jarvis/")

ROOT = Path(__file__).resolve().parent


def _setting(key: str) -> str:
    """A BEASTT_* setting from .env, or the environment, or "".

    Read straight out of the file rather than through beastt.config, because this
    script has to work before the code it is updating is in place -- including
    when a broken module is the very reason someone is running it.
    """
    env = ROOT / ".env"
    if env.exists():
        try:
            for line in env.read_text(encoding="utf-8",
                                      errors="replace").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                if name.strip() == key:
                    found = value.split("#")[0].strip().strip("\"'")
                    if found:
                        return found
        except Exception:
            pass
    return os.environ.get(key, "").strip()


def _token() -> str:
    return _setting("BEASTT_GITHUB_TOKEN")


TOKEN = _token()

#: Must match Config.ui_port's default. Duplicated rather than imported, for the
#: same reason the token is read by hand.
DEFAULT_UI_PORT = 8765


def ui_port() -> int:
    try:
        return int(_setting("BEASTT_UI_PORT"))
    except ValueError:
        return DEFAULT_UI_PORT


def _ui_answering(port: int, timeout: float = 0.4) -> bool:
    """Is the chat interface accepting connections right now?

    A socket connect, like beastt.uilaunch's own check: a PID file can outlive
    its process, and a listed process says nothing about whether its socket is up.
    """
    import socket

    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _windowless_beastt() -> bool:
    """Is a console-free Python running -- the wake-word service, or a UI it started?

    Named by image rather than by inspecting command lines: the wake service and
    the interface it launches both run under pythonw.exe precisely so that no
    console appears, and one restart covers both.
    """
    # sys.platform rather than os.name, because a test that patched os.name
    # would change which class pathlib.Path builds and break unrelated code.
    if sys.platform != "win32":
        return False
    try:
        import subprocess

        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq pythonw.exe", "/NH"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return False
    return "pythonw.exe" in (result.stdout or "").lower()


def running_parts(port: int) -> list:
    """Which parts of BEASTT are up right now, still holding the replaced code."""
    parts = []
    if _ui_answering(port):
        parts.append(f"the chat interface, on http://127.0.0.1:{port}")
    if _windowless_beastt():
        parts.append("a background BEASTT (the wake-word listener)")
    return parts


def restart_notice(parts: list) -> list:
    """What to print after an update, given what was found running.

    Because the files being replaced are not the files in memory. The interface
    re-reads its Javascript from disk on every request but its Python only at
    startup, so updating underneath it produces a new front end driving an old
    back end -- and errors that describe code which no longer exists on disk.
    That cost an evening once: a pasted screenshot named by brand-new code and
    then refused by the old uploader, which had never heard of images.
    """
    if not parts:
        return []

    lines = ["", "This update is on disk but not in memory. Still running:"]
    lines += [f"  - {part}" for part in parts]
    lines.append("")
    lines.append("Restart to load it:")
    if sys.platform == "win32":
        lines.append("  taskkill /F /IM pythonw.exe        # stops the background one")
        lines.append("  ...then start BEASTT from your Startup shortcut")
        lines.append("  (started it yourself in a terminal? Ctrl+C there instead)")
    else:
        lines.append("  stop BEASTT and start it again")
    return lines


def _headers(accept: str = "*/*") -> dict:
    headers = {
        "User-Agent": "beastt-updater",
        "Accept": accept,
        "Cache-Control": "no-cache, no-store",
        "Pragma": "no-cache",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


def _get(url: str, as_json: bool = False, accept: str = "*/*"):
    # raw.githubusercontent.com sits behind a CDN that caches for minutes, so a
    # plain request can return a stale file moments after a push. A unique query
    # string makes it a distinct resource, and the no-cache headers cover proxies.
    separator = "&" if "?" in url else "?"
    url = f"{url}{separator}_={int(time.time() * 1000)}"
    req = urllib.request.Request(url, headers=_headers(accept))
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    return json.loads(data) if as_json else data


#: Everything that makes up the app: Python, config, and the web interface's
#: assets. Omitting the web types once left the UI serving 404s -- and omitting
#: the config types did the same thing to the test suite, which arrived without
#: the pytest.ini that tells pytest where to look and which markers exist.
WANTED_SUFFIXES = (
    ".py", ".txt", ".md", ".example", ".gitignore",
    ".html", ".css", ".js", ".json", ".svg", ".ico",
    ".ini", ".cfg", ".toml", ".yml", ".yaml",
)


def _wanted(path: str) -> bool:
    if path in SKIP_EXACT:
        return False
    if any(path.startswith(p) for p in SKIP_PREFIX):
        return False
    return path.endswith(WANTED_SUFFIXES)


def list_files(branch: str) -> list:
    api = (
        f"https://api.github.com/repos/{OWNER}/{REPO}/git/trees/"
        f"{urllib.parse.quote(branch)}?recursive=1"
    )
    tree = _get(api, as_json=True)
    if "tree" not in tree:
        raise RuntimeError(tree.get("message", "unexpected GitHub response"))
    return [n["path"] for n in tree["tree"] if n["type"] == "blob" and _wanted(n["path"])]


def raw_url(branch: str, path: str) -> str:
    return (
        f"https://raw.githubusercontent.com/{OWNER}/{REPO}/"
        f"{branch}/{urllib.parse.quote(path)}"
    )


def file_url(branch: str, path: str) -> str:
    """Where to fetch one file's contents from.

    raw.githubusercontent.com serves public repositories quickly and without
    counting against any rate limit, so it stays the default. It cannot serve a
    private one, though: making the repository private would break every update
    with a bare 404 and no clue why.

    With a token we use the Contents API instead, which is the documented way to
    read a file from a private repository and returns the bytes directly given the
    right Accept header.
    """
    if TOKEN:
        return (
            f"https://api.github.com/repos/{OWNER}/{REPO}/contents/"
            f"{urllib.parse.quote(path)}?ref={urllib.parse.quote(branch)}"
        )
    return raw_url(branch, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Update BEASTT from GitHub.")
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    how = "signed in" if TOKEN else "anonymous"
    print(f"Updating BEASTT from {OWNER}/{REPO} ({args.branch}) [{how}]...\n")

    try:
        files = list_files(args.branch)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404) and not TOKEN:
            # The single most likely cause, and impossible to guess from a 404.
            print(f"ERROR: GitHub returned {exc.code}.")
            print("If the repository is private, add a token to .env:")
            print("  BEASTT_GITHUB_TOKEN=your_token_here")
            print("Otherwise check the branch name.")
        elif exc.code in (401, 403):
            print(f"ERROR: GitHub returned {exc.code} -- the token in .env was "
                  "refused. Check it hasn't expired and that it grants read "
                  f"access to {OWNER}/{REPO}.")
        else:
            print(f"ERROR: GitHub returned {exc.code} -- is the branch name right?")
        return 1
    except Exception as exc:
        print(f"ERROR: couldn't list files ({exc}).")
        return 1

    if not files:
        print("ERROR: no files found.")
        return 1

    added = updated = same = failed = 0
    for path in sorted(files):
        target = ROOT / path
        try:
            content = _get(
                file_url(args.branch, path),
                # The Contents API returns JSON metadata unless asked for the
                # file itself; raw.githubusercontent ignores this.
                accept="application/vnd.github.raw" if TOKEN else "*/*",
            )
        except Exception as exc:
            print(f"  FAILED  {path}  ({exc})")
            failed += 1
            continue

        existed = target.exists()
        if existed and target.read_bytes() == content:
            same += 1
            continue

        if args.dry_run:
            print(f"  {'would update' if existed else 'would add   '}  {path}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            print(f"  {'updated' if existed else 'added  '}  {path}")
        updated += existed
        added += not existed

    print(
        f"\nDone: {added} added, {updated} updated, {same} unchanged"
        + (f", {failed} FAILED" if failed else "")
    )
    if failed:
        print("Some files failed -- re-run `python update.py` to retry.")
        return 1

    if (added or updated) and not args.dry_run:
        for line in restart_notice(running_parts(ui_port())):
            print(line)

    print("\nNext steps:")
    print("  python main.py --status        # check everything is healthy")
    print("  python main.py --wake --my-voice   # test in the foreground")
    return 0


if __name__ == "__main__":
    sys.exit(main())
