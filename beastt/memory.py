"""Conversation memory -- keeps BEASTT aware of the flow of the chat.

Holds a persistent system prompt plus a rolling window of recent messages so
the model always has context without blowing past its limits.
"""

from __future__ import annotations

from typing import List

from .brain.base import Message


class Memory:
    def __init__(self, system_prompt: str, max_messages: int = 20):
        self._system = Message(role="system", content=system_prompt)
        self._history: List[Message] = []
        self.max_messages = max_messages

    def add_user(self, text: str) -> None:
        self._history.append(Message(role="user", content=text))
        self._trim()

    def add_assistant(self, text: str) -> None:
        self._history.append(Message(role="assistant", content=text))
        self._trim()

    def _trim(self) -> None:
        if len(self._history) > self.max_messages:
            # Drop the oldest messages, keeping the window recent.
            self._history = self._history[-self.max_messages :]

    def messages(self) -> List[Message]:
        """Full message list to send to the brain (system prompt first)."""
        return [self._system, *self._history]

    def clear(self) -> None:
        self._history.clear()
