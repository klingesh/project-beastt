"""Minimal GitHub client so the assistant can read and push files.

Uses the REST API directly through `requests` (already a core dependency), so
there's nothing extra to install. Authentication is a personal access token read
from the environment -- it is never printed or stored anywhere by this module.

Create a token at https://github.com/settings/tokens with `repo` scope, then put
it in .env as BEASTT_GITHUB_TOKEN.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str, timeout: int = 30):
        self.token = (token or "").strip()
        self.timeout = timeout
        self._login: Optional[str] = None

    # --- plumbing ---------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "beastt-assistant",
        }

    def _request(self, method: str, path: str, **kwargs):
        if not self.configured:
            raise GitHubError(
                "No GitHub token set. Create one at https://github.com/settings/tokens "
                "(scope: repo) and add BEASTT_GITHUB_TOKEN to your .env file."
            )
        url = path if path.startswith("http") else f"{API}{path}"
        resp = requests.request(
            method, url, headers=self._headers(), timeout=self.timeout, **kwargs
        )
        if resp.status_code == 401:
            raise GitHubError("GitHub rejected the token (401). Is it valid and unexpired?")
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            raise GitHubError("GitHub rate limit reached; try again shortly.")
        return resp

    # --- identity ---------------------------------------------------------
    def login(self) -> str:
        """The authenticated username (cached)."""
        if self._login:
            return self._login
        resp = self._request("GET", "/user")
        if not resp.ok:
            raise GitHubError(f"Couldn't identify you on GitHub ({resp.status_code}).")
        self._login = resp.json().get("login", "")
        return self._login

    # --- repositories -----------------------------------------------------
    def list_repos(self, limit: int = 30) -> List[Dict]:
        resp = self._request(
            "GET", "/user/repos", params={"per_page": min(limit, 100), "sort": "updated"}
        )
        if not resp.ok:
            raise GitHubError(f"Couldn't list repositories ({resp.status_code}).")
        return [
            {"name": r["name"], "full_name": r["full_name"], "private": r["private"],
             "url": r["html_url"], "default_branch": r.get("default_branch", "main")}
            for r in resp.json()
        ]

    def create_repo(self, name: str, private: bool = True, description: str = "") -> Dict:
        resp = self._request(
            "POST", "/user/repos",
            json={"name": name, "private": private, "description": description,
                  "auto_init": True},
        )
        if resp.status_code == 422:
            raise GitHubError(f"A repository named '{name}' already exists.")
        if not resp.ok:
            raise GitHubError(f"Couldn't create the repository ({resp.status_code}).")
        data = resp.json()
        return {"full_name": data["full_name"], "url": data["html_url"],
                "default_branch": data.get("default_branch", "main")}

    def default_branch(self, repo: str) -> str:
        resp = self._request("GET", f"/repos/{self._full(repo)}")
        if not resp.ok:
            raise GitHubError(f"Couldn't find the repository '{repo}' ({resp.status_code}).")
        return resp.json().get("default_branch", "main")

    # --- contents ---------------------------------------------------------
    def list_path(self, repo: str, path: str = "") -> List[Dict]:
        resp = self._request("GET", f"/repos/{self._full(repo)}/contents/{path.strip('/')}")
        if resp.status_code == 404:
            raise GitHubError(f"'{path or '/'}' not found in {repo}.")
        if not resp.ok:
            raise GitHubError(f"Couldn't list '{path}' ({resp.status_code}).")
        data = resp.json()
        items = data if isinstance(data, list) else [data]
        return [{"name": i["name"], "path": i["path"], "type": i["type"],
                 "size": i.get("size", 0)} for i in items]

    def read_file(self, repo: str, path: str) -> Tuple[bytes, str]:
        """Return (content_bytes, sha) for a file."""
        resp = self._request("GET", f"/repos/{self._full(repo)}/contents/{path.strip('/')}")
        if resp.status_code == 404:
            raise GitHubError(f"'{path}' not found in {repo}.")
        if not resp.ok:
            raise GitHubError(f"Couldn't read '{path}' ({resp.status_code}).")
        data = resp.json()
        if data.get("encoding") != "base64":
            raise GitHubError(f"'{path}' isn't a regular file.")
        return base64.b64decode(data["content"]), data["sha"]

    def _existing_sha(self, repo: str, path: str, branch: str) -> Optional[str]:
        resp = self._request(
            "GET", f"/repos/{self._full(repo)}/contents/{path.strip('/')}",
            params={"ref": branch},
        )
        if resp.ok:
            data = resp.json()
            if isinstance(data, dict):
                return data.get("sha")
        return None

    def push_file(
        self,
        repo: str,
        local_path: Path,
        remote_path: Optional[str] = None,
        message: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> Dict:
        """Create or update a file in a repository. Returns {url, path, updated}."""
        local_path = Path(local_path)
        if not local_path.exists():
            raise GitHubError(f"Local file not found: {local_path}")

        full = self._full(repo)
        branch = branch or self.default_branch(repo)
        remote = (remote_path or f"jarvis/{local_path.name}").strip("/")
        content = base64.b64encode(local_path.read_bytes()).decode("ascii")

        payload = {
            "message": message or f"Add {local_path.name} (created by your assistant)",
            "content": content,
            "branch": branch,
        }
        # Updating an existing file requires its current blob sha.
        sha = self._existing_sha(repo, remote, branch)
        if sha:
            payload["sha"] = sha

        resp = self._request("PUT", f"/repos/{full}/contents/{remote}", json=payload)
        if not resp.ok:
            detail = ""
            try:
                detail = resp.json().get("message", "")
            except Exception:
                pass
            raise GitHubError(f"Push failed ({resp.status_code}) {detail}".strip())

        data = resp.json()
        return {
            "url": data.get("content", {}).get("html_url", ""),
            "path": remote,
            "repo": full,
            "branch": branch,
            "updated": bool(sha),
        }

    # --- helpers ----------------------------------------------------------
    def _full(self, repo: str) -> str:
        """Accept either 'name' or 'owner/name'."""
        repo = repo.strip().strip("/")
        if "/" in repo:
            return repo
        return f"{self.login()}/{repo}"


def guess_mime(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"
