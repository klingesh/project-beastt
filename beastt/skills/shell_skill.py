"""Run commands and manage local clones of repositories.

Examples:
    "run git status"
    "clone my notes repo"
    "pull the latest in project-beastt"

Commands are vetted by beastt.shell: destructive ones are refused, read-only ones
run immediately, and anything else is quoted back for confirmation before it
runs. Confirmation is always required in voice mode, where a misheard word could
otherwise be executed.
"""

from __future__ import annotations

import re

from .base import Skill
from .intent import directive

_RUN = re.compile(
    r"^\s*(?:hey\s+\w+[,\s]+)?(?:please\s+)?"
    r"(?:run|execute|exec)\b\s*(?:the\s+)?(?:command\s+)?[\"']?(.+?)[\"']?\s*$",
    re.IGNORECASE,
)
_CLONE = re.compile(
    r"\b(?:clone|download|get|fetch|check\s*out)\b[^.?!]*?\b(?:repo|repository)?\s*"
    r"[\"']?([\w.-]+(?:/[\w.-]+)?)[\"']?\s*(?:repo|repository)?\s*$",
    re.IGNORECASE,
)
_CLONE_INTENT = re.compile(r"\b(clone|check\s*out)\b", re.IGNORECASE)
_PULL = re.compile(
    r"\b(?:pull|update|sync|refresh)\b[^.?!]*?\b(?:in|for|from|the)?\s*"
    r"[\"']?([\w.-]+(?:/[\w.-]+)?)[\"']?\s*(?:repo|repository)?\s*$",
    re.IGNORECASE,
)
_PULL_INTENT = re.compile(r"\b(pull|sync)\b", re.IGNORECASE)
_YES = re.compile(r"^\s*(yes|yep|yeah|yup|do it|go ahead|confirm|ok(ay)?|sure|please do)\b",
                  re.IGNORECASE)
_NO = re.compile(r"^\s*(no|nope|cancel|stop|don'?t|never\s?mind|forget it)\b", re.IGNORECASE)
_STOPWORDS = {"repo", "repository", "it", "this", "that", "the", "my", "latest", "changes"}


class ShellSkill(Skill):
    name = "shell"

    def __init__(self, config, voice_mode_provider=None):
        self.config = config
        # Confirmation is mandatory when input comes from the microphone.
        self._voice_mode = voice_mode_provider or (lambda: False)
        self._pending = None      # command awaiting a yes/no

    def matches(self, text: str) -> bool:
        if self._pending and (_YES.match(text) or _NO.match(text)):
            return True
        # _RUN is anchored, so it is already safe. Cloning and pulling are not,
        # and both touch the disk -- a repo name mentioned inside a pasted
        # document must not start a clone.
        return bool(
            _RUN.match(text)
            or (directive(_CLONE_INTENT, text) and _CLONE.search(text))
            or (directive(_PULL_INTENT, text) and _PULL.search(text))
        )

    def run(self, text: str) -> str:
        # Resolving a pending confirmation.
        if self._pending:
            if _NO.match(text):
                command = self._pending
                self._pending = None
                return f"Alright, I won't run `{command}`."
            if _YES.match(text):
                command = self._pending
                self._pending = None
                return self._execute(command)

        if _CLONE_INTENT.search(text):
            match = _CLONE.search(text)
            if match:
                return self._clone(match.group(1))

        if _PULL_INTENT.search(text):
            match = _PULL.search(text)
            if match:
                return self._pull(match.group(1))

        match = _RUN.match(text)
        if match:
            return self._request(match.group(1).strip())
        return ""

    # --- commands ---------------------------------------------------------
    def _request(self, command: str) -> str:
        from ..shell import Blocked, check

        if not self.config.shell_enabled:
            return (
                "Running commands is switched off. Set BEASTT_SHELL=on in .env if "
                "you want me to be able to."
            )

        verdict, reason = check(command)
        if verdict == "blocked":
            return (
                f"No -- I won't run that: it involves {reason}. "
                "That's off limits even if you confirm."
            )
        if verdict == "allowed" and not self._voice_mode():
            return self._execute(command)

        # Confirm: spoken input, or anything not read-only.
        self._pending = command
        why = "I'd rather check first since " + reason if verdict == "confirm" else \
              "I always confirm commands when we're talking out loud"
        return f"{why}. Run this?\n  {command}\nSay yes or no."

    def _execute(self, command: str) -> str:
        from ..shell import Blocked, run

        print(f"[shell] $ {command}")
        try:
            output = run(command)
        except Blocked as exc:
            return f"Refused: that involves {exc}."
        return f"$ {command}\n{output}"

    # --- repositories -----------------------------------------------------
    def _clean_repo(self, name: str) -> str:
        name = str(name or "").strip().strip("\"'")
        words = [w for w in re.split(r"\s+", name) if w.lower() not in _STOPWORDS]
        return words[-1] if words else ""

    def _clone(self, name: str) -> str:
        from ..git_ops import clone, pull

        repo = self._clean_repo(name)
        if not repo:
            return "Which repository should I clone?"

        owner = ""
        if "/" in repo:
            owner, repo = repo.split("/", 1)
        if not owner:
            # Work out the owner from the token, falling back to the repo string.
            try:
                from ..github_client import GitHubClient

                client = GitHubClient(self.config.github_token)
                owner = client.login() if client.configured else ""
            except Exception:
                owner = ""
        if not owner:
            return (
                f"I need to know who owns {repo}. Try \"clone owner/{repo}\", or add "
                "BEASTT_GITHUB_TOKEN to .env so I can look it up."
            )

        print(f"[git] Cloning {owner}/{repo}...")
        ok, message, path = clone(owner, repo, token=self.config.github_token)
        if ok and path and "already here" in message:
            ok2, pulled = pull(repo, token=self.config.github_token)
            return f"{message}\n{pulled}"
        return message if ok else f"Couldn't clone it: {message}"

    def _pull(self, name: str) -> str:
        from ..git_ops import pull

        repo = self._clean_repo(name)
        if not repo:
            return "Which repository should I update?"
        print(f"[git] Pulling {repo}...")
        ok, message = pull(repo, token=self.config.github_token)
        return message if ok else f"Couldn't update it: {message}"
