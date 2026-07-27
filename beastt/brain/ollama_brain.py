"""Ollama-backed brain -- runs a free local model on your own machine.

Install Ollama from https://ollama.com, then:  ollama pull llama3.2
"""

from __future__ import annotations

import json
from typing import Iterator, List

import requests

from .base import Brain, Message


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
    def reply(self, messages: List[Message]) -> str:
        payload = {
            "model": self.model,
            "messages": self.to_dicts(messages),
            "stream": False,
        }
        resp = requests.post(
            f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip()

    def stream(self, messages: List[Message]) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": self.to_dicts(messages),
            "stream": True,
        }
        with requests.post(
            f"{self.base_url}/api/chat", json=payload, stream=True, timeout=self.timeout
        ) as resp:
            resp.raise_for_status()
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
