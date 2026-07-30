"""Wake word detection -- BEASTT idles until you call its name.

Speech-to-text output is fuzzy ("beastt" comes back as "beast", "beast t",
"biest", "Beasty!"), so matching is tolerant: we compare each spoken word to a
set of wake variants using a similarity ratio rather than exact equality.

Anything the user says *after* the wake word is preserved, so "BEASTT, what's
the weather?" both wakes it and asks the question in one breath.
"""

from __future__ import annotations

import difflib
import re
from typing import List, Optional, Tuple

# Canonical spellings we accept as "BEASTT".
DEFAULT_WAKE_WORDS = ("beastt", "beast", "beasty", "beast", "biest")

# How similar a spoken word must be to count (0-1). Kept high because common
# words like "best" and "beans" are otherwise close enough to "beast" to trigger.
_SIMILARITY = 0.85

# Minimum length for a candidate, which rules out short lookalikes ("best").
_MIN_LEN = 5

# Filler that may precede the name.
_PREFIX = re.compile(r"^(?:hey|hi|hello|ok|okay|yo|hai)\s+", re.IGNORECASE)


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", (text or "").lower()).strip()


def _is_wake_token(token: str, wake_words) -> bool:
    if len(token) < _MIN_LEN:
        return False
    if token in wake_words:
        return True
    # "beasts", "beastt", "beastie"... but not "beans"/"beautiful".
    if token.startswith("beas"):
        return True
    for wake in wake_words:
        if difflib.SequenceMatcher(None, token, wake).ratio() >= _SIMILARITY:
            return True
    return False


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

    # Also catch "b east" style splits by testing joined pairs.
    for idx, word in enumerate(words):
        if _is_wake_token(word, wake_words):
            return True, " ".join(words[idx + 1 :]).strip()
        if idx + 1 < len(words) and _is_wake_token(word + words[idx + 1], wake_words):
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
    # Single-letter answers.
    if norm in ("t", "txt"):
        return "text"
    if norm in ("v",):
        return "voice"
    return None
