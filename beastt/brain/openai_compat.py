"""A brain for any OpenAI-compatible chat API.

One class covers a lot of ground. GitHub Models, Groq, Cerebras, OpenRouter,
Mistral and OpenAI itself all speak the same `/chat/completions` protocol, so
only the base URL, the key and the model name differ between them. Adding a
provider is therefore a table entry in `beastt/providers.py`, not a new backend.

Failures degrade the way the rest of BEASTT does: if the network is down or the
key is wrong, `is_available()` returns False and the caller falls back to the
local model instead of crashing.
"""

from __future__ import annotations

import json
from typing import Dict, Iterator, List, Optional

import requests

from .base import Brain, Message


class OpenAICompatError(RuntimeError):
    """A provider refused the request, carrying the clearest reason we found."""


class OpenAICompatBrain(Brain):
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        timeout: int = 300,
        label: str = "",
        extra_headers: Optional[Dict[str, str]] = None,
        catalog_url: str = "",
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.timeout = timeout
        self.label = label or model
        self.extra_headers = dict(extra_headers or {})
        # Most providers list models at {base}/models, but GitHub Models keeps
        # its catalogue on a different path, so it can be overridden.
        self.catalog_url = catalog_url or f"{self.base_url}/models"

    # --- plumbing ---------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        headers.update(self.extra_headers)
        return headers

    @staticmethod
    def _explain(resp) -> str:
        """Pull the provider's own error message out of the response body.

        Worth the effort: a bare "400 Client Error" says nothing, while these
        APIs almost always name the exact model or field they object to -- and a
        wrong model name is the most likely mistake here.
        """
        try:
            body = resp.json()
        except Exception:
            return (resp.text or "").strip()[:300]
        error = body.get("error", body) if isinstance(body, dict) else body
        if isinstance(error, dict):
            return str(error.get("message") or error)[:300]
        return str(error)[:300]

    # --- availability -----------------------------------------------------
    def list_models(self, timeout: int = 10) -> Optional[List[str]]:
        """Model names this provider advertises. None means it wasn't reachable.

        The None-vs-empty-list distinction matters: callers use it to tell "the
        provider is down" apart from "the provider works but publishes no list".
        """
        try:
            resp = requests.get(
                self.catalog_url, headers=self._headers(), timeout=timeout
            )
        except Exception:
            return None
        if resp.status_code >= 400:
            return None
        try:
            body = resp.json()
        except Exception:
            return None

        # OpenAI wraps the list in {"data": [...]}; some catalogues are a bare
        # list. Accept either, and entries keyed by id, name or model.
        rows = body.get("data") if isinstance(body, dict) else body
        if not isinstance(rows, list):
            return []

        names: List[str] = []
        for row in rows:
            if isinstance(row, str):
                names.append(row)
            elif isinstance(row, dict):
                found = row.get("id") or row.get("name") or row.get("model")
                if found:
                    names.append(str(found))
        return names

    def is_available(self) -> bool:
        """True if the provider answers with this key.

        Deliberately does *not* require the model to appear in the catalogue.
        Some providers omit models they will happily serve, and a false negative
        here would silently drop the user back to the basic fallback brain --
        the confusing failure this whole layer exists to avoid.
        """
        if not self.api_key:
            return False
        return self.list_models() is not None

    # --- inference --------------------------------------------------------
    def _post_chat(self, payload: dict) -> str:
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise OpenAICompatError(f"{resp.status_code}: {self._explain(resp)}")
        try:
            data = resp.json()
        except Exception as exc:
            raise OpenAICompatError(f"unreadable reply ({exc.__class__.__name__})")

        choices = data.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message", {}).get("content") or "").strip()

    def reply(
        self,
        messages: List[Message],
        json_mode: bool = False,
        num_ctx: Optional[int] = None,      # Ollama-only: accepted, ignored
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **_ignored,
    ) -> str:
        """Generate a reply.

        `num_ctx` is meaningless here -- context length is fixed per hosted model
        -- but the Brain contract says backends must swallow options they don't
        understand, so callers can pass the same kwargs to every backend.
        """
        payload = {
            "model": self.model,
            "messages": self.to_dicts(messages),
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            return self._post_chat(payload)
        except OpenAICompatError as exc:
            # Not every provider implements response_format. Rather than let
            # document generation fail outright, drop the constraint and retry:
            # docgen already copes with JSON wrapped in prose.
            unsupported = any(
                hint in str(exc).lower()
                for hint in ("response_format", "json_object", "json mode",
                             "not supported", "unsupported")
            )
            if json_mode and unsupported:
                payload.pop("response_format", None)
                return self._post_chat(payload)
            raise

    def stream(self, messages: List[Message]) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": self.to_dicts(messages),
            "stream": True,
        }
        with requests.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
            stream=True,
            timeout=self.timeout,
        ) as resp:
            if resp.status_code >= 400:
                raise OpenAICompatError(f"{resp.status_code}: {self._explain(resp)}")

            for raw in resp.iter_lines():
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                # Server-sent events: each payload arrives as "data: {...}",
                # terminated by a literal "data: [DONE]".
                if line.startswith("data:"):
                    line = line[len("data:"):].strip()
                if not line:
                    continue
                if line == "[DONE]":
                    break
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for choice in data.get("choices") or []:
                    chunk = (choice.get("delta") or {}).get("content")
                    if chunk:
                        yield chunk
