"""Tells the current time.

The pattern is deliberately anchored to the whole utterance. An earlier version
matched a bare "time" anywhere, so ordinary sentences like "most of the time"
hijacked the conversation instead of reaching the brain.
"""

from __future__ import annotations

import re
from datetime import datetime

from .base import Skill

_PATTERN = re.compile(
    r"""^\s*
    (?:hey\s+|ok\s+)?(?:beastt[\s,]*)?          # optional address
    (?:please\s+)?
    (?:
        (?:what(?:'?s|\s+is)?\s+)?(?:the\s+)?(?:current\s+)?time(?:\s+is\s+it)?
      | what\s+time\s+is\s+it(?:\s+now)?
      | (?:tell|give)\s+me\s+(?:the\s+)?time
      | time\s+now
      | got\s+the\s+time
    )
    \s*[?.!]*\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


class TimeSkill(Skill):
    name = "time"

    def matches(self, text: str) -> bool:
        return bool(_PATTERN.match(text))

    def run(self, text: str) -> str:
        now = datetime.now().strftime("%I:%M %p").lstrip("0")
        return f"It's {now} right now."
