"""Ollama-backed brain -- runs a free local model on your own machine.

Install Ollama from https://ollama.com, then:  ollama pull llama3.2
"""

from __future__ import annotations

import json
from typing import Iterator, List

import requests

from .base import Brain, Message


class OllamaError(RuntimeError):
    """Ollama refused the request, with something worth showing a human."""


def _explain(resp) -> str:
    """Ollama's own reason for refusing, which is usually specific and useful."""
    try:
        detail = str((resp.json() or {}).get("error", "")).strip()
    except Exception:
        detail = ""
    if not detail:
        detail = (resp.text or "").strip()[:200]
    return detail or f"HTTP {resp.status_code}"


def _check(resp) -> None:
    """Turn a bad status into a readable error rather than a bare HTTPError.

    `raise_for_status()` produced "Hmm, I hit a snag trying to think that through
    (HTTPError)" -- which names the exception class and nothing about the cause.
    That reached a user when a long research prompt was rejected, and there was
    no way to tell from the message that the prompt was the problem.

    Ollama puts a real reason in the body, so it is worth reading. The
    context-length case gets its own advice because it is the one a user can
    actually do something about, and it became reachable the moment replies
    started carrying pages of retrieved text.
    """
    if resp.status_code < 400:
        return
    detail = _explain(resp)
    lowered = detail.lower()
    if any(hint in lowered for hint in ("context", "too long", "token", "exceeds",
                                        "maximum", "memory", "n_ctx")):
        raise OllamaError(
            f"the local model rejected the request as too long ({detail}). "
            "Try BEASTT_SEARCH_READ_PAGES=1 to send less retrieved text, or a "
            "model with a larger context window."
        )
    raise OllamaError(f"Ollama refused the request ({detail}).")


class OllamaBrain(Brain):
    def __init__(self, model: str, base_url: str = "http://localhost:11434", timeout: int = 120):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # --- availability -----------------------------------------------------
    def is_available(self) -> bool:
        """True only if Ollama is running AND the requested model is pulled."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=3)
            resp.raise_for_status()
            tags = resp.json().get("models", [])
            names = {m.get("name", "").split(":")[0] for m in tags}
            return self.model.split(":")[0] in names
        except Exception:
            return False

    def server_running(self) -> bool:
        try:
            requests.get(f"{self.base_url}/api/tags", timeout=3)
            return True
        except Exception:
            return False

    # --- inference --------------------------------------------------------
    def reply(
        self,
        messages: List[Message],
        json_mode: bool = False,
        num_ctx: int = 8192,
        temperature: float = None,
    ) -> str:
        """Generate a reply.

        `json_mode` uses Ollama's constrained JSON decoding, which makes
        structured output far more reliable than asking politely in the prompt.
        `num_ctx` is raised well above Ollama's small default so long documents
        aren't silently truncated mid-reply.
        """
        options = {"num_ctx": num_ctx}
        if temperature is not None:
            options["temperature"] = temperature

        payload = {
            "model": self.model,
            "messages": self.to_dicts(messages),
            "stream": False,
            "options": options,
        }
        if json_mode:
            payload["format"] = "json"

        resp = requests.post(
            f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
        )
        _check(resp)
        return resp.json().get("message", {}).get("content", "").strip()

    def stream(self, messages: List[Message], num_ctx: int = 8192) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": self.to_dicts(messages),
            "stream": True,
            # Same raised context as reply(). Without this, streaming quietly
            # used Ollama's small default and truncated long conversations --
            # a difference that would only ever show up when streaming.
            "options": {"num_ctx": num_ctx},
        }
        with requests.post(
            f"{self.base_url}/api/chat", json=payload, stream=True, timeout=self.timeout
        ) as resp:
            _check(resp)
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                chunk = data.get("message", {}).get("content", "")
                if chunk:
                    yield chunk
                if data.get("done"):
                    break
