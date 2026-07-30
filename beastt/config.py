"""Central configuration for BEASTT.

Values are read from environment variables (optionally loaded from a `.env`
file) so nothing is hard-coded and everything has a sensible default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional; defaults still work without it.
    pass


def _get(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass
class Config:
    """Everything BEASTT needs to know about itself and its environment."""

    # Identity
    name: str = _get("BEASTT_NAME", "BEASTT")
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

    # Memory
    max_history_messages: int = int(_get("BEASTT_MAX_HISTORY", "20"))

    # Long-term memory (persists across sessions)
    longterm_enabled: bool = _get("BEASTT_MEMORY", "on").lower() in ("on", "true", "1", "yes")
    memory_path: str = _get("BEASTT_MEMORY_PATH", "beastt_memory/memory.json")
    memory_recall_limit: int = int(_get("BEASTT_MEMORY_RECALL", "8"))

    # Web search
    search_enabled: bool = _get("BEASTT_SEARCH", "on").lower() in ("on", "true", "1", "yes")
    search_max_results: int = int(_get("BEASTT_SEARCH_RESULTS", "5"))

    # Speaker recognition (respond to only the owner's voice)
    speaker_only: bool = _get("BEASTT_MY_VOICE_ONLY", "off").lower() in ("on", "true", "1", "yes")
    speaker_threshold: float = float(_get("BEASTT_SPEAKER_THRESHOLD", "0.80"))
    # Required lead of owner-match over best other-person match (cohort scoring).
    speaker_margin: float = float(_get("BEASTT_SPEAKER_MARGIN", "0.04"))
    voiceprint_path: str = _get("BEASTT_VOICEPRINT", "beastt_memory/voiceprint.npz")

    @classmethod
    def load(cls) -> "Config":
        return cls()
