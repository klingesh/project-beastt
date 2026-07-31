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

    # Brain (Ollama)
    model: str = _get("BEASTT_MODEL", "llama3.2")
    ollama_url: str = _get("BEASTT_OLLAMA_URL", "http://localhost:11434")

    # Voice
    voice_enabled: bool = _get("BEASTT_VOICE", "off").lower() in ("on", "true", "1", "yes")
    tts_rate: int = int(_get("BEASTT_TTS_RATE", "175"))
    stt_model: str = _get("BEASTT_STT_MODEL", "base")

    # Wake word / standby mode
    wake_model: str = _get("BEASTT_WAKE_MODEL", "tiny")   # small+fast for standby
    # What to do once woken: ask | voice | text
    on_wake: str = _get("BEASTT_ON_WAKE", "ask").lower()
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

    # GitHub access (push generated files, browse repos)
    github_token: str = _get("BEASTT_GITHUB_TOKEN", "")
    github_repo: str = _get("BEASTT_GITHUB_REPO", "")        # default push target
    github_folder: str = _get("BEASTT_GITHUB_FOLDER", "jarvis")

    # Web search
    search_enabled: bool = _get("BEASTT_SEARCH", "on").lower() in ("on", "true", "1", "yes")
    search_max_results: int = int(_get("BEASTT_SEARCH_RESULTS", "5"))

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
