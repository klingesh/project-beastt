"""Talk to GitHub in plain language.

Examples:
    "push it to github"                 (the last document created)
    "upload the ppt to my notes repo"
    "list my repos"
    "what files are in project-beastt"

Kept deliberately conservative: it only ever creates or updates a single file
under a dedicated folder, and never deletes or force-pushes anything.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import Skill

_PUSH = re.compile(
    r"\b(push|upload|commit|send|save)\b[^.?!]*\b(git\s?hub|repo|repository)\b", re.IGNORECASE
)
_LIST_REPOS = re.compile(
    r"\b(list|show|what are)\b[^.?!]*\b(my )?(repos|repositories)\b", re.IGNORECASE
)
_LIST_FILES = re.compile(
    r"\b(list|show|what)\b[^.?!]*\bfiles\b[^.?!]*?\b(?:in|of|from)\s+([\w.\-\/]+)", re.IGNORECASE
)
# "to my notes repo", "to klingesh/notes", "in the beastt repo"
_TARGET = re.compile(
    r"\b(?:to|into|in)\s+(?:my\s+|the\s+)?([\w.\-]+(?:/[\w.\-]+)?)\s*(?:repo|repository)?\b",
    re.IGNORECASE,
)
_STOPWORDS = {"github", "git", "hub", "repo", "repository", "my", "the", "it", "this", "that"}


class GitHubSkill(Skill):
    name = "github"

    def __init__(self, config, last_file_provider):
        self.config = config
        # Callable returning the most recently created document, if any.
        self._last_file = last_file_provider
        self._client = None
        # When we've asked "which repo?", the candidate list is held here so the
        # user's next message (a name or a number) can be resolved against it.
        self._pending_choice = None   # {"file": Path, "repos": [names]}

    # --- lifecycle --------------------------------------------------------
    def _get_client(self):
        if self._client is None:
            from ..github_client import GitHubClient

            self._client = GitHubClient(self.config.github_token)
        return self._client

    def matches(self, text: str) -> bool:
        # While waiting for a repo choice, claim short replies so "the second one"
        # or "notes" is understood as an answer rather than sent to the LLM.
        if self._pending_choice and len(text.split()) <= 8:
            return True
        return bool(_LIST_REPOS.search(text) or _LIST_FILES.search(text) or _PUSH.search(text))

    # --- actions ----------------------------------------------------------
    def run(self, text: str) -> str:
        from ..github_client import GitHubError

        client = self._get_client()
        if not client.configured:
            return (
                "I don't have GitHub access yet. Create a token at "
                "github.com/settings/tokens with 'repo' scope, then add this to your "
                ".env file:\n  BEASTT_GITHUB_TOKEN=your_token_here"
            )

        try:
            # An answer to "which repo?" takes priority over everything else.
            if self._pending_choice:
                handled = self._resolve_pending(client, text)
                if handled is not None:
                    return handled
            if _LIST_REPOS.search(text):
                return self._list_repos(client)
            match = _LIST_FILES.search(text)
            if match:
                return self._list_files(client, match.group(2))
            return self._push(client, text)
        except GitHubError as exc:
            return str(exc)
        except Exception as exc:
            return f"Something went wrong talking to GitHub ({exc.__class__.__name__}: {exc})."

    def _list_repos(self, client) -> str:
        repos = client.list_repos()
        if not repos:
            return "You don't have any repositories yet."
        lines = [
            f"- {r['name']}{' (private)' if r['private'] else ''}" for r in repos[:15]
        ]
        extra = "" if len(repos) <= 15 else f"\n...and {len(repos) - 15} more."
        return "Here are your most recently updated repos:\n" + "\n".join(lines) + extra

    def _list_files(self, client, repo: str) -> str:
        items = client.list_path(repo)
        if not items:
            return f"{repo} looks empty."
        lines = []
        for item in items[:20]:
            marker = "/" if item["type"] == "dir" else ""
            lines.append(f"- {item['name']}{marker}")
        extra = "" if len(items) <= 20 else f"\n...and {len(items) - 20} more."
        return f"Top level of {repo}:\n" + "\n".join(lines) + extra

    def _explicit_target(self, text: str) -> str:
        """A repo named directly in the request, e.g. "push it to my notes repo"."""
        for match in _TARGET.finditer(text):
            candidate = match.group(1).strip()
            if candidate.lower() not in _STOPWORDS and len(candidate) > 1:
                return candidate
        return ""

    # --- choosing a repository -------------------------------------------
    _ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
                 "1st": 1, "2nd": 2, "3rd": 3, "one": 1, "two": 2, "three": 3}

    def _ask_which_repo(self, client, path: Path) -> str:
        """List the user's repos and remember that we're awaiting a choice."""
        repos = [r["name"] for r in client.list_repos(limit=20)]
        if not repos:
            return "You don't have any repositories to push to yet."

        self._pending_choice = {"file": path, "repos": repos}
        listing = "\n".join(f"  {i}. {name}" for i, name in enumerate(repos[:10], 1))
        default = self.config.github_repo
        hint = f"\n(Just say \"default\" for {default}.)" if default else ""
        return (
            f"Which repo should I push {path.name} to?\n{listing}\n"
            f"Say the name or the number.{hint}"
        )

    def _resolve_pending(self, client, text: str):
        """Interpret a reply to 'which repo?'. Returns a message, or None if unclear."""
        pending = self._pending_choice
        repos = pending["repos"]
        path = pending["file"]
        cleaned = re.sub(r"[^\w\s.\-/]", " ", text).strip()
        lowered = cleaned.lower()

        if any(word in lowered for word in ("cancel", "never mind", "nevermind", "forget it")):
            self._pending_choice = None
            return "No problem -- I won't push it."

        chosen = None
        if "default" in lowered and self.config.github_repo:
            chosen = self.config.github_repo

        # A number, or an ordinal word ("the second one").
        if chosen is None:
            match = re.search(r"\b(\d{1,2})\b", lowered)
            index = int(match.group(1)) if match else self._ORDINALS.get(lowered.strip())
            if index is None:
                for word, value in self._ORDINALS.items():
                    if re.search(rf"\b{word}\b", lowered):
                        index = value
                        break
            if index and 1 <= index <= len(repos):
                chosen = repos[index - 1]

        # An exact or partial repo name.
        if chosen is None:
            for name in repos:
                low = name.lower()
                if low in lowered or lowered in low or low.replace("-", " ") in lowered:
                    chosen = name
                    break

        if chosen is None:
            return None  # let the caller/LLM handle it; keep waiting

        self._pending_choice = None
        return self._do_push(client, path, chosen)

    def _push(self, client, text: str) -> str:
        path = self._last_file()
        if not path:
            return (
                "I don't have a file ready to push yet. Ask me to make a document "
                "first -- for example, \"make a ppt about renewable energy\"."
            )
        path = Path(path)

        # An explicit target in the request always wins.
        repo = self._explicit_target(text)
        if not repo:
            if self.config.github_ask or not self.config.github_repo:
                return self._ask_which_repo(client, path)
            repo = self.config.github_repo

        return self._do_push(client, path, repo)

    def _do_push(self, client, path: Path, repo: str) -> str:
        print(f"[github] Pushing {path.name} to {repo}...")
        result = client.push_file(
            repo,
            path,
            remote_path=f"{self.config.github_folder.strip('/')}/{path.name}"
            if self.config.github_folder
            else path.name,
            message=f"Add {path.name} (generated by {self.config.name})",
        )
        verb = "Updated" if result["updated"] else "Pushed"
        return (
            f"{verb} {path.name} to {result['repo']} on branch {result['branch']}.\n"
            f"{result['url']}"
        )
