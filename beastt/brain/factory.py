"""Pick the best available brain for BEASTT.

Prefers the real local model (Ollama); falls back to the basic rule-based brain
so BEASTT always boots.
"""

from __future__ import annotations

from ..config import Config
from .base import Brain
from .fallback_brain import FallbackBrain
from .ollama_brain import OllamaBrain


def build_brain(config: Config, verbose: bool = True) -> Brain:
    ollama = OllamaBrain(model=config.model, base_url=config.ollama_url)

    if ollama.is_available():
        if verbose:
            print(f"[brain] Using local model '{config.model}' via Ollama.")
        return ollama

    if verbose:
        if ollama.server_running():
            print(
                f"[brain] Ollama is running but model '{config.model}' isn't installed. "
                f"Run: ollama pull {config.model}"
            )
        else:
            print(
                "[brain] Ollama not detected -- starting in basic mode. "
                "Install it from https://ollama.com for full intelligence."
            )
    return FallbackBrain(model_hint=config.model)
