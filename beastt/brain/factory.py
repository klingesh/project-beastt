"""Pick the best available brain for BEASTT.

Prefers whichever model was asked for -- local or hosted -- then the local
Ollama model, and finally the rule-based fallback so BEASTT always boots.
"""

from __future__ import annotations

from typing import Optional

from ..config import Config
from .base import Brain
from .fallback_brain import FallbackBrain
from .ollama_brain import OllamaBrain


def build_brain(config: Config, verbose: bool = True,
                model_id: Optional[str] = None) -> Brain:
    """Build a brain, falling back gracefully if the first choice isn't there.

    `model_id` is a "provider:model" id (see `beastt.providers`). When omitted,
    the configured default is used -- which is the local Ollama model unless
    BEASTT_DEFAULT_MODEL says otherwise.
    """
    # Imported here rather than at module scope: `beastt.providers` reaches back
    # into `beastt.brain`, and a top-level import would make that circular.
    from .. import providers

    wanted = model_id or providers.default_model_id(config)
    provider_id, _ = providers.split_model_id(wanted)
    provider = providers.get(provider_id)

    # A hosted model: use it if the provider answers, otherwise say why and
    # carry on down to the local model rather than leaving the user stuck.
    if provider is not None and not provider.is_local:
        brain = providers.build(config, wanted)
        if brain is None:
            if verbose:
                print(
                    f"[brain] No API key for {provider.label}. "
                    f"Add {provider.env_var} to your .env, or pick a local model."
                )
        elif brain.is_available():
            if verbose:
                print(f"[brain] Using {providers.describe(wanted)}.")
            return brain
        elif verbose:
            print(
                f"[brain] {provider.label} didn't answer -- falling back to the "
                f"local model. Check {provider.env_var} in your .env."
            )
        wanted = providers.join_model_id("ollama", config.model)

    _, local_model = providers.split_model_id(wanted)
    ollama = OllamaBrain(
        model=local_model or config.model,
        base_url=config.ollama_url,
        timeout=getattr(config, "request_timeout", 300),
    )

    if ollama.is_available():
        if verbose:
            print(f"[brain] Using local model '{ollama.model}' via Ollama.")
        return ollama

    # A saved id can name a provider that no longer exists -- GitHub Models was
    # retired mid-2026 -- in which case it parses as an odd local model name.
    # Retry on the configured model rather than telling the user to pull it.
    if ollama.model != config.model:
        plain = OllamaBrain(
            model=config.model,
            base_url=config.ollama_url,
            timeout=getattr(config, "request_timeout", 300),
        )
        if plain.is_available():
            if verbose:
                print(
                    f"[brain] '{ollama.model}' isn't available; "
                    f"using local model '{config.model}' instead."
                )
            return plain

    if verbose:
        if ollama.server_running():
            print(
                f"[brain] Ollama is running but model '{ollama.model}' isn't installed. "
                f"Run: ollama pull {ollama.model}"
            )
        else:
            print(
                "[brain] Ollama not detected -- starting in basic mode. "
                "Install it from https://ollama.com for full intelligence."
            )
    return FallbackBrain(model_hint=ollama.model)
