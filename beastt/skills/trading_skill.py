"""Answering "how's my bot?".

Read-only, and deliberately so. This reports what the trading bot published about
itself and nothing more: it cannot place, close or modify a trade, and it will not
be extended to. A language model in the order path is a bad idea however good the
reporting around it gets.

It also does not offer opinions on the positions it reports. Stating that Brent is
short and 2.80 up is a fact; suggesting what to do about it would be investment
advice, which is not this program's business.
"""

from __future__ import annotations

import re

from .base import Skill
from .intent import directive

#: Things that mean "the trading bot", not trading in general.
_BOT = (r"bot|trading\s*bot|tradingbot|trade\s*bot|mt5|metatrader|"
        r"ea\b|expert\s+advisor|algo")

#: Asking about its state.
_ASK = re.compile(
    r"\b(?:how(?:'?s| is| are)|what(?:'?s| is)|status\s+of|check(?:\s+on)?|"
    r"is|are|any|show|tell\s+me\s+about|update\s+on)\b"
    r"[^.\n]{0,40}?"
    rf"\b(?:{_BOT})\b",
    re.IGNORECASE,
)

#: Asking about trades without naming the bot. "any trades?" needs no further
#: explanation when a bot is the only thing placing them -- and this skill is only
#: registered at all when one is configured.
_ASK_TRADES = re.compile(
    r"\b(?:any|what|which|show|list|how\s+many|got\s+any)\b"
    r"[^.\n]{0,24}?"
    r"\b(?:trades?|positions?|orders?|entries)\b",
    re.IGNORECASE,
)

#: Or naming it outright: "bot status", "trading bot report".
_DIRECT = re.compile(
    rf"\b(?:{_BOT})\b\s*(?:status|report|health|update|equity|drawdown|"
    r"positions?|trades?|doing|running|alive|ok)\b",
    re.IGNORECASE,
)

#: Requests to *act*. Refused explicitly rather than falling through to the model,
#: which would answer as though it might comply.
#: Note the gaps use [^\n] rather than [^.\n]. Excluding the full stop looked
#: tidier but meant the pattern could not span a decimal, so "sell 0.04 lots of
#: brent" slipped through to the model, which would then answer as though it might
#: comply. In this skill a false refusal is harmless; a missed one is not.
_ACT = re.compile(
    # an action verb reaching a trading noun
    r"\b(?:close|open|place|enter|exit|buy|sell|short|long|modify|move|cancel|"
    r"increase|decrease|double|halve|hedge|scale|reverse|liquidate)\b"
    r"[^\n]{0,30}\b(?:position|positions|trade|trades|order|orders|lot|lots|"
    r"sl|tp|stop|target|entry)\b"
    # or opening a direction outright: "open a buy on gold"
    r"|\b(?:open|place|enter|put|take)\b\s*(?:a|an|the)?\s*"
    r"\b(?:buy|sell|long|short)\b"
    # or a direction with a size: "sell 0.04 of brent"
    r"|\b(?:buy|sell|short|long)\b\s*[\d.]+"
    # or controlling the process itself
    rf"|\b(?:stop|start|restart|pause|resume|kill|disable|enable|shut\s*down)\b"
    rf"[^\n]{{0,20}}\b(?:{_BOT})\b",
    re.IGNORECASE,
)


class TradingBotSkill(Skill):
    name = "trading-bot"

    def __init__(self, config):
        self.config = config

    def matches(self, text: str) -> bool:
        from ..trading import configured

        if not configured(self.config):
            return False
        body = text or ""
        if directive(_ACT, body) is not None:
            return True
        return (directive(_ASK, body) is not None
                or directive(_DIRECT, body) is not None
                or directive(_ASK_TRADES, body) is not None)

    def run(self, text: str) -> str:
        from ..trading import MonitorError, fetch_status, summarise

        if directive(_ACT, text or "") is not None:
            # Say what it will not do, and why, rather than letting the model
            # improvise something that sounds like agreement.
            return (
                "I only watch the bot — I can't place, close or change trades, "
                "and I'm not going to be able to. Anything in the order path "
                "needs to be your decision, made in MT5 or in the bot's own "
                "config.\n"
                "Ask me \"how's my bot?\" and I'll tell you exactly where it "
                "stands."
            )

        try:
            status = fetch_status(self.config)
        except MonitorError as exc:
            return f"I couldn't read the bot's status: {exc}"

        return summarise(self.config, status)
