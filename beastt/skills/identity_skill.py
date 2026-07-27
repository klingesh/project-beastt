"""Answers 'who are you / what's your name' with BEASTT's own identity."""

from __future__ import annotations

import re

from .base import Skill


class IdentitySkill(Skill):
    name = "identity"
    _pattern = re.compile(
        r"\b(who are you|what('?s| is) your name|what are you called)\b", re.IGNORECASE
    )

    def __init__(self, config):
        self._name = config.name
        self._user = config.user_name

    def matches(self, text: str) -> bool:
        return bool(self._pattern.search(text))

    def run(self, text: str) -> str:
        return (
            f"I'm {self._name} -- your personal AI companion and friend. "
            f"Think of me as your own JARVIS, {self._user}. I'm always here for you."
        )
