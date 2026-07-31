"""Read and summarise documents held in a GitHub repository (or locally).

Examples:
    "read the renewable energy ppt in project-beastt"
    "summarise report.pdf from my notes repo"
    "what documents are in project-beastt"
    "read beastt_output/plan.docx"          (a local file)

Files are found by fuzzy name match against the repository tree, downloaded,
converted to text by beastt.readers, and summarised by the local model.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from .base import Skill

_READ = re.compile(
    r"\b(?:read|open|summari[sz]e|summary\s+of|tell\s+me\s+about|what(?:'s|\s+is)\s+in|"
    r"go\s+through|look\s+at|review|explain)\b",
    re.IGNORECASE,
)
_LIST_DOCS = re.compile(
    r"\b(?:list|show|what)\b[^.?!]*\b(?:documents?|docs?|files?|ppts?|pdfs?|"
    r"presentations?|reports?|sheets?)\b",
    re.IGNORECASE,
)
# A filename with an extension we can read.
_FILENAME = re.compile(
    r"([\w./-]+\.(?:pdf|docx?|pptx?|xlsx?|csv|json|txt|md|py|js|html|css|ya?ml|sql))\b",
    re.IGNORECASE,
)
# "... in <repo>" / "from my <repo> repo"
_IN_REPO = re.compile(
    r"\b(?:in|from|inside|on)\s+(?:my\s+|the\s+)?([\w.-]+(?:/[\w.-]+)?)\s*(?:repo|repository)?\s*$",
    re.IGNORECASE,
)
_DOC_WORDS = re.compile(
    r"\b(ppt|powerpoint|presentation|deck|pdf|word|document|doc|report|excel|"
    r"spreadsheet|sheet|workbook|file)\b",
    re.IGNORECASE,
)
_STOPWORDS = {
    "repo", "repository", "my", "the", "it", "that", "this", "there", "github",
    "document", "documents", "file", "files", "doc", "docs",
}

_SUMMARY_PROMPT = """Summarise this document for {user}.

Document: {name} ({note}){cut}

---
{body}
---

Write a clear summary: what it covers, the key points, and anything notable.
Use a short paragraph followed by a few bullet points. Plain text, no markdown
headings. Be specific -- quote figures and names where they appear.
"""


class ReadSkill(Skill):
    name = "read"

    def __init__(self, config, brain_provider):
        self.config = config
        self._brain_provider = brain_provider
        self._client = None
        self.last_text = None
        self.last_name = None

    # --- routing ----------------------------------------------------------
    def matches(self, text: str) -> bool:
        if _LIST_DOCS.search(text) and (_IN_REPO.search(text) or "repo" in text.lower()):
            return True
        if not _READ.search(text):
            return False
        # Needs something document-ish to act on, or it would swallow ordinary
        # requests like "tell me about solar power".
        return bool(_FILENAME.search(text) or (_DOC_WORDS.search(text) and _IN_REPO.search(text)))

    def run(self, text: str) -> str:
        from ..github_client import GitHubError

        try:
            if _LIST_DOCS.search(text) and not _FILENAME.search(text):
                return self._list(text)
            return self._read(text)
        except GitHubError as exc:
            return str(exc)
        except Exception as exc:
            return f"I couldn't read that ({exc.__class__.__name__}: {exc})."

    # --- helpers ----------------------------------------------------------
    def _get_client(self):
        if self._client is None:
            from ..github_client import GitHubClient

            self._client = GitHubClient(self.config.github_token)
        return self._client

    def _repo_from(self, text: str) -> str:
        match = _IN_REPO.search(text)
        if match:
            candidate = match.group(1).strip()
            if candidate.lower() not in _STOPWORDS and not _FILENAME.match(candidate):
                return candidate
        return self.config.github_repo

    def _tree(self, repo: str) -> List[str]:
        """Every readable file path in the repository."""
        from ..readers import supported

        client = self._get_client()
        branch = client.default_branch(repo)
        resp = client._request(
            "GET", f"/repos/{client._full(repo)}/git/trees/{branch}",
            params={"recursive": "1"},
        )
        if not resp.ok:
            from ..github_client import GitHubError

            raise GitHubError(f"Couldn't list {repo} ({resp.status_code}).")
        return [
            node["path"] for node in resp.json().get("tree", [])
            if node.get("type") == "blob" and supported(node["path"])
        ]

    def _list(self, text: str) -> str:
        repo = self._repo_from(text)
        if not repo:
            return "Which repository should I look in?"
        paths = self._tree(repo)
        if not paths:
            return f"I couldn't find any readable documents in {repo}."

        # Group by type so a long list stays scannable.
        groups: dict = {}
        for path in paths:
            groups.setdefault(Path(path).suffix.lower(), []).append(path)
        lines = [f"Readable documents in {repo}:"]
        for suffix in sorted(groups):
            files = groups[suffix]
            lines.append(f"  {suffix} ({len(files)}):")
            for path in files[:6]:
                lines.append(f"    {path}")
            if len(files) > 6:
                lines.append(f"    ...and {len(files) - 6} more")
        lines.append("\nSay \"read <filename>\" and I'll go through it.")
        return "\n".join(lines)

    def _match_file(self, paths: List[str], wanted: str) -> Optional[str]:
        """Find the closest path to what was asked for."""
        wanted = wanted.strip().lower().replace("\\", "/")
        if not wanted:
            return None
        # Exact, then endswith, then all words present in the path.
        for path in paths:
            if path.lower() == wanted:
                return path
        for path in paths:
            if path.lower().endswith("/" + wanted) or Path(path).name.lower() == wanted:
                return path
        words = [w for w in re.split(r"[\s_./-]+", wanted) if len(w) > 2]
        scored = []
        for path in paths:
            low = path.lower()
            hits = sum(1 for w in words if w in low)
            if hits:
                scored.append((hits, -len(path), path))
        if scored:
            scored.sort(reverse=True)
            return scored[0][2]
        return None

    def _local_file(self, name: str) -> Optional[Path]:
        from ..paths import project_root

        candidate = Path(name)
        roots = [project_root(), project_root() / "beastt_output",
                 project_root() / "beastt_workspace"]
        if candidate.is_absolute() and candidate.is_file():
            return candidate
        for root in roots:
            target = root / candidate
            if target.is_file():
                return target
            matches = list(root.glob(f"**/{candidate.name}"))
            if matches:
                return matches[0]
        return None

    # --- reading ----------------------------------------------------------
    def _read(self, text: str) -> str:
        from ..readers import Unsupported, extract

        match = _FILENAME.search(text)
        wanted = match.group(1) if match else ""

        # A local file wins if it exists -- no point going to the network.
        if wanted:
            local = self._local_file(wanted)
            if local is not None:
                try:
                    body, note, cut = extract(local.name, local.read_bytes())
                except Unsupported as exc:
                    return str(exc)
                return self._summarise(local.name, note, body, cut, source=str(local))

        repo = self._repo_from(text)
        if not repo:
            return (
                "Which repository is it in? You can also set a default with "
                "BEASTT_GITHUB_REPO in .env."
            )

        client = self._get_client()
        if not client.configured:
            return (
                "I need GitHub access for that. Add BEASTT_GITHUB_TOKEN to your .env "
                "(a token with 'repo' scope from github.com/settings/tokens)."
            )

        paths = self._tree(repo)
        if not paths:
            return f"I couldn't find any readable documents in {repo}."

        if not wanted:
            # No filename given: use the document words as search terms.
            hint = _DOC_WORDS.sub(" ", text)
            hint = _READ.sub(" ", hint)
            hint = _IN_REPO.sub(" ", hint)
            wanted = " ".join(w for w in hint.split() if w.lower() not in _STOPWORDS)

        path = self._match_file(paths, wanted)
        if path is None:
            preview = "\n".join(f"  {p}" for p in paths[:8])
            return (
                f"I couldn't find that in {repo}. Here's what's there:\n{preview}\n"
                "Say \"read <filename>\"."
            )

        print(f"[read] Fetching {path} from {repo}...")
        data, _sha = client.read_file(repo, path)
        try:
            body, note, cut = extract(Path(path).name, data)
        except Unsupported as exc:
            return str(exc)
        return self._summarise(Path(path).name, note, body, cut,
                              source=f"{repo}/{path}")

    def _summarise(self, name: str, note: str, body: str, truncated: bool,
                   source: str) -> str:
        from ..brain.base import Message

        self.last_text, self.last_name = body, name
        print(f"[read] {name}: {note}{' (truncated)' if truncated else ''} -- summarising...")

        prompt = _SUMMARY_PROMPT.format(
            user=self.config.user_name, name=name, note=note,
            cut=" -- only the first part is shown" if truncated else "",
            body=body,
        )
        try:
            summary = self._brain_provider().reply([Message(role="user", content=prompt)])
        except Exception as exc:
            return (
                f"I read {name} ({note}) but couldn't summarise it "
                f"({exc.__class__.__name__}). Here's how it starts:\n\n{body[:600]}"
            )
        summary = (summary or "").strip()
        if not summary:
            return f"I read {name} ({note}) but couldn't make a summary of it."
        return f"{name} -- {note}, from {source}\n\n{summary}"
