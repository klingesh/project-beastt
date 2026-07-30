"""Explicit memory control: "remember that ...", "forget ...", "what do you know about me".

Gives the user direct, immediate control over BEASTT's long-term memory rather
than relying only on automatic end-of-session reflection.
"""

from __future__ import annotations

import re

from .base import Skill

_REMEMBER = re.compile(
    r"^\s*(?:hey\s+[a-z]{4,10}[,\s]+)?(?:please\s+)?remember\s+(?:that\s+|this[:,]\s*|about\s+me\s+that\s+)?(.+)",
    re.IGNORECASE,
)
_FORGET = re.compile(
    r"^\s*(?:hey\s+[a-z]{4,10}[,\s]+)?(?:please\s+)?forget\s+(?:about\s+|that\s+|the\s+)?(.+)",
    re.IGNORECASE,
)
_FORGET_ALL = re.compile(
    r"\bforget\s+everything\b|\bclear\s+(?:your\s+)?memory\b|\bwipe\s+(?:your\s+)?memory\b",
    re.IGNORECASE,
)
_RECALL = re.compile(
    r"\bwhat\s+do\s+you\s+(?:know|remember)\s+about\s+me\b"
    r"|\bwhat\s+do\s+you\s+remember\b"
    r"|\blist\s+your\s+memor(?:y|ies)\b",
    re.IGNORECASE,
)


class MemorySkill(Skill):
    name = "memory"

    def __init__(self, memory, user_name: str):
        self.memory = memory
        self.user_name = user_name

    def matches(self, text: str) -> bool:
        return bool(
            _FORGET_ALL.search(text)
            or _RECALL.search(text)
            or _REMEMBER.match(text)
            or _FORGET.match(text)
        )

    def run(self, text: str) -> str:
        # Wipe everything.
        if _FORGET_ALL.search(text):
            n = self.memory.clear()
            if n:
                return f"Done -- I've cleared all {n} things I remembered about you."
            return "There wasn't anything stored yet, so nothing to clear."

        # Recite what's known.
        if _RECALL.search(text):
            facts = self.memory.all_texts()
            if not facts:
                return (
                    "Honestly, not much yet! Tell me about yourself and I'll "
                    "remember it for next time."
                )
            shown = facts[:15]
            listing = "\n".join(f"- {f}" for f in shown)
            extra = "" if len(facts) <= 15 else f"\n...and {len(facts) - 15} more."
            return f"Here's what I remember about you:\n{listing}{extra}"

        # Explicit remember.
        m = _REMEMBER.match(text)
        if m:
            fact = m.group(1).strip().rstrip(".")
            # Rewrite first person into third person so the stored fact reads well.
            normalised = self._to_third_person(fact)
            is_new = self.memory.add(normalised, core=True)
            if is_new:
                return f"Got it -- I'll remember that {fact}."
            return f"I already had that noted, but thanks for confirming: {fact}."

        # Explicit forget.
        m = _FORGET.match(text)
        if m:
            query = m.group(1).strip().rstrip(".")
            removed = self.memory.forget(query)
            if removed:
                listing = "; ".join(removed[:3])
                return f"Forgotten: {listing}."
            return f"I couldn't find anything about \"{query}\" in my memory."

        return ""

    _IRREGULAR = {
        "have": "has",
        "am": "is",
        "do": "does",
        "go": "goes",
        "was": "was",
        "will": "will",
        "can": "can",
        "would": "would",
        "like": "likes",
    }

    def _conjugate(self, verb: str) -> str:
        """Turn a first-person verb into third person ('love' -> 'loves')."""
        low = verb.lower()
        if low in self._IRREGULAR:
            return self._IRREGULAR[low]
        if low.endswith(("s", "x", "z", "ch", "sh")):
            return low + "es"
        if low.endswith("y") and len(low) > 1 and low[-2] not in "aeiou":
            return low[:-1] + "ies"  # study -> studies
        if low.endswith("e") or low.isalpha():
            return low + "s"
        return low

    def _to_third_person(self, fact: str) -> str:
        """Convert 'I love cricket' -> '<name> loves cricket' (best effort)."""
        f = fact.strip()
        name = self.user_name

        # "I am ..." / "I'm ..." -> "<name> is ..."
        m = re.match(r"^i(?:\s+am|'?m)\s+(.*)$", f, re.IGNORECASE)
        if m:
            return f"{name} is {m.group(1)}"

        # "my X ..." -> "<name>'s X ..."
        m = re.match(r"^my\s+(.*)$", f, re.IGNORECASE)
        if m:
            return f"{name}'s {m.group(1)}"

        # "I <verb> ..." -> "<name> <verb>s ..."
        m = re.match(r"^i\s+(\w+)(\s+.*)?$", f, re.IGNORECASE)
        if m:
            verb = self._conjugate(m.group(1))
            rest = m.group(2) or ""
            return f"{name} {verb}{rest}"

        return f
