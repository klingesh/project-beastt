"""Wake word detection -- the assistant idles until you call its name.

Speech-to-text output is fuzzy (a name comes back with different spellings each
time), so matching is tolerant: each spoken word is compared to a set of
variants using a similarity ratio rather than exact equality.

The wake words are derived from the configured assistant name, so renaming the
assistant automatically changes what it answers to. Extra spellings can be
added via BEASTT_WAKE_WORDS.

Anything said *after* the wake word is preserved, so "Jarvis, what's the
weather?" both wakes it and asks the question in one breath.
"""

from __future__ import annotations

import difflib
import re
from typing import Iterable, Optional, Tuple

# Common mishearings for names we ship with, keyed by lowercase name.
_KNOWN_VARIANTS = {
    "jarvis": ("jarvis", "jervis", "javis", "jarvist", "jarviss", "charvis", "garvis"),
    "beastt": ("beastt", "beast", "beasty", "biest"),
    "beast": ("beast", "beastt", "beasty", "biest"),
}

# How similar a spoken word must be to count (0-1). Kept high so everyday
# lookalikes (e.g. "best" vs "beast") don't trigger a false wake.
_SIMILARITY = 0.85

# Filler that may precede the name.
_PREFIX = re.compile(r"^(?:hey|hi|hello|ok|okay|yo|hai)\s+", re.IGNORECASE)


def variants_for(name: str, extra: Iterable[str] = ()) -> tuple:
    """Build the set of spellings that should wake the assistant."""
    low = (name or "").strip().lower()
    words = set(_KNOWN_VARIANTS.get(low, (low,)))
    words.add(low)
    words.update(w.strip().lower() for w in extra if w and w.strip())
    return tuple(w for w in words if w)


DEFAULT_WAKE_WORDS = variants_for("jarvis")


def _min_len(wake_words) -> int:
    shortest = min((len(w) for w in wake_words), default=5)
    return max(4, shortest - 1)


def _prefixes(wake_words) -> set:
    """Leading stems, so 'jarvis'/'jarvist' both match but 'jars' doesn't."""
    return {w[:4] for w in wake_words if len(w) >= 5}


def _is_wake_token(token: str, wake_words, min_len: int, prefixes: set) -> bool:
    if len(token) < min_len:
        return False
    if token in wake_words:
        return True
    if any(token.startswith(p) for p in prefixes):
        return True
    for wake in wake_words:
        if difflib.SequenceMatcher(None, token, wake).ratio() >= _SIMILARITY:
            return True
    return False


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", (text or "").lower()).strip()


def detect(text: str, wake_words=DEFAULT_WAKE_WORDS) -> Tuple[bool, str]:
    """Return (woken, remainder).

    `remainder` is whatever followed the wake word, so it can be treated as the
    user's first message. Empty if they only called the name.
    """
    norm = _normalise(text)
    if not norm:
        return False, ""

    norm = _PREFIX.sub("", norm)
    words = norm.split()
    min_len = _min_len(wake_words)
    prefixes = _prefixes(wake_words)

    # Also catch split-up transcriptions ("jar vis") by testing joined pairs.
    for idx, word in enumerate(words):
        if _is_wake_token(word, wake_words, min_len, prefixes):
            return True, " ".join(words[idx + 1 :]).strip()
        # Joined pairs are matched strictly (exact or stem only). Fuzzy matching
        # here would fire on innocent phrases -- "java is" joins to "javais".
        if idx + 1 < len(words):
            joined = word + words[idx + 1]
            if joined in wake_words or any(joined.startswith(p) for p in prefixes):
                return True, " ".join(words[idx + 2 :]).strip()

    return False, ""


# --- choosing a mode after waking ------------------------------------------
_TEXT_WORDS = ("text", "type", "typing", "keyboard", "chat", "written", "write")
_VOICE_WORDS = ("voice", "speak", "speech", "talk", "talking", "audio", "say")


def parse_mode_choice(text: str) -> Optional[str]:
    """Interpret a spoken/typed answer to 'voice or text?'.

    Returns "voice", "text", or None if unclear.
    """
    norm = _normalise(text)
    if not norm:
        return None
    words = set(norm.split())

    text_hit = any(w in words for w in _TEXT_WORDS)
    voice_hit = any(w in words for w in _VOICE_WORDS)

    # If both appear ("voice or text"), it's not an answer.
    if text_hit and voice_hit:
        return None
    if text_hit:
        return "text"
    if voice_hit:
        return "voice"
    if norm in ("t", "txt"):
        return "text"
    if norm in ("v",):
        return "voice"
    return None
