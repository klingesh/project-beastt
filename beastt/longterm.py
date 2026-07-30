"""Long-term memory -- lets BEASTT remember you across sessions.

Durable facts about the user are kept in a small JSON file on disk. Before each
reply, the most relevant facts are injected into the brain's context, so BEASTT
can recall things you told it days ago.

Design notes:
  * Facts are short, self-contained sentences ("Lingaa has an RTX 3050 laptop").
  * Recall is keyword-overlap based -- no embedding model needed, so this stays
    dependency-free and instant. Facts marked `core` are always included.
  * Near-duplicate facts are merged so the store doesn't grow unbounded.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional

_STOPWORDS = {
    "a", "an", "the", "i", "im", "i'm", "me", "my", "mine", "you", "your", "is",
    "am", "are", "was", "were", "be", "been", "to", "of", "in", "on", "at", "for",
    "and", "or", "but", "it", "its", "this", "that", "these", "those", "with",
    "do", "does", "did", "so", "just", "very", "really", "have", "has", "had",
    "what", "who", "when", "where", "how", "why", "can", "could", "would", "will",
    "about", "like", "get", "got", "there", "here", "he", "she", "they", "we",
}


def _tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


class LongTermMemory:
    """A tiny persistent fact store with relevance-based recall."""

    def __init__(self, path: str = "beastt_memory/memory.json", max_facts: int = 300):
        # Resolve against the project so a background service launched from an
        # arbitrary directory still finds the same memory file.
        from .paths import resolve

        self.path = str(resolve(path))
        self.max_facts = max_facts
        self.facts: List[Dict] = []
        self._load()

    # --- persistence ------------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.facts = data.get("facts", [])
        except Exception as exc:
            print(f"[memory] Couldn't read memory file ({exc}); starting fresh.")
            self.facts = []

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"facts": self.facts}, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            print(f"[memory] Couldn't save memory ({exc}).")

    # --- writing ----------------------------------------------------------
    def add(self, text: str, core: bool = False) -> bool:
        """Store a fact. Returns True if it was new."""
        text = " ".join(text.strip().split())
        if len(text) < 3:
            return False

        new_tokens = _tokens(text)
        for fact in self.facts:
            existing = _tokens(fact["text"])
            if not existing or not new_tokens:
                continue
            overlap = len(existing & new_tokens) / max(1, min(len(existing), len(new_tokens)))
            if overlap >= 0.8:  # near-duplicate -> refresh instead of duplicating
                fact["text"] = text if len(text) > len(fact["text"]) else fact["text"]
                fact["updated"] = time.time()
                if core:
                    fact["core"] = True
                self.save()
                return False

        self.facts.append(
            {
                "text": text,
                "core": core,
                "created": time.time(),
                "updated": time.time(),
            }
        )
        # Keep the store bounded: drop the oldest non-core facts first.
        if len(self.facts) > self.max_facts:
            non_core = [f for f in self.facts if not f.get("core")]
            non_core.sort(key=lambda f: f.get("updated", 0))
            for fact in non_core[: len(self.facts) - self.max_facts]:
                self.facts.remove(fact)
        self.save()
        return True

    def forget(self, query: str) -> List[str]:
        """Remove facts matching a query. Returns the removed fact texts."""
        q = _tokens(query)
        if not q:
            return []
        removed = []
        keep = []
        for fact in self.facts:
            ft = _tokens(fact["text"])
            score = len(ft & q) / max(1, len(q))
            if score >= 0.5:
                removed.append(fact["text"])
            else:
                keep.append(fact)
        if removed:
            self.facts = keep
            self.save()
        return removed

    def clear(self) -> int:
        n = len(self.facts)
        self.facts = []
        self.save()
        return n

    # --- reading ----------------------------------------------------------
    def relevant(self, query: str, limit: int = 8) -> List[str]:
        """Return facts most relevant to `query`, always including core facts."""
        q = _tokens(query)
        core = [f for f in self.facts if f.get("core")]
        others = [f for f in self.facts if not f.get("core")]

        scored = []
        for fact in others:
            ft = _tokens(fact["text"])
            if not ft:
                continue
            overlap = len(ft & q)
            if overlap:
                scored.append((overlap / len(ft), fact))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        picked = [f["text"] for f in core]
        for _score, fact in scored:
            if len(picked) >= limit:
                break
            picked.append(fact["text"])

        # If nothing matched, fall back to the most recently updated facts.
        if len(picked) < min(limit, len(self.facts)):
            recent = sorted(others, key=lambda f: f.get("updated", 0), reverse=True)
            for fact in recent:
                if len(picked) >= limit:
                    break
                if fact["text"] not in picked:
                    picked.append(fact["text"])
        return picked

    def all_texts(self) -> List[str]:
        return [f["text"] for f in self.facts]

    def __len__(self) -> int:
        return len(self.facts)
