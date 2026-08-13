"""Turning "i live in chennai" into something that is still known tomorrow.

Reported, after a restart:

    > so jarvis where do i live
    You live with Prahadhesvaryaa K S, but I don't recall you mentioning the
    exact location -- would you like to share that with me, Lingaa?

Chennai had been said, and was gone. Three faults met to produce that answer.

**Nothing said in the browser was ever remembered.** `remember_session()` is
called from four places in `cli.py` and from nowhere else, so the entire web
interface wrote no facts at all -- and in the CLI it only runs on a *graceful*
exit, so the `taskkill` in the documented restart procedure loses the session too.

**A plain statement was never captured.** `MemorySkill` needs the literal word
"remember", so "i live in chennai" only had a chance via end-of-session
reflection, which needs a model call and a clean exit.

**And the entry before this one closed half a loop.** It correctly identified a
first-person statement as *a fact being offered, to be remembered* -- stopped
researching it, and then did not remember it. The judgement was right and only
half-used.

So this captures them at the moment they are said, deterministically: no model
call, no waiting for the session to end, and no dependency on how the process
dies. A model is still better at distilling a whole conversation, and still does
that -- but "I live in Chennai" does not need a language model to be understood,
and making it depend on one is how it came to be lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

#: Trailing noise to cut off a captured value. "i live in pg not casagrand"
#: should record a PG, not "pg not casagrand".
_TAIL_CUT = re.compile(
    r"\s+(?:not|but|though|although|however|fyi|btw|now|anymore|any more)\b"
    r"|[,;.!?]",
    re.IGNORECASE,
)

#: Words that mean the sentence is about right now rather than about a life.
#: "i'm at college attending my classes" is conversation; "i study at college"
#: is a fact. Getting this wrong fills the store with weather reports.
_TRANSIENT = re.compile(
    r"\b(?:today|tonight|right now|at the moment|currently|just now|this "
    r"morning|this afternoon|this evening|for now|at present|about to|going to|"
    r"gonna|later|tomorrow|yesterday)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Statement:
    """A durable fact offered in conversation."""

    text: str
    #: Facts sharing a key are alternatives, so a new one replaces the old. Empty
    #: means the fact accumulates -- someone can own two laptops, but they do not
    #: live in two cities.
    key: str = ""


#: Values that match a pattern but carry nothing worth keeping. "i have to go"
#: fits "i have X" perfectly and would be filed as a fact about the user having
#: "to go"; "i like it" records a preference for an unknown antecedent. A store
#: full of these is worse than an empty one, because recall then spends its
#: budget on them.
_USELESS_VALUE = re.compile(
    r"^(?:to\s+\w+"                                  # to go, to leave
    r"|it|that|this|these|those|them|one|some|any|none|nothing|something"
    r"|a lot|lots|much|more|less|enough|so much"
    r"|question|questions|doubt|doubts|problem|problems|issue|issues"
    r"|idea|ideas|thought|thoughts|feeling|feelings|opinion|opinions"
    r"|minute|moment|second|sec|while|bit|time|day|days"
    r"|no idea|not sure|nothing much)$",
    re.IGNORECASE,
)


def _clean(value: str) -> str:
    value = _TAIL_CUT.split(str(value or ""), maxsplit=1)[0]
    value = re.sub(r"\s+", " ", value).strip(" ,.'\"")
    return value


#: (pattern, template, key). Order matters: the more specific reading first, so
#: "i live with my parents in chennai" is not read as living in "my parents".
_PATTERNS = (
    # Where someone lives. Two keys, not one: a place and a household are both
    # true at once, and collapsing them would let "i live with my parents" erase
    # the city.
    (r"^i(?:'m| am)? ?(?:live|living|based|stay|staying)\s+(?:in|at)\s+(?:a\s+|an\s+|the\s+)?(.+)",
     "{user} lives in {value}", "lives-in"),
    (r"^i(?:'m| am)? ?(?:live|living|stay|staying)\s+with\s+(.+)",
     "{user} lives with {value}", "lives-with"),
    (r"^i(?:'ve| have)? ?(?:just )?moved to\s+(.+)",
     "{user} lives in {value}", "lives-in"),
    # Work and study.
    (r"^i\s+(?:work|am working|'m working)\s+(?:at|for|in)\s+(.+)",
     "{user} works at {value}", "works-at"),
    (r"^i\s+(?:study|am studying|'m studying)\s+(?:at\s+)?(.+)",
     "{user} studies {value}", "studies"),
    (r"^i(?:'m| am)\s+(?:a|an)\s+(.+?)\s+(?:student|by profession)$",
     "{user} studies {value}", "studies"),
    # Identity.
    (r"^my name(?:'s| is)\s+(.+)", "{user}'s name is {value}", "name"),
    (r"^i(?:'m| am)\s+from\s+(.+)", "{user} is from {value}", "from"),
    (r"^my birthday(?:'s| is)\s+(?:on\s+|in\s+)?(.+)",
     "{user}'s birthday is {value}", "birthday"),
    (r"^i(?:'m| am)\s+(\d{1,2})\s+years old", "{user} is {value} years old", "age"),
    # Things that accumulate -- no key, so they never replace one another.
    (r"^i\s+(?:have|have got|'ve got|own)\s+(?:a\s+|an\s+|the\s+)?(.+)",
     "{user} has {value}", ""),
    (r"^i\s+(?:use|am using|'m using)\s+(.+)", "{user} uses {value}", ""),
    (r"^i\s+(?:drive|ride)\s+(?:a\s+|an\s+)?(.+)", "{user} drives {value}", ""),
    (r"^i\s+(?:speak|know)\s+(.+?)\s+(?:fluently|language|languages)$",
     "{user} speaks {value}", ""),
    (r"^i\s+(?:like|love|enjoy|prefer)\s+(.+)", "{user} likes {value}", ""),
    (r"^i\s+(?:hate|dislike|can't stand|cannot stand)\s+(.+)",
     "{user} dislikes {value}", ""),
    (r"^i(?:'m| am)\s+allergic to\s+(.+)", "{user} is allergic to {value}", ""),
    (r"^i\s+(?:run|am building|'m building|am working on|'m working on)\s+"
     r"(?:a\s+|an\s+|the\s+)?(.+)",
     "{user} is working on {value}", ""),
)
_COMPILED = tuple((re.compile(p, re.IGNORECASE), t, k) for p, t, k in _PATTERNS)

#: Denials, which should remove rather than add. "i don't live with X" is how
#: someone corrects a fact that was wrong -- and the reported session had one:
#: a living arrangement invented from the fact that somebody was a friend.
_DENIALS = (
    r"^i\s+(?:don't|do not|dont)\s+(?:live|stay)\s+(?:in|at|with)\s+(.+)",
    r"^i\s+(?:no longer|don't|do not|dont)\s+(?:work|study)\s+(?:at|for|in)\s+(.+)",
    r"^i\s+(?:no longer|never)\s+(?:live|stay|work|study)\s+(?:in|at|with|for)?\s*(.+)",
    r"^i\s+(?:don't|do not|dont)\s+(?:have|own|use|like|drive)\s+(?:a\s+|an\s+)?(.+)",
    r"^i(?:'m| am)\s+not\s+(?:from|allergic to)\s+(.+)",
)
_DENIAL_RE = tuple(re.compile(p, re.IGNORECASE) for p in _DENIALS)


def _normalise(text: str, name: str = "") -> str:
    """Strip the assistant's name and tidy, so the patterns can be anchored."""
    from .search import strip_assistant_name

    body = strip_assistant_name(text, name)
    body = re.sub(r"^\s*(?:so|and|but|ok|okay|well|umm?|hey|also|yeah|yes)\b[\s,]*",
                  "", body, flags=re.IGNORECASE)
    body = re.sub(r"\bi'?m\b", "i am", body, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", body).strip()


def fact_from(text: str, user_name: str = "you", name: str = "") -> Optional[Statement]:
    """The durable fact a statement offers, or None.

    Anchored at the start on purpose. An unanchored match would read "the article
    says people who live in chennai ..." as the user's address, which is the same
    mistake as searching the web for a statement.
    """
    body = _normalise(text, name)
    if not body or _TRANSIENT.search(body):
        return None
    # "i live in chennai?" is someone checking what is known, not telling.
    # Checked here rather than relying on the caller, because the trailing
    # punctuation is stripped from the captured value and would be invisible by
    # the time the fact was built.
    if "?" in body:
        return None

    for pattern, template, key in _COMPILED:
        match = pattern.match(body)
        if not match:
            continue
        value = _clean(match.group(1))
        # Two characters is a typo, and a whole paragraph is not a fact.
        if len(value) < 2 or len(value) > 90:
            return None
        if _USELESS_VALUE.match(value):
            return None
        return Statement(
            text=template.format(user=user_name or "The user", value=value),
            key=key,
        )
    return None


#: The same predicates, read back off a stored third-person fact.
#:
#: Needed because keys are new and every fact already on disk predates them.
#: Without this the whole superseding mechanism is dead on any real install: the
#: reported store held "Lingaa lives with Prahadhesvaryaa K S" written by
#: end-of-session reflection, and a new keyed fact had nothing to compare it to.
_INFER = (
    (r"\blives?\s+in\b", "lives-in"),
    (r"\blives?\s+with\b", "lives-with"),
    (r"\bworks?\s+(?:at|for)\b", "works-at"),
    (r"\bstud(?:ies|ying)\b", "studies"),
    (r"'s name is\b|\bname is\b", "name"),
    (r"\bis from\b", "from"),
    (r"\bbirthday is\b", "birthday"),
    (r"\bis \d{1,2} years old\b", "age"),
)
_INFER_RE = tuple((re.compile(p, re.IGNORECASE), k) for p, k in _INFER)


def infer_key(fact_text: str) -> str:
    """The key a stored fact would have been given, or "" if it accumulates."""
    text = str(fact_text or "")
    for pattern, key in _INFER_RE:
        if pattern.search(text):
            return key
    return ""


def denial_from(text: str, name: str = "") -> str:
    """What a correction is denying, as a query for forget(), or "".

    Kept separate from fact_from because the two are not opposites: "i don't live
    with Prahadhesvaryaa" should remove a fact, not store the absence of one.
    """
    body = _normalise(text, name)
    for pattern in _DENIAL_RE:
        match = pattern.match(body)
        if match:
            value = _clean(match.group(1))
            if len(value) >= 2:
                return value
    return ""


def describe(statement: Statement, replaced: List[str]) -> str:
    """A short line for the log, so capture is visible rather than magical."""
    if replaced:
        return f"noted: {statement.text} (replacing {len(replaced)})"
    return f"noted: {statement.text}"
