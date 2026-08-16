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

#: Filler in front of an instruction. Reported: three corrections in one message,
#: none of which fired, because the first line began "for fuck sake remember
#: this" and the patterns are anchored at the start of the whole message. The
#: model then answered "Got it, I'll keep it straight" and stored nothing, which
#: is the worst of the available outcomes -- the user believed it was fixed.
#:
#: Bounded to a known list rather than searching anywhere in the line. An
#: unanchored search for "remember" would let a pasted article file its contents
#: as facts, which is a bug this project has already had once.
_PREAMBLE = re.compile(
    r"^(?:"
    r"hey\s+[a-z]{3,10}|ok(?:ay)?|so|and|also|now|just|please|kindly|"
    r"listen|look|note|c'?mon|come\s+on|seriously|dude|mate|bro|"
    r"for\s+\w+(?:'s)?\s+sake|for\s+the\s+love\s+of\s+\w+|"
    r"i\s+(?:said|told\s+you)|again|damn|damn\s+it|"
    r"[^\w\s]+"
    r")\b[\s,:;.!-]*",
    re.IGNORECASE,
)

#: "remember this" immediately followed by another instruction is someone getting
#: your attention, not a fact whose content is the word "this". Only stripped when
#: a directive follows, so "remember this: i study MBA" keeps its fact.
_ATTENTION = re.compile(
    r"^remember\s+(?:this|that|these|it)\b[\s,:;.!-]*(?=(?:remember|forget)\b)",
    re.IGNORECASE,
)


def instructions(text: str):
    """Every memory instruction in a message, as (kind, body), in order.

    Line by line, each line still anchored. That is the whole design: it accepts
    the three corrections someone types on three lines, and a bit of swearing in
    front of them, without becoming an unanchored search that a pasted document
    could trip.
    """
    found = []
    for line in re.split(r"[\n\r]+", str(text or "")):
        line = line.strip()
        # Twice, because "for fuck sake" and "remember this" can stack -- and
        # twice rather than a loop, so a paragraph of filler cannot be peeled
        # away one word at a time until something matches.
        for _ in range(2):
            line = _PREAMBLE.sub("", line, count=1).strip()
            line = _ATTENTION.sub("", line, count=1).strip()
        if not line:
            continue
        for kind, pattern in (("remember", _REMEMBER), ("forget", _FORGET)):
            match = pattern.match(line)
            if match:
                body = match.group(1).strip().rstrip(".")
                if body:
                    found.append((kind, body))
                break
    return found


class MemorySkill(Skill):
    name = "memory"

    def __init__(self, memory, user_name: str):
        self.memory = memory
        self.user_name = user_name

    def matches(self, text: str) -> bool:
        return bool(
            _FORGET_ALL.search(text)
            or _RECALL.search(text)
            or instructions(text)
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

        # Remembering and forgetting, however many of each arrive at once.
        found = instructions(text)
        if not found:
            return ""

        lines = [self._apply(kind, body) for kind, body in found]
        if len(lines) == 1:
            return lines[0]
        # Itemised, because the failure this replaced was a confident "Got it"
        # over a store that had not changed. Each instruction reports separately
        # so a miss is visible instead of averaged away.
        return "\n".join(f"- {line}" for line in lines)

    def _apply(self, kind: str, body: str) -> str:
        if kind == "forget":
            removed = self.memory.forget(body)
            if removed:
                return f"Forgotten: {'; '.join(removed[:3])}."
            return f"Nothing stored about \"{body}\" -- so nothing to forget."

        fact = self._to_third_person(body)
        was_new, replaced = self.memory.remember_dictated(fact)
        if replaced:
            # Named, not counted. This is the sentence the user has been trying
            # to get rid of for two days; seeing it go is the confirmation.
            return (f"Got it -- {body}. That replaces: "
                    f"{'; '.join(replaced[:3])}.")
        if was_new:
            return f"Got it -- I'll remember that {body}."
        return f"Already had that one, but thanks for confirming: {body}."

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
