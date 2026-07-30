"""Tells today's date.

Anchored to the whole utterance so passing mentions of "date"/"today" in normal
conversation don't hijack the reply.
"""

from __future__ import annotations

import re
from datetime import datetime

from .base import Skill

_PATTERN = re.compile(
    r"""^\s*
    (?:hey\s+|ok\s+)?(?:[a-z]{4,10}[\s,]+)?
    (?:please\s+)?
    (?:
        (?:what(?:'?s|\s+is)?\s+)?(?:the\s+)?(?:today'?s\s+)?date
      | what\s+day\s+is\s+(?:it|today)
      | what(?:'?s|\s+is)\s+today(?:'?s\s+date)?
      | (?:tell|give)\s+me\s+(?:the\s+)?date
    )
    \s*[?.!]*\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


class DateSkill(Skill):
    name = "date"

    def matches(self, text: str) -> bool:
        return bool(_PATTERN.match(text))

    def run(self, text: str) -> str:
        today = datetime.now().strftime("%A, %B %d, %Y")
        return f"Today is {today}."
