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
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OWNER = "klingesh"
REPO = "project-beastt"
DEFAULT_BRANCH = "feat/beastt-ai-companion"

# Only code/config is synced; anything personal stays local.
SKIP_EXACT = {".env"}
SKIP_PREFIX = ("beastt_memory/", ".git/")

ROOT = Path(__file__).resolve().parent


def _get(url: str, as_json: bool = False):
    req = urllib.request.Request(
        url, headers={"User-Agent": "beastt-updater", "Accept": "*/*"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    return json.loads(data) if as_json else data


def _wanted(path: str) -> bool:
    if path in SKIP_EXACT:
        return False
    if any(path.startswith(p) for p in SKIP_PREFIX):
        return False
    # Everything else that's source or config.
    return path.endswith((".py", ".txt", ".md", ".example", ".gitignore"))


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Update BEASTT from GitHub.")
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    print(f"Updating BEASTT from {OWNER}/{REPO} ({args.branch})...\n")

    try:
        files = list_files(args.branch)
    except urllib.error.HTTPError as exc:
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
            content = _get(raw_url(args.branch, path))
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

    print("\nNext steps:")
    print("  python main.py --status        # check everything is healthy")
    print("  python main.py --wake --my-voice   # test in the foreground")
    return 0


if __name__ == "__main__":
    sys.exit(main())
