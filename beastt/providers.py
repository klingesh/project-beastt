"""Which models BEASTT can think with, and where they come from.

The local Ollama model remains the default and the only one that needs no
account. Everything else is opt-in: add an API key to `.env` and that provider's
models appear in the picker.

Model ids are `provider:model`, split on the *first* colon only -- Ollama's own
names contain colons ("llama3.1:8b"), so "ollama:llama3.1:8b" has to survive a
round trip intact.

Catalogues are fetched live from each provider rather than hard-coded. Free model
line-ups change constantly -- models get deprecated or moved behind a paywall
without warning -- and a stale hard-coded list fails silently at the worst moment.
The `recommend` lists below are only a sort hint, so a renamed model costs us
nothing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .brain.base import Brain
from .config import Config

LOCAL = "local"
CLOUD = "cloud"


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    kind: str                       # LOCAL or CLOUD
    base_url: str
    #: Config attribute holding the API key. Empty for local providers.
    key_field: str = ""
    #: What to add to .env, shown in the interface when the key is missing.
    env_var: str = ""
    signup: str = ""
    blurb: str = ""
    #: Candidate catalogue addresses, tried in order. Empty means {base_url}/models.
    catalog_urls: Tuple[str, ...] = ()
    #: Sort hints only -- never used to decide what exists.
    recommend: Tuple[str, ...] = ()
    extra_headers: Tuple[Tuple[str, str], ...] = ()
    #: Extra headers for the catalogue only, where it isn't a plain JSON API.
    catalog_headers: Tuple[Tuple[str, str], ...] = ()

    @property
    def is_local(self) -> bool:
        return self.kind == LOCAL


#: Registry. Order here is the order shown in the picker.
PROVIDERS: Tuple[Provider, ...] = (
    Provider(
        id="ollama",
        label="Local (Ollama)",
        kind=LOCAL,
        base_url="",                # taken from config.ollama_url at build time
        blurb="Runs on this machine. Nothing leaves your laptop.",
        recommend=("llama3.1:8b", "llama3.2", "qwen2.5", "qwen3"),
    ),
    Provider(
        id="github",
        label="GitHub Models",
        kind=CLOUD,
        base_url="https://models.github.ai/inference",
        # The catalogue is a GitHub REST endpoint rather than an OpenAI-style
        # one, and has lived at more than one address; try both instead of
        # betting on either.
        catalog_urls=(
            "https://models.github.ai/catalog/models",
            "https://api.github.com/catalog/models",
        ),
        catalog_headers=(
            ("Accept", "application/vnd.github+json"),
            ("X-GitHub-Api-Version", "2022-11-28"),
        ),
        key_field="github_token",
        env_var="BEASTT_GITHUB_TOKEN",
        signup="https://github.com/settings/tokens",
        blurb="Free with your GitHub account. Needs the models:read scope.",
        recommend=("openai/gpt-4.1-mini", "openai/gpt-4.1", "openai/gpt-4o-mini"),
    ),
    Provider(
        id="groq",
        label="Groq",
        kind=CLOUD,
        base_url="https://api.groq.com/openai/v1",
        key_field="groq_key",
        env_var="BEASTT_GROQ_KEY",
        signup="https://console.groq.com/keys",
        blurb="Free tier, no card. Very fast -- best for everyday chat.",
        recommend=("llama-3.3-70b-versatile", "openai/gpt-oss-120b"),
    ),
    Provider(
        id="cerebras",
        label="Cerebras",
        kind=CLOUD,
        base_url="https://api.cerebras.ai/v1",
        key_field="cerebras_key",
        env_var="BEASTT_CEREBRAS_KEY",
        signup="https://cloud.cerebras.ai",
        blurb="Free tier with a large daily token budget -- good for documents.",
        recommend=("gpt-oss-120b",),
    ),
    Provider(
        id="openrouter",
        label="OpenRouter",
        kind=CLOUD,
        base_url="https://openrouter.ai/api/v1",
        key_field="openrouter_key",
        env_var="BEASTT_OPENROUTER_KEY",
        signup="https://openrouter.ai/keys",
        blurb="One key, many models. Names ending ':free' cost nothing.",
        extra_headers=(("X-Title", "JARVIS"),),
    ),
    Provider(
        id="mistral",
        label="Mistral",
        kind=CLOUD,
        base_url="https://api.mistral.ai/v1",
        key_field="mistral_key",
        env_var="BEASTT_MISTRAL_KEY",
        signup="https://console.mistral.ai/api-keys",
        blurb="Free tier available.",
    ),
    Provider(
        id="openai",
        label="OpenAI",
        kind=CLOUD,
        base_url="https://api.openai.com/v1",
        key_field="openai_key",
        env_var="BEASTT_OPENAI_KEY",
        signup="https://platform.openai.com/api-keys",
        blurb="Paid. Add a key only if you want GPT models directly.",
    ),
)

_BY_ID: Dict[str, Provider] = {p.id: p for p in PROVIDERS}

#: provider id -> (fetched_at, names or None, why it failed).
#: A None name list means "couldn't reach it"; the reason is kept so the
#: interface can say *why* rather than just showing an empty list.
_cache: Dict[str, Tuple[float, Optional[List[str]], str]] = {}
_CACHE_TTL = 300.0        # seconds; long enough to keep the picker snappy


# --- ids --------------------------------------------------------------------
def split_model_id(model_id: str) -> Tuple[str, str]:
    """"groq:llama-3.3" -> ("groq", "llama-3.3").

    Splits on the first colon only, so "ollama:llama3.1:8b" yields the full
    "llama3.1:8b" as the model name. An id with no provider prefix is treated as
    a bare Ollama model, which keeps older BEASTT_MODEL values working.
    """
    text = str(model_id or "").strip()
    if not text:
        return "", ""
    if ":" not in text:
        return "ollama", text
    head, tail = text.split(":", 1)
    if head in _BY_ID:
        return head, tail
    # Not a known provider, so the colon belonged to the model name itself.
    return "ollama", text


def join_model_id(provider_id: str, model: str) -> str:
    return f"{provider_id}:{model}"


def get(provider_id: str) -> Optional[Provider]:
    return _BY_ID.get(provider_id)


def default_model_id(config: Config) -> str:
    """The model to use when nothing else is chosen."""
    configured_default = getattr(config, "default_model", "") or ""
    if configured_default.strip():
        provider_id, model = split_model_id(configured_default)
        if model:
            return join_model_id(provider_id, model)

    # BEASTT_MODEL (or --model on the command line) may itself name a provider,
    # e.g. "--model groq:llama-3.3-70b-versatile". split_model_id sorts out which
    # it is, so a plain "llama3.1:8b" still means the local model.
    provider_id, model = split_model_id(config.model)
    return join_model_id(provider_id, model or config.model)


def describe(model_id: str) -> str:
    """A short human label, e.g. "llama-3.3-70b (Groq)"."""
    provider_id, model = split_model_id(model_id)
    provider = get(provider_id)
    if provider is None or not model:
        return model or model_id
    if provider.is_local:
        return f"{model} (local)"
    return f"{model} ({provider.label})"


# --- keys -------------------------------------------------------------------
def key_for(config: Config, provider: Provider) -> str:
    if provider.is_local or not provider.key_field:
        return ""
    return str(getattr(config, provider.key_field, "") or "")


def is_configured(config: Config, provider: Provider) -> bool:
    """Local is always usable; cloud needs a key present."""
    return provider.is_local or bool(key_for(config, provider))


# --- discovery --------------------------------------------------------------
def _ollama_models(config: Config) -> Tuple[Optional[List[str]], str]:
    """What's actually pulled locally, via Ollama's own (non-OpenAI) endpoint."""
    try:
        import requests

        resp = requests.get(f"{config.ollama_url.rstrip('/')}/api/tags", timeout=5)
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}"
        rows = resp.json().get("models") or []
    except Exception as exc:
        return None, exc.__class__.__name__
    return [str(r.get("name")) for r in rows if r.get("name")], ""


def _fetch(config: Config, provider: Provider) -> Tuple[Optional[List[str]], str]:
    """(model names, failure reason). Names are None only when unreachable."""
    if provider.is_local:
        return _ollama_models(config)

    key = key_for(config, provider)
    if not key:
        return None, "no key"

    from .brain.openai_compat import OpenAICompatBrain

    probe = OpenAICompatBrain(
        model="",
        base_url=provider.base_url,
        api_key=key,
        catalog_urls=provider.catalog_urls,
        extra_headers=dict(provider.extra_headers),
        catalog_headers=dict(provider.catalog_headers),
    )
    names = probe.list_models()
    return names, ("" if names is not None else probe.last_error)


def _discover(config: Config, provider: Provider,
              force: bool = False) -> Tuple[Optional[List[str]], str]:
    now = time.time()
    cached = _cache.get(provider.id)
    if not force and cached and now - cached[0] < _CACHE_TTL:
        return cached[1], cached[2]

    names, why = _fetch(config, provider)
    _cache[provider.id] = (now, names, why)
    return names, why


def discover(config: Config, provider: Provider,
             force: bool = False) -> Optional[List[str]]:
    """Model names for one provider, cached briefly. None means unreachable."""
    return _discover(config, provider, force=force)[0]


def clear_cache() -> None:
    _cache.clear()


def _rank(provider: Provider, name: str) -> Tuple[int, str]:
    """Sort key: recommended first, then free OpenRouter models, then the rest."""
    lowered = name.lower()
    for index, hint in enumerate(provider.recommend):
        if lowered == hint.lower():
            return (index, lowered)
    base = len(provider.recommend)
    # OpenRouter marks its zero-cost models with a ":free" suffix.
    if lowered.endswith(":free"):
        return (base, lowered)
    return (base + 1, lowered)


def catalogue(config: Config, force: bool = False) -> Dict:
    """Everything the picker needs: models on offer, and providers still to set up."""
    models: List[Dict] = []
    provider_rows: List[Dict] = []

    for provider in PROVIDERS:
        configured = is_configured(config, provider)
        names, why = (_discover(config, provider, force=force)
                      if configured else (None, "no key"))

        row = {
            "id": provider.id,
            "label": provider.label,
            "kind": provider.kind,
            "blurb": provider.blurb,
            "configured": configured,
            "env_var": provider.env_var,
            "signup": provider.signup,
            "count": len(names or []),
        }
        if not configured:
            row["status"] = "no key"
        elif names is None:
            if provider.is_local:
                row["status"] = "Ollama isn't running"
            else:
                # Include the provider's own reason: a 401 means the key is
                # wrong or lacks a scope, while a 404 means we have the address
                # wrong -- and those need completely different fixes.
                row["status"] = f"couldn't reach it — {why}" if why else "couldn't reach it"
                row["detail"] = why
        elif not names:
            row["status"] = ("no models pulled yet" if provider.is_local
                             else "no models offered")
        else:
            row["status"] = "ready"
        provider_rows.append(row)

        for name in sorted(names or [], key=lambda n: _rank(provider, n)):
            models.append({
                "id": join_model_id(provider.id, name),
                "provider": provider.id,
                "provider_label": provider.label,
                "model": name,
                "kind": provider.kind,
            })

    return {"models": models, "providers": provider_rows}


# --- construction -----------------------------------------------------------
def build(config: Config, model_id: str) -> Optional[Brain]:
    """Build a brain for one model id, or None if it can't be served.

    Returning None rather than raising lets the caller decide whether to fall
    back to the local model or report the problem.
    """
    provider_id, model = split_model_id(model_id)
    provider = get(provider_id)
    if provider is None or not model:
        return None

    if provider.is_local:
        from .brain.ollama_brain import OllamaBrain

        return OllamaBrain(
            model=model,
            base_url=config.ollama_url,
            timeout=getattr(config, "request_timeout", 300),
        )

    key = key_for(config, provider)
    if not key:
        return None

    from .brain.openai_compat import OpenAICompatBrain

    return OpenAICompatBrain(
        model=model,
        base_url=provider.base_url,
        api_key=key,
        timeout=getattr(config, "request_timeout", 300),
        label=describe(model_id),
        extra_headers=dict(provider.extra_headers),
        catalog_urls=provider.catalog_urls,
        catalog_headers=dict(provider.catalog_headers),
    )
