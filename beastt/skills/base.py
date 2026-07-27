"""The Skill interface. Subclass this to give BEASTT a new ability."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Skill(ABC):
    #: Human-readable name, handy for debugging and `help`.
    name: str = "skill"

    @abstractmethod
    def matches(self, text: str) -> bool:
        """Return True if this skill should handle the given user text."""

    @abstractmethod
    def run(self, text: str) -> str:
        """Produce a reply for the matched text."""
