"""Write code and scaffold projects on request.

Examples:
    "write a python script that renames files by date"
    "make an html page for my portfolio"
    "create a flask project called notes-api"
    "what project layouts can you do"
"""

from __future__ import annotations

import re

from .base import Skill

_VERB = r"(?:write|make|create|generate|build|code|scaffold|set\s+up|start|give\s+me)"

# A code request: a making verb plus a language or code-ish noun.
_CODE = re.compile(
    rf"\b{_VERB}\b[^.?!]*\b(python|py|script|html|web\s?page|website|css|javascript|js|"
    r"typescript|sql|query|bash|shell\s+script|powershell|batch|program|function|class|"
    r"api|regex|snippet|code)\b",
    re.IGNORECASE,
)
# A project request: a making verb plus a scaffold name or the word project.
_PROJECT = re.compile(
    rf"\b{_VERB}\b[^.?!]*\b(project|app|application|package|flask|fastapi|django|"
    r"static\s+site|cli|command\s+line)\b",
    re.IGNORECASE,
)
_LIST = re.compile(
    r"\b(?:list|show|what|which)\b[^.?!]*\b(?:project\s+)?(?:layouts?|scaffolds?|templates?)\b",
    re.IGNORECASE,
)
_NAME = re.compile(
    r"\b(?:called|named|name\s+it)\s+[\"']?([\w.-]+)[\"']?", re.IGNORECASE
)
_SCAFFOLD_WORDS = (
    ("flask", "flask"),
    ("fastapi", "fastapi"),
    ("fast api", "fastapi"),
    ("static site", "static-site"),
    ("static website", "static-site"),
    ("website", "static-site"),
    ("web page", "static-site"),
    ("html site", "static-site"),
    ("package", "python-package"),
    ("library", "python-package"),
    ("cli", "python-cli"),
    ("command line", "python-cli"),
)


class CodeSkill(Skill):
    name = "code"

    def __init__(self, brain_provider, on_created=None):
        self._brain_provider = brain_provider
        self._on_created = on_created
        self.last_path = None

    def matches(self, text: str) -> bool:
        return bool(_LIST.search(text) or _PROJECT.search(text) or _CODE.search(text))

    def run(self, text: str) -> str:
        from ..code import SCAFFOLDS, generate, list_scaffolds, scaffold

        if _LIST.search(text):
            return (
                "Project layouts I can scaffold:\n" + list_scaffolds() +
                "\n\nSay something like \"create a flask project called notes-api\"."
            )

        # A scaffold request needs a recognised template name.
        kind = self._scaffold_kind(text)
        if kind:
            name = self._project_name(text, kind)
            root, files = scaffold(kind, name)
            if root is None:
                return f"I don't have a {kind} layout."
            self.last_path = root
            listing = "\n".join(f"  {f}" for f in files[:12])
            return (
                f"Created a {kind} project called {root.name} ({len(files)} files):\n"
                f"{listing}\n"
                f"Folder: {root}\n"
                f"{SCAFFOLDS[kind][0]}."
            )

        # Otherwise: a single generated file.
        print("[code] Writing that for you...")
        result = generate(self._brain_provider(), text)
        if not result:
            return (
                "I couldn't get clean code out of that. Try being specific about the "
                "language and what it should do -- for example \"write a python "
                "script that renames files by their modified date\"."
            )

        self.last_path = result["path"]
        if self._on_created:
            try:
                self._on_created(result["path"])
            except Exception:
                pass
        note = f"\n{result['explanation']}" if result["explanation"] else ""
        return (
            f"Done -- {result['lines']} lines of {result['language']}.\n"
            f"Saved to: {result['path']}{note}\n"
            "Say \"push it to GitHub\" if you'd like it uploaded."
        )

    # --- helpers ----------------------------------------------------------
    def _scaffold_kind(self, text: str):
        lowered = text.lower()
        if not _PROJECT.search(text):
            return None
        for phrase, kind in _SCAFFOLD_WORDS:
            if phrase in lowered:
                return kind
        # "create a python project" with no other hint.
        if re.search(r"\bpython\b", lowered):
            return "python-cli"
        return None

    def _project_name(self, text: str, kind: str) -> str:
        match = _NAME.search(text)
        if match:
            return match.group(1)
        # Fall back to words after "project"/"app".
        match = re.search(r"\b(?:project|app|package|site)\s+(?:for\s+)?([\w -]{3,40})",
                          text, re.IGNORECASE)
        if match:
            words = match.group(1).strip().split()
            return "-".join(w.lower() for w in words[:3])
        return kind
