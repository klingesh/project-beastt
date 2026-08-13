"""Checking that the figures in a reply came from somewhere.

A wrong sentence invites doubt. A wrong *number* does not -- it looks checkable,
so it gets believed and acted on. This is what the reported failure looked like:

    > how about top gainer stocks and loser stocks state 5 nos in india today
    According to Moneycontrol, here are 5 top gainer stocks ...
    1. Adani Enterprises - up 4.55%
    2. Vedanta - up 3.65%
    ...

No search ran for that question. Every figure was invented, to two decimal
places, with a publisher's name attached. The persona already forbids exactly
this, in as many words -- "Quoting a remembered number as though you had just
fetched it is the same failure as inventing a filename -- worse, because a number
looks checkable" -- and the instruction was simply not obeyed. A local 8B model
being told to be warm, helpful and conversational will fill a gap rather than
admit one.

So this does not ask the model anything. It extracts the figures from the reply
and checks each one appears in the material that was actually retrieved. That is
a pure function over two strings: no second model call, no added latency, and
testable against the real transcript.

Deliberately separate from "is the answer right", which is not decidable here.
The claim being tested is narrower and worth more: **every number in this reply
came from a source, and I can point at which one.**
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

#: A numeric literal, with optional thousands separators and decimals.
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

#: Currency markers that turn a small number into a claim worth checking. "under
#: 5" is chatter; "₹5" is a price.
_MONEY_BEFORE = re.compile(r"(?:₹|rs\.?|inr|\$|usd|us\$|€|£|¥)\s*$", re.IGNORECASE)

#: Units that do the same from the right: "5%" and "5 crore" are both claims.
#: `%` carries no word boundary of its own -- "5%\b" never matches, because there
#: is no word character after the sign -- so it is listed without one.
_UNIT_AFTER = re.compile(
    r"^\s*(?:%"
    r"|(?:percent|per\s?cent|bps|crore|lakhs?|billion|million|trillion"
    r"|bn|mn|kg|km|kb|mb|gb)\b)",
    re.IGNORECASE,
)

#: Below this, a bare integer is usually structure ("1.", "top 5", "1-4
#: sentences") rather than an assertion. With money or a unit attached it counts
#: however small it is.
TRIVIAL_BELOW = 10.0

#: Two figures match if they are equal to within this, relatively. Covers a
#: source printing 184.6 where the reply says 184.60, without letting 184 pass
#: for 185.
TOLERANCE = 0.001


def _to_float(raw: str) -> Optional[float]:
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None


def figures(text: str) -> List[str]:
    """Every numeric claim in `text`, as it was written.

    Structure is skipped -- list numbering and small bare counts -- because
    flagging "here are 5 stocks" would drown the real signal. Anything carrying
    money or a unit is kept regardless of size.
    """
    text = str(text or "")
    found: List[str] = []
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(0).rstrip(",")
        value = _to_float(raw)
        if value is None:
            continue

        before = text[max(0, match.start() - 6):match.start()]
        after = text[match.end():match.end() + 12]
        marked = bool(_MONEY_BEFORE.search(before)) or bool(_UNIT_AFTER.match(after))

        if not marked and "." not in raw and "," not in raw and value < TRIVIAL_BELOW:
            continue
        found.append(raw)
    return found


def _values(text: str) -> List[float]:
    out = []
    for match in _NUMBER_RE.finditer(str(text or "")):
        value = _to_float(match.group(0).rstrip(","))
        if value is not None:
            out.append(value)
    return out


def _appears_in(value: float, pool: Sequence[float]) -> bool:
    for candidate in pool:
        if candidate == value:
            return True
        scale = max(abs(value), abs(candidate), 1.0)
        if abs(candidate - value) / scale <= TOLERANCE:
            return True
    return False


@dataclass
class Verdict:
    """What the figures in a reply are backed by."""

    ok: bool
    #: Figures found in no source and not offered by the user either.
    invented: List[str] = field(default_factory=list)
    #: Figures whose only source is the user's own message. Not invented, but not
    #: verified -- repeating one back as fact is how "its 184.60" became
    #: "according to my latest update, the price is indeed ₹184.60".
    from_user: List[str] = field(default_factory=list)
    #: Figures traced to retrieved material.
    grounded: List[str] = field(default_factory=list)

    @property
    def unverified(self) -> List[str]:
        return [*self.invented, *self.from_user]

    def why(self) -> str:
        """A line for the log, or for telling the user what could not be stood up."""
        parts = []
        if self.invented:
            parts.append("not in any source: " + ", ".join(self.invented))
        if self.from_user:
            parts.append("only from the question itself: "
                         + ", ".join(self.from_user))
        return "; ".join(parts)


def check(reply: str, sources: Iterable[str] = (),
          question: str = "") -> Verdict:
    """Are the figures in `reply` traceable to `sources`?

    `question` is checked separately and never counts as grounding. A number the
    user supplied is theirs, not a finding, and stating it back as though it had
    been looked up is the same failure in a friendlier costume.
    """
    claimed = figures(reply)
    if not claimed:
        return Verdict(ok=True)

    pool: List[float] = []
    for source in sources or ():
        pool.extend(_values(source))
    asked = _values(question)

    invented, from_user, grounded = [], [], []
    for raw in claimed:
        value = _to_float(raw)
        if value is None:
            continue
        if _appears_in(value, pool):
            grounded.append(raw)
        elif _appears_in(value, asked):
            from_user.append(raw)
        else:
            invented.append(raw)

    return Verdict(ok=not invented and not from_user, invented=invented,
                   from_user=from_user, grounded=grounded)


#: Appended to a retry when the first attempt invented figures. Names them, so
#: the instruction is about something concrete rather than a general plea.
RETRY_INSTRUCTION = (
    "[Your previous answer contained these figures, which appear in none of the "
    "material above: {figures}.\n"
    "You have no source for them, so you must not state them. Rewrite the answer "
    "using only figures that appear above. If the material does not contain the "
    "numbers {user_name} asked for, say plainly that you could not find them and "
    "name where they could be checked. An answer that admits a gap is correct; an "
    "invented number is not.]"
)


def retry_note(verdict: Verdict, user_name: str = "you") -> str:
    return RETRY_INSTRUCTION.format(
        figures=", ".join(verdict.unverified) or "none",
        user_name=user_name,
    )


def caveat(verdict: Verdict) -> str:
    """What to append when even the retry could not stand the figures up.

    Kept as a visible admission rather than silently deleting the numbers: the
    reply may still be useful, and the user is owed the knowledge that part of it
    is unsourced.
    """
    if verdict.ok:
        return ""
    figures_text = ", ".join(verdict.unverified)
    return ("\n\n(I could not verify these figures against any source I "
            f"retrieved: {figures_text}. Please treat them as unconfirmed and "
            "check a live source before relying on them.)")
