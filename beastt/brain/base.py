"""The Brain interface -- any LLM backend implements this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Iterator, List


@dataclass
class Message:
    """A single chat message. `role` is one of: system, user, assistant."""

    role: str
    content: str

    def as_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


class Brain(ABC):
    """Abstract base class for anything that can turn messages into a reply."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this backend is ready to answer (model reachable, etc.)."""

    @abstractmethod
    def reply(self, messages: List[Message]) -> str:
        """Return a complete reply for the given conversation."""

    def stream(self, messages: List[Message]) -> Iterator[str]:
        """Yield the reply in chunks. Default: yield the whole thing at once."""
        yield self.reply(messages)

    @staticmethod
    def to_dicts(messages: Iterable[Message]) -> List[dict]:
        return [m.as_dict() for m in messages]
