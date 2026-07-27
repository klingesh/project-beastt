"""Tells today's date."""

from __future__ import annotations

import re
from datetime import datetime

from .base import Skill


class DateSkill(Skill):
    name = "date"
    _pattern = re.compile(
        r"\b(what('?s| is)? (the |today'?s )?date|what day is it|what'?s today)\b",
        re.IGNORECASE,
    )

    def matches(self, text: str) -> bool:
        return bool(self._pattern.search(text))

    def run(self, text: str) -> str:
        today = datetime.now().strftime("%A, %B %d, %Y")
        return f"Today is {today}."
