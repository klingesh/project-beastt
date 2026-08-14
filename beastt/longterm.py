"""Long-term memory -- lets BEASTT remember you across sessions.

Durable facts about the user are kept in a small JSON file on disk. Before each
reply, the most relevant facts are injected into the brain's context, so BEASTT
can recall things you told it days ago.

Design notes:
  * Facts are short, self-contained sentences ("Lingaa has an RTX 3050 laptop").
  * Recall is keyword-overlap based -- no embedding model needed, so this stays
    dependency-free and instant.
  * Near-duplicate facts are merged so the store doesn't grow unbounded.
  * Only *identity* facts -- what the user likes to be called -- are included
    unconditionally. See `is_identity` and `relevant` for why that distinction
    had to be drawn.
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


#: Plural forms that are not plurals, and words a naive rule would ruin.
_NEVER_STEM = {
    "analysis", "diagnosis", "thesis", "basis", "crisis", "series", "species",
    "news", "physics", "maths", "business", "address", "access", "progress",
    "gas", "bus", "plus", "less", "class", "glass", "pass", "chess", "boss",
    "always", "perhaps", "sometimes", "yes", "his", "hers", "ours", "yours",
    "this", "was", "has", "is", "its", "us",
}

_VOWELS = "aeiou"


def _undouble(base: str) -> str:
    """"runn" -> "run", but "attend" -> "attend"."""
    if len(base) > 2 and base[-1] == base[-2] and base[-1] not in "lsz":
        return base[:-1]
    return base


def _restore_e(base: str, was_doubled: bool) -> str:
    """Put back the "e" that -ed/-ing swallowed, where one was probably there.

    "lived" -> "liv" -> "live", but "running" -> "runn" -> "run" and not "rune".
    A doubled consonant is the signal: English doubles precisely to *stop* the
    preceding vowel being read as long, so a doubled base never wanted an "e".
    """
    if len(base) < 2:
        return base
    # A base left ending in "i" came from a "-y" verb, whatever its length:
    # studied -> studi -> study, carried -> carri -> carry, applied -> appli ->
    # apply. Checked before the length guard, which "studi" is too long for.
    if base[-1] == "i":
        return base[:-1] + "y"
    if was_doubled or len(base) > 4:
        return base
    if base[-1] not in _VOWELS + "yw" and base[-2] in _VOWELS:
        return base + "e"               # liv -> live, hop -> hope, mov -> move
    return base


def stem(word: str) -> str:
    """Collapse a word to a key for comparison. Deliberately shallow.

    Recall is exact-token overlap, which is what keeps it dependency-free and
    instant -- and meant that "where do I live" did not match "Lingesh lives in
    Chennai". Nothing was broken; the fact was there and simply never scored. From
    the outside that is indistinguishable from the assistant having forgotten, and
    it is the sort of thing a person notices and stops trusting.

    So: suffixes only, no dictionary, no dependency. Nowhere near a real stemmer,
    and it does not need to be -- the job is to make two spellings of the same word
    meet, not to do linguistics. Everything here is a rule that fires on both the
    question and the fact, so a mistake is at worst symmetrical.

    The conservative bits matter more than the clever ones. `_NEVER_STEM` exists
    because "analysis" is not a plural and "class" already ends in a doubled s, and
    a rule that strips an "s" from either would fuse unrelated facts -- this
    function also feeds the near-duplicate merge, where a false match silently
    destroys one of two distinct memories.
    """
    word = str(word or "")
    if len(word) <= 3 or word in _NEVER_STEM:
        return word

    for suffix, replacement in (("ies", "y"), ("sses", "ss"), ("ches", "ch"),
                                ("shes", "sh"), ("xes", "x"), ("zes", "z")):
        if word.endswith(suffix) and len(word) > len(suffix) + 1:
            return word[: -len(suffix)] + replacement

    if word.endswith("s") and not word.endswith(("ss", "us", "is", "ys")):
        return word[:-1]                # lives -> live, laptops -> laptop

    if word.endswith("ing") and len(word) > 5:
        base = word[:-3]
        return _restore_e(_undouble(base), _undouble(base) != base)

    if word.endswith("ed") and len(word) > 4:
        base = word[:-2]
        return _restore_e(_undouble(base), _undouble(base) != base)

    return word


def _tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {stem(w) for w in words if w not in _STOPWORDS and len(w) > 2}


#: Ways of recording what someone is called. Deliberately narrow.
_IDENTITY_PATTERNS = (
    r"likes? to be called",
    r"prefers? to be called",
    r"wants? to be called",
    r"prefers? the name",
    r"goes by",
    r"is known as",
    r"name is",
)
_IDENTITY_RE = re.compile("|".join(_IDENTITY_PATTERNS), re.IGNORECASE)

#: Heads that mean "this is about the user" even without their name in the text.
_SELF_SUBJECTS = {"", "the user", "user"}

#: At most this many identity facts bypass relevance, so the always-on set
#: cannot quietly grow back into the every-turn dossier this replaced.
MAX_IDENTITY = 3


def is_identity(text: str, user_name: str = "") -> bool:
    """Does this fact say what the *user* is called?

    This is the only class of fact that belongs in every single reply, and
    drawing the line here matters more than it looks.

    Recall used to include every fact marked `core`, unconditionally, on the
    reasoning that "who someone is and what they like to be called are relevant
    to every reply". That reasoning is sound; the flag was not. `core` is set by
    exactly one thing -- MemorySkill, when the user says "remember that ..." --
    so anything the user ever asked to be remembered became permanent every-turn
    context. Told once to remember a friend, the assistant then asked after her
    in reply to "nothing much", to a question about Tamil Nadu news, and to a
    complaint about wrong stock prices. Worse, the same facts were handed to the
    research planner as its context, which went and researched the user's own
    nickname and reported back about a 2014 Rajinikanth film.

    So the test is narrow on purpose: an identity phrase, with the *user* as its
    subject. "Lingaa likes to be called Lingaa" passes. "Prahadhesvaryaa is
    Lingaa's close friend" does not -- it is still remembered, still recalled the
    moment she is mentioned, and no longer volunteered when she is not.
    """
    match = _IDENTITY_RE.search(text or "")
    if not match:
        return False
    head = str(text)[: match.start()].strip().lower().strip(",'\"")
    name = str(user_name or "").strip().lower()
    if name and name in head:
        return True
    # A fact stored without a subject ("likes to be called Lingaa") is about the
    # user; one whose subject is somebody else is not.
    return head in _SELF_SUBJECTS


class LongTermMemory:
    """A tiny persistent fact store with relevance-based recall."""

    def __init__(self, path: str = "beastt_memory/memory.json", max_facts: int = 300,
                 user_name: str = ""):
        # Resolve against the project so a background service launched from an
        # arbitrary directory still finds the same memory file.
        from .paths import resolve

        self.path = str(resolve(path))
        self.max_facts = max_facts
        #: Needed to tell "what the user is called" from "what somebody else is
        #: called" -- see is_identity().
        self.user_name = str(user_name or "")
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

    def remember_statement(self, statement, ) -> List[str]:
        """Store a fact offered in conversation, replacing what it contradicts.

        Returns the facts it displaced, so the caller can say what changed.

        The replacing is the part that matters, and the reason `add()` alone was
        not enough. Told "i live in chennai" after a stored "Lingaa lives with
        Prahadhesvaryaa K S", `add()` kept both -- their token overlap is 0.67,
        under the 0.80 merge threshold -- and recall then returned the pair, which
        is how "where do i live" came back answering who with and not where.

        Facts sharing a `key` are alternatives, so the newest wins. Facts without
        one accumulate, because somebody can own two laptops but does not live in
        two cities.
        """
        text = " ".join(str(getattr(statement, "text", statement) or "").split())
        key = str(getattr(statement, "key", "") or "")
        if len(text) < 3:
            return []

        replaced: List[str] = []
        if key:
            from .statements import infer_key

            keep = []
            for fact in self.facts:
                # Keys are new, so anything already on disk has none and its key
                # has to be read back off the sentence. Without that, superseding
                # would work perfectly in tests and never once on a real install.
                existing = fact.get("key") or infer_key(fact["text"])
                if existing == key and fact["text"] != text:
                    replaced.append(fact["text"])
                else:
                    keep.append(fact)
            self.facts = keep

        self.add(text)
        # add() may have merged into an existing entry, so find it by text.
        for fact in self.facts:
            if fact["text"] == text:
                fact["key"] = key
                break
        self.save()
        return replaced

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
    def identity_facts(self) -> List[str]:
        """The facts that belong in every reply: what the user is called."""
        found = [f["text"] for f in self.facts
                 if is_identity(f["text"], self.user_name)]
        return found[:MAX_IDENTITY]

    def relevant(self, query: str, limit: int = 8) -> List[str]:
        """Facts relevant to `query`, plus the user's own name.

        Two rules, and the second one is the fix for a real failure:

        * **Identity facts always go through** -- what someone likes to be called
          bears on every reply.
        * **Everything else must earn its place by matching the question.** That
          includes facts the user explicitly asked to be remembered. Being asked
          to remember something means keep it, not recite it: a fact about a
          friend is recalled the moment she comes up and stays quiet when she
          does not. Previously every `core` fact was injected on every turn, and
          the model, handed a name, found a reason to use it.

        Being `core` still counts for something -- a slight scoring boost, and
        protection from eviction -- it just no longer bypasses relevance.
        """
        q = _tokens(query)
        always = self.identity_facts()
        picked = list(always)
        already = set(always)

        scored = []
        for fact in self.facts:
            if fact["text"] in already:
                continue
            ft = _tokens(fact["text"])
            if not ft:
                continue
            overlap = len(ft & q)
            if not overlap:
                continue
            score = overlap / len(ft)
            if fact.get("core"):
                # Asked for deliberately, so preferred among things that match --
                # but it still has to match.
                score += 0.25
            scored.append((score, fact))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        for _score, fact in scored:
            if len(picked) >= limit:
                break
            picked.append(fact["text"])

        # No padding with recent facts.
        #
        # There used to be a fallback here: if fewer than `limit` facts matched, it
        # topped the list up with the most recently updated ones. Because a match is
        # rare and the limit is eight, that fallback fired on almost every turn --
        # so a question about generating an image arrived with seven unrelated facts
        # attached, and the model, handed facts, found a way to use them. It
        # produced replies that recommended a particular friend for artistic advice
        # and read her mood from a greeting. She had nothing to do with either
        # conversation.
        #
        # Relevance was the whole idea of this method; padding to a quota
        # guaranteed the opposite. When nothing matches, the right answer is
        # nothing.
        #
        # Removing the padding was not enough on its own, though: every `core`
        # fact still went through unconditionally, and `core` means "the user
        # asked me to remember this" rather than "this is who the user is". So the
        # same friend came back through the other door -- volunteered in reply to
        # "nothing much", to a question about state news, and to a complaint about
        # wrong share prices. Only identity facts bypass relevance now.
        #
        # "What do you remember about me?" is unaffected -- MemorySkill answers
        # that directly and does not come through here.
        return picked

    def all_texts(self) -> List[str]:
        return [f["text"] for f in self.facts]

    def __len__(self) -> int:
        return len(self.facts)
