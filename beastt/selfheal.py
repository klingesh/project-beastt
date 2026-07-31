"""Self-diagnosis and repair.

Deliberately *not* an LLM editing its own source: a model rewriting the code it's
running from can break the assistant with no way back. Instead this handles the
failures that actually happen in practice, each with a known, deterministic fix:

  * a missing Python package            -> offer to pip install it
  * Ollama not running                  -> offer to start it
  * the configured model not pulled     -> offer to pull it
  * missing data folders                -> create them
  * a corrupt memory file               -> back it up and start fresh
  * a stale voiceprint                  -> explain how to re-enrol

Anything needing a real code change is reported clearly, with the traceback, so
it can be fixed properly rather than guessed at.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .config import Config
from .paths import data_dir, resolve

ERROR_LOG = "errors.log"
_MAX_ERRORS = 60


# --- error recording --------------------------------------------------------
def record(exc: BaseException, context: str = "") -> None:
    """Append a failure to the error log so it can be diagnosed later."""
    try:
        path = data_dir() / ERROR_LOG
        entry = {
            "when": time.strftime("%Y-%m-%d %H:%M:%S"),
            "context": context,
            "type": exc.__class__.__name__,
            "message": str(exc)[:400],
            "traceback": "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )[-2000:],
        }
        lines = []
        if path.exists():
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        lines.append(json.dumps(entry))
        path.write_text("\n".join(lines[-_MAX_ERRORS:]) + "\n", encoding="utf-8")
    except Exception:
        pass      # never let error logging cause an error


def recent_errors(limit: int = 5) -> List[dict]:
    path = data_dir() / ERROR_LOG
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def clear_errors() -> int:
    path = data_dir() / ERROR_LOG
    if not path.exists():
        return 0
    count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    path.unlink()
    return count


# --- checks -----------------------------------------------------------------
@dataclass
class Finding:
    name: str
    ok: bool
    detail: str
    fix: Optional[Callable[[], str]] = None      # returns a result message
    fix_label: str = ""

    @property
    def fixable(self) -> bool:
        return self.fix is not None and not self.ok


def _pip_install(package: str) -> Callable[[], str]:
    def apply() -> str:
        try:
            done = subprocess.run(
                [sys.executable, "-m", "pip", "install", package],
                capture_output=True, text=True, timeout=600,
            )
        except Exception as exc:
            return f"couldn't install {package} ({exc.__class__.__name__})"
        if done.returncode == 0:
            return f"installed {package}"
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or [""]
        return f"failed to install {package}: {tail[0][:120]}"

    return apply


def _ollama_pull(model: str) -> Callable[[], str]:
    def apply() -> str:
        try:
            done = subprocess.run(
                ["ollama", "pull", model], capture_output=True, text=True, timeout=1800
            )
        except FileNotFoundError:
            return "Ollama isn't installed -- get it from https://ollama.com/download"
        except Exception as exc:
            return f"couldn't pull {model} ({exc.__class__.__name__})"
        return f"pulled {model}" if done.returncode == 0 else f"failed to pull {model}"

    return apply


def _start_ollama() -> str:
    try:
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creation,
        )
    except FileNotFoundError:
        return "Ollama isn't installed -- get it from https://ollama.com/download"
    except Exception as exc:
        return f"couldn't start Ollama ({exc.__class__.__name__})"
    # Give the server a moment, then confirm.
    for _ in range(10):
        time.sleep(1)
        if _ollama_up(Config.load()):
            return "started Ollama"
    return "started Ollama, but it isn't answering yet -- give it a few seconds"


def _ollama_up(config: Config) -> bool:
    try:
        import requests

        return requests.get(f"{config.ollama_url}/api/tags", timeout=3).status_code == 200
    except Exception:
        return False


def _reset_memory(path: Path) -> Callable[[], str]:
    def apply() -> str:
        try:
            backup = path.with_suffix(".json.broken")
            shutil.copy2(path, backup)
            path.write_text('{"facts": []}', encoding="utf-8")
            return f"reset the memory file (old one kept as {backup.name})"
        except Exception as exc:
            return f"couldn't reset memory ({exc.__class__.__name__})"

    return apply


def _make_dirs() -> str:
    data_dir()
    (resolve("beastt_output")).mkdir(parents=True, exist_ok=True)
    (resolve("beastt_workspace")).mkdir(parents=True, exist_ok=True)
    return "created the data folders"


# Optional features and the package each needs.
_FEATURES = [
    ("speech output", "pyttsx3", "pyttsx3"),
    ("microphone", "sounddevice", "sounddevice"),
    ("speech recognition", "whisper", "openai-whisper"),
    ("PowerPoint", "pptx", "python-pptx"),
    ("Word", "docx", "python-docx"),
    ("Excel", "openpyxl", "openpyxl"),
    ("PDF reading", "pypdf", "pypdf"),
]


def diagnose(config: Optional[Config] = None) -> List[Finding]:
    """Check everything that commonly breaks, with a fix where one exists."""
    config = config or Config.load()
    findings: List[Finding] = []

    # 1. Core dependencies -- without these nothing works.
    for module, package in (("requests", "requests"), ("dotenv", "python-dotenv")):
        try:
            importlib.import_module(module)
            findings.append(Finding(f"core: {package}", True, "installed"))
        except Exception:
            findings.append(Finding(
                f"core: {package}", False, "missing",
                _pip_install(package), f"install {package}",
            ))

    # 2. Optional features.
    for label, module, package in _FEATURES:
        try:
            importlib.import_module(module)
            findings.append(Finding(label, True, "available"))
        except Exception:
            findings.append(Finding(
                label, False, f"needs {package}",
                _pip_install(package), f"install {package}",
            ))

    # 3. The brain.
    if _ollama_up(config):
        findings.append(Finding("Ollama", True, "running"))
        try:
            import requests

            tags = requests.get(f"{config.ollama_url}/api/tags", timeout=5).json()
            names = {m.get("name", "").split(":")[0] for m in tags.get("models", [])}
            wanted = config.model.split(":")[0]
            if wanted in names:
                findings.append(Finding(f"model {config.model}", True, "pulled"))
            else:
                findings.append(Finding(
                    f"model {config.model}", False, "not pulled",
                    _ollama_pull(config.model), f"pull {config.model}",
                ))
        except Exception as exc:
            findings.append(Finding("model list", False, f"couldn't read ({exc.__class__.__name__})"))
    else:
        findings.append(Finding(
            "Ollama", False, f"not answering on {config.ollama_url}",
            _start_ollama, "start Ollama",
        ))

    # 4. Data folders.
    missing = [
        name for name in ("beastt_memory", "beastt_output", "beastt_workspace")
        if not resolve(name).exists()
    ]
    if missing:
        findings.append(Finding(
            "data folders", False, f"missing {', '.join(missing)}", _make_dirs,
            "create them",
        ))
    else:
        findings.append(Finding("data folders", True, "present"))

    # 5. Long-term memory readable?
    mem = resolve(config.memory_path)
    if mem.exists():
        try:
            json.loads(mem.read_text(encoding="utf-8"))
            findings.append(Finding("memory file", True, "readable"))
        except Exception:
            findings.append(Finding(
                "memory file", False, "corrupt", _reset_memory(mem),
                "reset it (keeping a backup)",
            ))

    # 6. GitHub token, if one is configured.
    if config.github_token:
        try:
            from .github_client import GitHubClient

            login = GitHubClient(config.github_token).login()
            findings.append(Finding("GitHub token", True, f"valid ({login})"))
        except Exception as exc:
            findings.append(Finding("GitHub token", False, str(exc)[:110]))

    return findings


def summarise(findings: List[Finding]) -> str:
    problems = [f for f in findings if not f.ok]
    if not problems:
        return f"All {len(findings)} checks passed -- everything's healthy."
    lines = [f"{len(problems)} thing(s) need attention:"]
    for finding in problems:
        suffix = f"  [I can {finding.fix_label}]" if finding.fixable else "  [needs you]"
        lines.append(f"  - {finding.name}: {finding.detail}{suffix}")
    return "\n".join(lines)


def repair(findings: List[Finding]) -> str:
    """Apply every available fix, reporting what happened."""
    fixable = [f for f in findings if f.fixable]
    if not fixable:
        return "There's nothing I can fix automatically."
    results = []
    for finding in fixable:
        print(f"[heal] {finding.fix_label}...")
        try:
            results.append(f"  - {finding.fix()}")
        except Exception as exc:
            results.append(f"  - {finding.name}: failed ({exc.__class__.__name__})")
    return "Here's what I did:\n" + "\n".join(results)
