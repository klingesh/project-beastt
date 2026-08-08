"""Telling an instruction apart from words that merely appear in a message.

Skills match on regular expressions, which works well for "create a flask
project called notes-api" and badly for a four-thousand-character document that
happens to contain the phrase "I can generate a package". That actually
happened: a pasted DaVinci Resolve grading spec caused a Python package called
"i-can-generate" to be scaffolded on disk, because the words were there.

A real instruction has a shape. It is short, and it starts with the request --
you do not bury "make me a website" in paragraph nine. So for anything that
takes an action, the match has to look like a directive rather than a quotation.
"""

from __future__ import annotations

import re

#: Beyond this the message is a document rather than a request.
LONG_MESSAGE = 400
#: An instruction lives at the front. Allow a little room for "hey Jarvis,".
HEAD = 160


def is_directive(text: str, match, head: int = HEAD,
                 long_message: int = LONG_MESSAGE) -> bool:
    """True when `match` is plausibly the user asking, not text they pasted.

    Short messages are always taken at face value -- that is the normal case,
    and being strict there would break ordinary requests. Only once a message is
    long enough to be a document does position start to matter.
    """
    if match is None:
        return False
    if len(text or "") <= long_message:
        return True
    return match.start() <= head


def directive(pattern: re.Pattern, text: str, **kwargs):
    """Search for `pattern`, returning the match only if it reads as an order."""
    found = pattern.search(text or "")
    return found if is_directive(text, found, **kwargs) else None


#: Words that are never the name of anything the user meant to create. Pulling a
#: name out of free prose is guesswork, and these are the giveaways that the
#: guess went wrong.
FILLER = frozenset({
    "i", "you", "we", "it", "he", "she", "they", "me", "my", "your", "our",
    "a", "an", "the", "this", "that", "these", "those",
    "can", "could", "will", "would", "should", "may", "might", "must",
    "and", "or", "but", "if", "so", "then", "than", "with", "without",
    "for", "of", "to", "in", "on", "at", "by", "from", "into", "about",
    "is", "are", "was", "were", "be", "been", "being", "do", "does", "did",
    "please", "also", "just", "very", "really", "containing", "contains",
    "including", "includes", "using", "based", "like", "such", "here", "there",
    "generate", "create", "make", "build", "write", "give", "prepare", "want",
    "need", "help", "let", "how", "what", "which", "when", "where", "why",
})


def plausible_name(words) -> bool:
    """Does this look like something a person would name a project?

    Rejects a phrase that opens with filler, which is what a greedy fallback
    produces when it has wandered into prose: "I can generate" is not a name.
    """
    cleaned = [w for w in (words or []) if w]
    if not cleaned:
        return False
    if cleaned[0].lower() in FILLER:
        return False
    # Mostly-filler is prose too, however it starts.
    filler = sum(1 for w in cleaned if w.lower() in FILLER)
    return filler * 2 <= len(cleaned)
