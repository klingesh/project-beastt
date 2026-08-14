"""Central configuration for BEASTT.

Values are read from environment variables (optionally loaded from a `.env`
file) so nothing is hard-coded and everything has a sensible default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    from pathlib import Path as _Path

    # Load the project's .env explicitly: a background service may be started
    # from any working directory, where a bare load_dotenv() would find nothing.
    _env = _Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(_env if _env.exists() else None)
except Exception:  # dotenv is optional; defaults still work without it.
    pass


def _get(name: str, default: str) -> str:
    """Read a setting, accepting either the JARVIS_ or legacy BEASTT_ prefix."""
    for key in (name.replace("BEASTT_", "JARVIS_", 1), name):
        value = os.environ.get(key)
        if value not in (None, ""):
            return value
    return default


@dataclass
class Config:
    """Everything BEASTT needs to know about itself and its environment."""

    # Identity
    name: str = _get("BEASTT_NAME", "JARVIS")
    user_name: str = _get("BEASTT_USER_NAME", "friend")

    # Brain (Ollama) -- the local model, and still the default.
    model: str = _get("BEASTT_MODEL", "llama3.2")
    ollama_url: str = _get("BEASTT_OLLAMA_URL", "http://localhost:11434")
    # Generous, because generating a whole document on a laptop GPU is slow.
    request_timeout: int = int(_get("BEASTT_TIMEOUT", "300"))

    # Hosted model providers -- every one optional. BEASTT behaves exactly as it
    # always has if none are set; adding a key is the only thing that makes a
    # provider's models appear in the picker. GitHub Models reuses the existing
    # BEASTT_GITHUB_TOKEN further down, so it may already work for you.
    groq_key: str = _get("BEASTT_GROQ_KEY", "")
    cerebras_key: str = _get("BEASTT_CEREBRAS_KEY", "")
    openrouter_key: str = _get("BEASTT_OPENROUTER_KEY", "")
    mistral_key: str = _get("BEASTT_MISTRAL_KEY", "")
    openai_key: str = _get("BEASTT_OPENAI_KEY", "")
    #: Which model to start with, as "provider:model" (e.g.
    #: "groq:llama-3.3-70b-versatile"). Empty means the local model named above.
    default_model: str = _get("BEASTT_DEFAULT_MODEL", "")

    # Voice
    voice_enabled: bool = _get("BEASTT_VOICE", "off").lower() in ("on", "true", "1", "yes")
    tts_rate: int = int(_get("BEASTT_TTS_RATE", "175"))
    stt_model: str = _get("BEASTT_STT_MODEL", "base")

    # Wake word / standby mode
    wake_model: str = _get("BEASTT_WAKE_MODEL", "tiny")   # small+fast for standby
    # What to do once woken: ask | voice | text
    #: What happens once the wake word is heard: ask | voice | text | ui.
    #: "ui" greets you out loud and opens the chat interface in a browser, which
    #: is the least ambiguous feedback available -- a spoken greeting alone is
    #: invisible if the speakers are muted.
    on_wake: str = _get("BEASTT_ON_WAKE", "ask").lower()
    #: Port the chat interface listens on, so waking can open the right address.
    ui_port: int = int(_get("BEASTT_UI_PORT", "8765"))
    # Extra spellings to accept as the wake word (comma separated).
    extra_wake_words: str = _get("BEASTT_WAKE_WORDS", "")

    def wake_words(self) -> tuple:
        """Spellings that should wake the assistant, derived from its name."""
        from .wake import variants_for

        extra = [w for w in self.extra_wake_words.split(",") if w.strip()]
        return variants_for(self.name, extra)

    # Memory
    max_history_messages: int = int(_get("BEASTT_MAX_HISTORY", "20"))

    # Long-term memory (persists across sessions)
    longterm_enabled: bool = _get("BEASTT_MEMORY", "on").lower() in ("on", "true", "1", "yes")
    memory_path: str = _get("BEASTT_MEMORY_PATH", "beastt_memory/memory.json")
    memory_recall_limit: int = int(_get("BEASTT_MEMORY_RECALL", "8"))

    # Documents (PowerPoint / Word / Excel generation)
    documents_enabled: bool = _get("BEASTT_DOCUMENTS", "on").lower() in ("on", "true", "1", "yes")
    doc_theme: str = _get("BEASTT_DOC_THEME", "navy")   # navy | slate | plum | ember
    #: Plan the deck first, then write each slide on its own. Much better
    #: content, but it costs one model call per slide -- turn it off if you are
    #: on a slow local model and would rather have the deck in one shot.
    doc_deliberate: bool = _get("BEASTT_DOC_DELIBERATE", "on").lower() in (
        "on", "true", "1", "yes")
    # Openly-licensed photos in presentations (attribution is added automatically).
    images_enabled: bool = _get("BEASTT_IMAGES", "on").lower() in ("on", "true", "1", "yes")
    #: Generate artwork for slides no photograph can illustrate. Pollinations
    #: needs nothing at all; Cloudflare is steadier once you add a token. Every
    #: generated image is credited as AI-generated on the credits slide.
    imagegen_enabled: bool = _get("BEASTT_IMAGE_GEN", "on").lower() in (
        "on", "true", "1", "yes")
    #: photo | generated -- which to try first when both are available.
    image_prefer: str = _get("BEASTT_IMAGE_PREFER", "photo").lower()
    pollinations_key: str = _get("BEASTT_POLLINATIONS_KEY", "")
    cloudflare_account: str = _get("BEASTT_CF_ACCOUNT", "")
    cloudflare_token: str = _get("BEASTT_CF_TOKEN", "")

    # GitHub access (push generated files, browse repos)
    github_token: str = _get("BEASTT_GITHUB_TOKEN", "")
    github_repo: str = _get("BEASTT_GITHUB_REPO", "")        # default push target
    github_folder: str = _get("BEASTT_GITHUB_FOLDER", "jarvis")
    # Ask which repo to push to instead of silently using the default.
    github_ask: bool = _get("BEASTT_GITHUB_ASK", "on").lower() in ("on", "true", "1", "yes")

    # Coding help: writing files, scaffolding projects, cloning repositories.
    code_enabled: bool = _get("BEASTT_CODE", "on").lower() in ("on", "true", "1", "yes")
    # Running shell commands. Off by default: it is the riskiest capability here.
    shell_enabled: bool = _get("BEASTT_SHELL", "off").lower() in ("on", "true", "1", "yes")

    # Published data (economic figures from the institutions that publish them,
    # instead of whatever the model half-remembers). World Bank needs no key and
    # works immediately; FRED wants a free one for US monthly/daily series.
    data_enabled: bool = _get("BEASTT_DATA", "on").lower() in ("on", "true", "1", "yes")
    fred_key: str = _get("BEASTT_FRED_KEY", "")

    # Watching a trading bot that runs elsewhere. The bot publishes status.json to
    # a private repo; this reads it. Read-only -- BEASTT never places a trade.
    bot_status_repo: str = _get("BEASTT_BOT_STATUS_REPO", "")
    #: A read-only token for that repo. Falls back to BEASTT_GITHUB_TOKEN, which
    #: only works if that token's access covers the status repository too.
    bot_status_token: str = _get("BEASTT_BOT_STATUS_TOKEN", "")
    bot_status_file: str = _get("BEASTT_BOT_STATUS_FILE", "status.json")
    #: Must exceed the publisher's interval, not the bot's poll interval: the
    #: heartbeat is written every 60s but only published every 300s, so a healthy
    #: bot legitimately looks five minutes old from here.
    bot_stale_minutes: int = int(_get("BEASTT_BOT_STALE_MINUTES", "15"))
    #: Interrupt me when the bot is in trouble, rather than only when I ask.
    #: Needs the background service running (--install-startup).
    bot_alerts: bool = _get("BEASTT_BOT_ALERTS", "on").lower() in (
        "on", "true", "1", "yes")
    bot_check_minutes: int = int(_get("BEASTT_BOT_CHECK_MINUTES", "5"))
    #: How long before a problem that hasn't gone away is mentioned again. Hourly:
    #: a kill switch at 3am should still be visible at breakfast, but repeating
    #: every five minutes teaches its owner to dismiss notifications.
    bot_remind_minutes: int = int(_get("BEASTT_BOT_REMIND_MINUTES", "60"))

    # Web search
    search_enabled: bool = _get("BEASTT_SEARCH", "on").lower() in ("on", "true", "1", "yes")
    #: Work substantial requests through in steps -- understand, plan, then do
    #: each step -- instead of answering in one call. Short or chatty messages
    #: are always answered straight away regardless.
    deliberate: bool = _get("BEASTT_DELIBERATE", "on").lower() in ("on", "true", "1", "yes")
    search_max_results: int = int(_get("BEASTT_SEARCH_RESULTS", "5"))
    #: How many of the top results to open and read in full, rather than relying
    #: on the search engine's snippet. A snippet is about twenty words chosen to
    #: look relevant; answering from snippets alone is what made replies read as
    #: confident summaries of pages nobody had opened. Costs roughly a second per
    #: page. 0 restores the old snippet-only behaviour.
    search_read_pages: int = int(_get("BEASTT_SEARCH_READ_PAGES", "3"))
    #: Check that every figure in a reply appears in the material retrieved, and
    #: re-ask once if not. Deterministic and local -- no extra model call unless
    #: something is actually unsupported.
    verify_figures: bool = _get("BEASTT_VERIFY", "on").lower() in (
        "on", "true", "1", "yes")
    #: Live market quotes (Yahoo Finance, Stooq fallback). Keyless, and both are
    #: undocumented public endpoints, so this is here to be turned off.
    quotes_enabled: bool = _get("BEASTT_QUOTES", "on").lower() in (
        "on", "true", "1", "yes")

    # Speaker recognition (respond to only the owner's voice)
    speaker_only: bool = _get("BEASTT_MY_VOICE_ONLY", "off").lower() in ("on", "true", "1", "yes")
    # Only used when no "other voices" cohort is enrolled. Kept forgiving,
    # because a strict absolute cut-off rejects the owner's own voice as often
    # as it blocks anyone else; cohort comparison is the reliable mechanism.
    speaker_threshold: float = float(_get("BEASTT_SPEAKER_THRESHOLD", "0.70"))
    # Required lead of owner-match over best other-person match (cohort scoring).
    speaker_margin: float = float(_get("BEASTT_SPEAKER_MARGIN", "0.04"))
    voiceprint_path: str = _get("BEASTT_VOICEPRINT", "beastt_memory/voiceprint.npz")

    @classmethod
    def load(cls) -> "Config":
        return cls()
