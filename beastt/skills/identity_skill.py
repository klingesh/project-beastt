"""Answers 'who are you / what's your name' with BEASTT's own identity."""

from __future__ import annotations

import re

from .base import Skill


_PATTERN = re.compile(
    r"""^\s*
    (?:hey\s+|ok\s+)?(?:[a-z]{4,10}[\s,]+)?
    (?:so\s+|and\s+)?
    (?:
        who\s+are\s+you
      | what(?:'?s|\s+is)\s+your\s+name
      | what\s+are\s+you\s+called
      | (?:tell|remind)\s+me\s+your\s+name
    )
    \s*[?.!]*\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


class IdentitySkill(Skill):
    name = "identity"

    def __init__(self, config):
        self._name = config.name
        self._user = config.user_name

    def matches(self, text: str) -> bool:
        return bool(_PATTERN.match(text))

    def run(self, text: str) -> str:
        # Only address the user by name when we actually know it.
        address = "" if self._user.lower() in ("friend", "", "user") else f", {self._user}"
        return (
            f"I'm {self._name} -- your personal AI companion and friend{address}. "
            "I'm always here for you."
        )
