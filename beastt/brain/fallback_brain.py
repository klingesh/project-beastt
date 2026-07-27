"""A tiny rule-based brain used when no local model is available yet.

It lets BEASTT boot, greet you, and hold a *very* basic conversation so the
whole app is usable before you install Ollama. It also nudges you toward
setting up the real brain for full intelligence.
"""

from __future__ import annotations

import random
import re
from typing import List

from .base import Brain, Message


class FallbackBrain(Brain):
    def __init__(self, model_hint: str = "llama3.2"):
        self.model_hint = model_hint

    def is_available(self) -> bool:
        return True  # always works

    def reply(self, messages: List[Message]) -> str:
        user_text = ""
        for m in reversed(messages):
            if m.role == "user":
                user_text = m.content.strip().lower()
                break

        if re.search(r"\b(hi|hello|hey|yo|sup)\b", user_text):
            return random.choice(
                [
                    "Hey! I'm running in my basic mode right now, but I'm still happy to chat.",
                    "Hello there! Good to hear from you.",
                ]
            )
        if re.search(r"how are you", user_text):
            return "I'm doing great, thanks for asking! How are you feeling today?"
        if re.search(r"\b(thanks|thank you)\b", user_text):
            return "Anytime! That's what friends are for."
        if re.search(r"\b(bye|goodbye|see you)\b", user_text):
            return "Talk soon! I'll be right here whenever you need me."

        return (
            "I'm currently in basic mode, so my thoughts are a little limited. "
            f"To unlock my full brain, install Ollama and run `ollama pull {self.model_hint}` "
            "-- then I'll be able to really talk with you."
        )
