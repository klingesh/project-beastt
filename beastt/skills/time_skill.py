"""Tells the current time."""

from __future__ import annotations

import re
from datetime import datetime

from .base import Skill


class TimeSkill(Skill):
    name = "time"
    _pattern = re.compile(r"\b(what('?s| is)? the )?time\b|what time is it", re.IGNORECASE)

    def matches(self, text: str) -> bool:
        return bool(self._pattern.search(text))

    def run(self, text: str) -> str:
        now = datetime.now().strftime("%I:%M %p").lstrip("0")
        return f"It's {now} right now."
