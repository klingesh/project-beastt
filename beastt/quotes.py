"""Live market quotes, so a share price is fetched rather than remembered.

`data.py` covers published macro statistics -- FRED and the World Bank -- which is
the right source for inflation or GDP but has nothing to say about an equity. So
asked "whats tata steel current share price", nothing was retrieved at all, and
the only thing left to answer was the model. It answered Rs 117.45, then Rs
184.60 after being corrected, and described both as coming from Moneycontrol.

The shape of the fix is the same as `data.py`'s: fetch the real number, carry its
source and timestamp with it, and let the reply cite them. A price without a
timestamp is barely better than a guess, because the interesting question is
always "as of when".

Two backends, both keyless:

  1. **Yahoo Finance's chart endpoint**, which also gives the previous close, so
     the change can be computed here rather than by a language model.
  2. **Stooq's CSV**, as a fallback.

Both are undocumented public endpoints rather than contracted APIs. They are used
because they need no key, which is what makes this work on a laptop with nothing
configured -- but they can change without notice, so every failure path here is
non-fatal and says which backend failed. If they ever stop working the honest
outcome is no quote, which the grounding check then turns into "I couldn't find
it" rather than an invented price.

Name resolution goes through Yahoo's search endpoint rather than a hard-coded
table, so "tata steel", "reliance" and "apple" all work without this module
having to know about them in advance. Indian names prefer the NSE listing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

TIMEOUT = 12
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"}

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
STOOQ_URL = "https://stooq.com/q/l/"


class QuoteError(RuntimeError):
    """No quote could be had. Carries something worth showing a human."""


# --- what counts as a quote question ---------------------------------------
_PRICE_WORDS = (
    r"share\s*price|stock\s*price|price\s+of|current\s+price|market\s+price|"
    r"trading\s+at|quote|ltp|last\s+traded|closing\s+price|close(?:d)?\s+at|"
    r"how\s+much\s+is|what'?s\s+.{0,20}\btrading|valuation\s+today"
)
_MARKET_WORDS = (
    r"\b(?:share|shares|stock|stocks|equity|equities|market|markets|nifty|"
    r"sensex|nasdaq|dow|s&p|index|indices|bse|nse|ticker|crypto|bitcoin|"
    r"ethereum)\b"
)
_QUOTE_RE = re.compile(f"(?:{_PRICE_WORDS})", re.IGNORECASE)
_MARKET_RE = re.compile(_MARKET_WORDS, re.IGNORECASE)

#: "top gainers", "biggest losers" -- a ranked list, which a per-symbol quote
#: endpoint cannot answer. Recognised so the caller can say so instead of
#: pretending, which is what produced the invented five-and-five list.
#: The gap is generous and spans a verb phrase because the reported question was
#: "top stocks that has gained" -- the ranking word and the movement word had
#: five words between them, and matching only the noun "gainer" missed it.
_RANKED_RE = re.compile(
    r"\b(?:top|best|biggest|worst|highest|lowest|most)\b[^.\n]{0,40}"
    r"\b(?:gainer|gainers|gained|gaining|gains|loser|losers|lost|losing|"
    r"mover|movers|performer|performers|advance|advances|decline|declines|"
    r"rise|risen|fell|fallen|dropped)\b|"
    r"\b(?:gainer|gainers|loser|losers|movers)\b",
    re.IGNORECASE,
)


def wanted(text: str) -> bool:
    """Is this a question a live quote would answer?"""
    text = str(text or "")
    if _QUOTE_RE.search(text):
        return True
    # "how is nifty doing", "sensex today" -- a market word plus a present-tense
    # ask is enough; the words alone are not, or every mention of the stock
    # market would trigger a fetch.
    return bool(_MARKET_RE.search(text)) and bool(
        re.search(r"\b(?:today|now|current(?:ly)?|latest|live|at\s+the\s+moment|"
                  r"doing|up|down|open|closed?)\b", text, re.IGNORECASE))


def is_ranked_list(text: str) -> bool:
    """Asking for gainers/losers, which needs a screener rather than a quote."""
    return bool(_RANKED_RE.search(str(text or "")))


#: Indices and crypto, where a plain-language name resolves badly through search.
_ALIASES = {
    "nifty": "^NSEI", "nifty 50": "^NSEI", "nifty50": "^NSEI",
    "bank nifty": "^NSEBANK", "nifty bank": "^NSEBANK",
    "sensex": "^BSESN", "bse sensex": "^BSESN",
    "dow": "^DJI", "dow jones": "^DJI",
    "nasdaq": "^IXIC", "s&p 500": "^GSPC", "sp500": "^GSPC",
    "ftse": "^FTSE", "nikkei": "^N225",
    "bitcoin": "BTC-USD", "btc": "BTC-USD",
    "ethereum": "ETH-USD", "eth": "ETH-USD",
    "dogecoin": "DOGE-USD", "solana": "SOL-USD",
    "gold": "GC=F", "silver": "SI=F", "crude": "CL=F", "brent": "BZ=F",
    "usd inr": "USDINR=X", "dollar rupee": "USDINR=X",
    "rupee": "USDINR=X", "eur usd": "EURUSD=X",
}

#: Stripped before a name is sent to the resolver.
_NOISE_RE = re.compile(
    r"\b(?:what|whats|what's|is|the|current|share|shares|stock|stocks|price|"
    r"of|for|today|now|latest|live|tell|me|give|get|please|can|you|how|much|"
    r"doing|about|update|quote|value|worth|ltp|trading|at|in|india|indian|"
    r"and|a|an|its|it)\b",
    re.IGNORECASE,
)


def extract_names(text: str) -> List[str]:
    """The instruments a question is asking about, in the words used.

    Aliases are checked against the whole question first, because "nifty 50" must
    not be reduced to "nifty" and then to a company search.
    """
    text = str(text or "")
    lowered = text.lower()
    found: List[str] = []

    for alias in sorted(_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            if not any(alias in seen for seen in found):
                found.append(alias)

    # An explicit ticker, e.g. "TATASTEEL.NS" or "AAPL".
    for match in re.finditer(r"\b([A-Z]{2,12}(?:\.[A-Z]{2})?)\b", text):
        token = match.group(1)
        if token.lower() not in ("i", "a", "the", "usd", "inr", "nse", "bse"):
            found.append(token)

    if found:
        return found[:4]

    # Otherwise whatever is left once the question words are removed.
    remainder = _NOISE_RE.sub(" ", text)
    remainder = re.sub(r"[^\w\s&.-]", " ", remainder)
    remainder = re.sub(r"\s+", " ", remainder).strip()
    return [remainder] if len(remainder) > 1 else []


# --- parsing (pure, so it can be tested without the network) ---------------
@dataclass
class Quote:
    """One instrument's price, with enough provenance to be quotable."""

    symbol: str
    name: str = ""
    price: Optional[float] = None
    previous_close: Optional[float] = None
    currency: str = ""
    exchange: str = ""
    as_of: str = ""
    source: str = ""
    source_url: str = ""

    @property
    def change(self) -> Optional[float]:
        if self.price is None or self.previous_close is None:
            return None
        return self.price - self.previous_close

    @property
    def change_percent(self) -> Optional[float]:
        if self.change is None or not self.previous_close:
            return None
        return self.change / self.previous_close * 100.0

    def as_text(self) -> str:
        """One line, with the figures the model is allowed to quote."""
        label = f"{self.name} ({self.symbol})" if self.name else self.symbol
        money = f"{self.price:,.2f}" if self.price is not None else "unavailable"
        parts = [f"{label}: {money} {self.currency}".strip()]
        if self.previous_close is not None:
            parts.append(f"previous close {self.previous_close:,.2f}")
        if self.change is not None and self.change_percent is not None:
            parts.append(f"change {self.change:+,.2f} ({self.change_percent:+.2f}%)")
        if self.exchange:
            parts.append(f"exchange {self.exchange}")
        if self.as_of:
            parts.append(f"as of {self.as_of}")
        line = ", ".join(parts)
        if self.source:
            line += f" [{self.source}"
            line += f" — {self.source_url}]" if self.source_url else "]"
        return line


def _stamp(epoch: Any) -> str:
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result  # reject NaN


def parse_chart(payload: Dict[str, Any], symbol: str = "") -> Quote:
    """Read Yahoo's chart response. Raises QuoteError if it holds no price."""
    if not isinstance(payload, dict):
        raise QuoteError("the quote response wasn't the expected shape")

    chart = payload.get("chart") or {}
    if chart.get("error"):
        detail = chart["error"]
        message = detail.get("description") if isinstance(detail, dict) else detail
        raise QuoteError(f"the quote service refused that symbol ({message})")

    results = chart.get("result") or []
    if not results or not isinstance(results[0], dict):
        raise QuoteError("the quote service returned no result")
    meta = results[0].get("meta") or {}

    price = _number(meta.get("regularMarketPrice"))
    previous = (_number(meta.get("chartPreviousClose"))
                or _number(meta.get("previousClose")))
    if price is None:
        raise QuoteError("the quote response carried no price")

    resolved = str(meta.get("symbol") or symbol or "")
    return Quote(
        symbol=resolved,
        name=str(meta.get("longName") or meta.get("shortName") or ""),
        price=price,
        previous_close=previous,
        currency=str(meta.get("currency") or ""),
        exchange=str(meta.get("fullExchangeName")
                     or meta.get("exchangeName") or ""),
        as_of=_stamp(meta.get("regularMarketTime")),
        source="Yahoo Finance",
        source_url=CHART_URL.format(symbol=resolved or symbol),
    )


def parse_search(payload: Dict[str, Any], prefer_india: bool = False) -> str:
    """Pick the best symbol out of Yahoo's search response, or "" if none fit."""
    if not isinstance(payload, dict):
        return ""
    quotes = payload.get("quotes") or []
    equities = [q for q in quotes
                if isinstance(q, dict) and q.get("symbol")
                and q.get("quoteType") in (None, "EQUITY", "ETF", "INDEX",
                                           "CRYPTOCURRENCY", "CURRENCY")]
    if not equities:
        return ""
    if prefer_india:
        for quote in equities:
            symbol = str(quote["symbol"])
            if symbol.endswith((".NS", ".BO")):
                return symbol
    return str(equities[0]["symbol"])


#: Stooq's CSV header, so a shape change is noticed rather than mis-parsed.
_STOOQ_COLUMNS = ("symbol", "date", "time", "open", "high", "low", "close",
                  "volume")


def parse_stooq(csv_text: str, symbol: str = "") -> Quote:
    """Read Stooq's one-line CSV. Raises QuoteError if it holds no price."""
    lines = [line.strip() for line in str(csv_text or "").splitlines()
             if line.strip()]
    if len(lines) < 2:
        raise QuoteError("the fallback quote source returned nothing")

    header = [column.strip().lower() for column in lines[0].split(",")]
    values = [value.strip() for value in lines[1].split(",")]
    row = dict(zip(header, values))

    close = _number(row.get("close"))
    if close is None or row.get("close", "").upper() == "N/D":
        raise QuoteError("the fallback quote source had no price for that symbol")

    when = " ".join(part for part in (row.get("date", ""), row.get("time", ""))
                    if part and part.upper() != "N/D")
    return Quote(
        symbol=str(row.get("symbol") or symbol).upper(),
        price=close,
        previous_close=_number(row.get("open")),
        as_of=when,
        source="Stooq",
        source_url=STOOQ_URL,
    )


# --- fetching ---------------------------------------------------------------
def _get(url: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
    return requests.get(url, params=params, headers=_UA, timeout=TIMEOUT)


def resolve_symbol(name: str, prefer_india: bool = True) -> str:
    """A tradeable symbol for a plain-language name."""
    key = str(name or "").strip().lower()
    if not key:
        return ""
    if key in _ALIASES:
        return _ALIASES[key]
    # Already a symbol.
    if re.fullmatch(r"[A-Za-z0-9.\-^=]{1,14}", name or "") and name.isupper():
        return name
    try:
        response = _get(SEARCH_URL, {"q": name, "quotesCount": 8, "newsCount": 0})
        if response.status_code >= 400:
            return ""
        return parse_search(response.json(), prefer_india=prefer_india)
    except Exception:
        return ""


def fetch(symbol: str) -> Quote:
    """One quote, trying Yahoo and then Stooq. Raises QuoteError if both fail."""
    if not str(symbol or "").strip():
        raise QuoteError("no symbol to look up")

    reasons = []
    try:
        response = _get(CHART_URL.format(symbol=symbol),
                        {"interval": "1d", "range": "5d"})
        if response.status_code < 400:
            return parse_chart(response.json(), symbol=symbol)
        reasons.append(f"Yahoo returned HTTP {response.status_code}")
    except QuoteError as exc:
        reasons.append(str(exc))
    except Exception as exc:
        reasons.append(f"Yahoo was unreachable ({exc.__class__.__name__})")

    stooq_symbol = _stooq_symbol(symbol)
    if stooq_symbol:
        try:
            response = _get(STOOQ_URL, {"s": stooq_symbol, "f": "sd2t2ohlcv",
                                        "h": "", "e": "csv"})
            if response.status_code < 400:
                return parse_stooq(response.text, symbol=symbol)
            reasons.append(f"Stooq returned HTTP {response.status_code}")
        except QuoteError as exc:
            reasons.append(str(exc))
        except Exception as exc:
            reasons.append(f"Stooq was unreachable ({exc.__class__.__name__})")

    raise QuoteError("; ".join(reasons) or "no quote source answered")


def _stooq_symbol(symbol: str) -> str:
    """Yahoo's suffixes don't match Stooq's. Translate the ones we can."""
    symbol = str(symbol or "")
    if symbol.endswith(".NS"):
        return symbol[:-3].lower() + ".in"
    if symbol.startswith("^") or "=" in symbol or "-" in symbol:
        return ""          # indices, futures and crypto differ too much
    return symbol.lower() + ".us"


def lookup(text: str, limit: int = 3) -> Tuple[List[Quote], List[str]]:
    """Quotes for whatever a question names. Never raises.

    Returns (quotes, problems). A problem is worth showing the user: it is the
    difference between "the market is closed" and a silently empty answer that
    the model then fills in.
    """
    quotes: List[Quote] = []
    problems: List[str] = []
    prefer_india = bool(re.search(r"\b(?:india|indian|nse|bse|rupee|inr|nifty|"
                                  r"sensex)\b", text or "", re.IGNORECASE))

    for name in extract_names(text)[:limit]:
        symbol = resolve_symbol(name, prefer_india=prefer_india)
        if not symbol:
            problems.append(f"couldn't work out which instrument {name!r} means")
            continue
        try:
            quotes.append(fetch(symbol))
        except QuoteError as exc:
            problems.append(f"{name} ({symbol}): {exc}")
    return quotes, problems


# --- handing them to the model ---------------------------------------------
def as_prompt(quotes: List[Quote]) -> str:
    """The block appended to the conversation when quotes were fetched."""
    if not quotes:
        return ""
    lines = [
        "LIVE MARKET QUOTES (fetched just now, use these figures exactly):",
        *(f"- {quote.as_text()}" for quote in quotes),
        "",
        "Quote these numbers as written and give the 'as of' time with them -- a "
        "price without a timestamp is not much use. Name the exchange when it "
        "matters. Do not add any figure that is not listed above, and do not "
        "round the change percentage differently from what is given.",
    ]
    return "\n".join(lines)


def no_quote_prompt(problems: List[str], user_name: str = "you") -> str:
    """The block appended when a quote was wanted and could not be had.

    The mirror of data.no_data_prompt, and for the same reason: without it the
    model treats an empty retrieval as licence to answer from memory, and says
    "according to Moneycontrol" about a number it made up.
    """
    detail = "; ".join(problems[:3]) or "no quote source answered"
    return (
        f"[No live price could be fetched ({detail}). You therefore do NOT have "
        f"a current figure, and must not state one -- not from memory, not from "
        f"training data, and not by repeating a number {user_name} mentioned. Say "
        f"plainly that you couldn't get a live quote, say why if it is useful, and "
        f"point {user_name} at the NSE/BSE site, Moneycontrol, Google Finance or "
        f"TradingView to check. A stale or invented price looks exactly like a "
        f"real one, which is what makes it worse than admitting the gap.]"
    )


def ranked_list_prompt(user_name: str = "you") -> str:
    """For "top gainers", which no per-symbol source here can answer.

    The first version of this said flatly "do NOT produce a ranked list". That
    was aimed at the right failure -- a fabricated table of five gainers with
    two-decimal percentages -- but it was the wrong rule, and in practice the
    model half-ignored it anyway. When a real page has been read, the figures on
    it are worth having; what went wrong was presenting a page from **August
    2024** as the day's biggest movers.

    So the instruction is no longer "don't answer". It is "answer, and date it".
    A list from a named page with its date is useful. The same list implied to be
    live is not, and that distinction is the whole of the problem.
    """
    return (
        f"[{user_name} asked for a ranked list of movers (gainers/losers). There "
        f"is no live market screener available here, so you cannot state today's "
        f"list as fact.\n"
        f"If the researched pages above contain movers, you may report them -- but "
        f"only with the name of the page and the date it carries, and only if you "
        f"say plainly that it is that page's snapshot rather than live data. If a "
        f"page is marked as appearing to be from an earlier year, lead with that.\n"
        f"If the pages contain no movers, say you couldn't pull a screener. Either "
        f"way, do NOT invent percentages, and point {user_name} at the NSE 'Top "
        f"Gainers' page, Moneycontrol or TradingView for the live list.]"
    )
