"""Real numbers, from the institutions that publish them.

A language model asked "what is US inflation right now" will answer with great
confidence and no source. The figure comes from its training data, so it is
months or years stale, and there is no way to tell from the reply. For anything
economic that is not a small error -- it is the whole answer being wrong.

So BEASTT looks the number up instead. Two publishers, both free:

  * **FRED** (Federal Reserve Bank of St. Louis) -- 800,000+ US series, updated
    daily to monthly. Needs a free key.
  * **World Bank** -- thousands of indicators for every country, annual history
    going back decades. No key, no registration, no rate limit, so this one
    works the moment BEASTT is installed.

Both are wired the same way: ask a question, get a `Series` back carrying its
own units, its observation dates, and a citation. The citation is not decoration
-- World Bank data lags by a year or two, and an answer that hides that is
misleading even when the number is right.

This module reports what was published. It does not offer advice about what to
do with it, and the prompt built by `as_prompt` says so.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

import requests

from .config import Config

#: The World Bank's gateway is genuinely slow and intermittently returns 502.
#: 15 seconds was too tight in practice and produced ReadTimeouts on a healthy
#: connection, so the answer came from the model's memory instead.
TIMEOUT = 30
#: Statuses worth trying again. A 502/503/504 from a gateway says "ask me again",
#: not "this data does not exist", and giving up on the first one is how a
#: perfectly good question ended up answered from training data.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
#: Two retries, briefly spaced. Enough to ride out a blip; short enough that a
#: genuinely dead source doesn't hold up the reply.
_ATTEMPTS = 3
_BACKOFF = 1.5
#: How much history to pull. Enough for a year-on-year comparison and a trend.
DEFAULT_POINTS = 24
_UA = "JARVIS/1.0 (personal assistant; +https://github.com/klingesh/project-beastt)"


#: Answers already fetched this session, so a flaky free API is asked once rather
#: than once per question. Ten minutes is safe: nothing here updates faster than
#: daily, and a deck that mentions the same series on four slides should not make
#: four requests for it.
CACHE_TTL = 600
_CACHE: Dict[str, Tuple[float, object]] = {}


class DataError(RuntimeError):
    """A source was reachable but could not answer."""


# --- what comes back --------------------------------------------------------
@dataclass(frozen=True)
class Observation:
    date: str
    value: float


@dataclass
class Series:
    """One data series, with everything needed to quote it honestly."""

    title: str
    observations: List[Observation]
    units: str
    source: str
    source_url: str
    series_id: str
    frequency: str = ""
    #: When we fetched it -- distinct from when the data itself is from.
    retrieved: str = field(default_factory=lambda: date.today().isoformat())
    note: str = ""

    def __bool__(self) -> bool:
        return bool(self.observations)

    @property
    def latest(self) -> Optional[Observation]:
        return self.observations[-1] if self.observations else None

    @property
    def earliest(self) -> Optional[Observation]:
        return self.observations[0] if self.observations else None

    def change(self, back: int = 1) -> Optional[float]:
        """Percent change from `back` observations ago, or None."""
        if len(self.observations) <= back:
            return None
        then = self.observations[-1 - back].value
        now = self.observations[-1].value
        if then == 0:
            return None
        return (now - then) / abs(then) * 100.0

    def citation(self) -> str:
        return (f"{self.source}, {self.series_id} — {self.source_url} "
                f"(retrieved {self.retrieved})")

    def as_text(self, points: int = 6) -> str:
        """A compact block a model can read and quote without inventing detail."""
        if not self.observations:
            return f"{self.title}: no observations returned."
        lines = [f"{self.title} [{self.source}: {self.series_id}]"]
        if self.units:
            lines.append(f"  Units: {self.units}")
        if self.frequency:
            lines.append(f"  Frequency: {self.frequency}")
        latest = self.latest
        lines.append(f"  Latest: {_fmt(latest.value)} ({latest.date})")
        recent = self.observations[-points:]
        if len(recent) > 1:
            trail = "; ".join(f"{o.date} {_fmt(o.value)}" for o in recent[:-1])
            lines.append(f"  Preceding: {trail}")
        step = self.change(1)
        if step is not None:
            lines.append(f"  Change on previous observation: {step:+.2f}%")
        if self.note:
            lines.append(f"  Note: {self.note}")
        lines.append(f"  Source: {self.citation()}")
        return "\n".join(lines)


def _fmt(value: float) -> str:
    """Readable, without pretending to a precision nobody asked for.

    A raw World Bank GDP figure is 3956067115771.63, and a model handed that will
    read it out in full: "3,956,067,115,771.63 US dollars". "3.96 trillion" is the
    same fact in a form a person can actually use.

    The magnitude words deliberately start at a million, which keeps them away
    from series whose units already carry a scale. FRED's GDP series is quoted in
    billions of dollars and sits around 29,000 -- rewriting that as "29 thousand"
    would be wrong, and this threshold means it never happens.
    """
    magnitude = abs(value)
    for size, word in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million")):
        if magnitude >= size:
            scaled = f"{value / size:,.2f}".rstrip("0").rstrip(".")
            return f"{scaled} {word}"
    if value == int(value) and magnitude < 1e15:
        return f"{int(value):,}"
    if magnitude >= 1000:
        return f"{value:,.2f}"
    return f"{value:.4g}"


def as_prompt(series: Sequence[Series]) -> str:
    """Turn fetched series into context for the model, with the rules attached."""
    usable = [s for s in series if s]
    if not usable:
        return ""
    blocks = "\n\n".join(s.as_text() for s in usable)
    return (
        "PUBLISHED DATA (retrieved just now -- use these figures, do not recall "
        "your own):\n"
        f"{blocks}\n\n"
        "Rules for using this data:\n"
        "- Quote the figures above exactly, and give the observation date with "
        "each one. A number without its date is misleading.\n"
        "- Name the publisher (FRED or World Bank) so the reader can check it.\n"
        "- If the latest observation is old -- World Bank annual data often lags "
        "a year or two -- say so plainly.\n"
        "- If web search results elsewhere in this conversation give a different "
        "figure, the series above wins -- it came straight from the publisher.\n"
        "- Report and explain what the data shows. Do not recommend buying, "
        "selling or holding anything, and do not forecast a price."
    )


# --- the sources ------------------------------------------------------------
@dataclass(frozen=True)
class Source:
    id: str
    label: str
    home: str
    key_field: str = ""
    env_var: str = ""
    signup: str = ""
    blurb: str = ""

    @property
    def needs_key(self) -> bool:
        return bool(self.key_field)


SOURCES: Tuple[Source, ...] = (
    Source(
        id="worldbank",
        label="World Bank",
        home="https://data.worldbank.org",
        blurb=("Every country, thousands of indicators, decades of history. "
               "No key needed -- works out of the box. Annual, so it lags."),
    ),
    Source(
        id="fred",
        label="FRED",
        home="https://fred.stlouisfed.org",
        key_field="fred_key",
        env_var="BEASTT_FRED_KEY",
        signup="https://fredaccount.stlouisfed.org/apikeys",
        blurb=("US economic data from the St. Louis Fed -- inflation, jobs, "
               "rates, yields. Updated daily to monthly. Free key."),
    ),
)

_BY_ID: Dict[str, Source] = {s.id: s for s in SOURCES}


def get_source(source_id: str) -> Optional[Source]:
    return _BY_ID.get(source_id)


def key_for(config: Config, source: Source) -> str:
    if not source.key_field:
        return ""
    return str(getattr(config, source.key_field, "") or "")


def is_configured(config: Config, source: Source) -> bool:
    return not source.needs_key or bool(key_for(config, source))


def catalogue(config: Config) -> List[Dict]:
    """Which sources are usable, for the status screen."""
    rows = []
    for source in SOURCES:
        ready = is_configured(config, source)
        rows.append({
            "id": source.id,
            "label": source.label,
            "blurb": source.blurb,
            "configured": ready,
            "needs_key": source.needs_key,
            "env_var": source.env_var,
            "signup": source.signup,
            "status": "ready" if ready else "no key",
        })
    return rows


# --- HTTP -------------------------------------------------------------------
def _get_json(url: str, params: Dict[str, str], timeout: int = TIMEOUT,
              attempts: int = _ATTEMPTS):
    """GET some JSON, retrying the failures that are worth retrying.

    Observed in the wild: two consecutive 502s from api.worldbank.org followed by
    a successful request minutes later, for an identical URL. Treating the first
    failure as final meant the assistant fell back to remembered figures for a
    question the source could perfectly well answer.

    A 4xx other than 429 is not retried -- a bad indicator code will be just as
    bad the second time, and retrying it only makes the user wait.
    """
    key = url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    hit = _CACHE.get(key)
    if hit is not None and (time.time() - hit[0]) < CACHE_TTL:
        return hit[1]

    last = "unknown error"
    for attempt in range(1, max(1, attempts) + 1):
        retryable = False
        try:
            resp = requests.get(url, params=params, timeout=timeout,
                                headers={"User-Agent": _UA})
        except Exception as exc:
            # Timeouts and dropped connections are exactly the transient class.
            last = f"could not reach {url} ({exc.__class__.__name__})"
            retryable = True
        else:
            if resp.status_code in _RETRY_STATUS:
                last = f"HTTP {resp.status_code}"
                retryable = True
            elif resp.status_code >= 400:
                detail = ""
                try:
                    body = resp.json()
                    if isinstance(body, dict):
                        detail = str(body.get("error_message") or "")
                except Exception:
                    pass
                raise DataError(
                    f"HTTP {resp.status_code}{': ' + detail if detail else ''}")
            else:
                try:
                    payload = resp.json()
                except Exception:
                    # Almost always means file_type/format was dropped and we
                    # got XML back. Retrying will not change that.
                    raise DataError("reply was not JSON")
                _CACHE[key] = (time.time(), payload)
                return payload

        if retryable and attempt < attempts:
            print(f"[data] {last}; retrying ({attempt + 1} of {attempts})")
            time.sleep(_BACKOFF * attempt)

    raise DataError(f"{last} after {attempts} attempts")


# --- FRED -------------------------------------------------------------------
FRED_BASE = "https://api.stlouisfed.org/fred"

#: FRED's own transformations, so we never do the arithmetic ourselves.
#: "pc1" is percent change from a year ago -- which is what "inflation" means
#: when the underlying series is a price *index*.
_FRED_UNITS = {
    "lin": "as published",
    "pc1": "percent change from a year ago",
    "pch": "percent change from previous period",
}


def fred_fetch(config: Config, series_id: str, points: int = DEFAULT_POINTS,
               units: str = "lin", title: str = "", note: str = "") -> Series:
    """One FRED series, newest observations last."""
    key = key_for(config, _BY_ID["fred"])
    if not key:
        raise DataError("no FRED key set (BEASTT_FRED_KEY)")

    meta_title, meta_units, frequency = title, "", ""
    try:
        meta = _get_json(f"{FRED_BASE}/series", {
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
        })
        rows = meta.get("seriess") or []
        if rows:
            meta_title = title or str(rows[0].get("title") or series_id)
            meta_units = str(rows[0].get("units") or "")
            frequency = str(rows[0].get("frequency") or "")
    except DataError:
        # Metadata is a convenience; the observations are the point.
        pass

    payload = _get_json(f"{FRED_BASE}/series/observations", {
        "series_id": series_id,
        "api_key": key,
        # Without this FRED returns XML. It is the single most common mistake
        # made against this API.
        "file_type": "json",
        "units": units,
        "sort_order": "desc",
        "limit": str(max(1, points)),
    })

    observations: List[Observation] = []
    for row in payload.get("observations") or []:
        raw = str(row.get("value", "")).strip()
        # FRED writes "." for a missing observation.
        if not raw or raw == ".":
            continue
        try:
            observations.append(Observation(date=str(row.get("date") or ""),
                                            value=float(raw)))
        except ValueError:
            continue
    observations.reverse()      # asked for newest first; we want oldest first

    unit_label = meta_units
    if units != "lin":
        transformed = _FRED_UNITS.get(units, units)
        unit_label = f"{transformed}" + (f" ({meta_units})" if meta_units else "")

    return Series(
        title=meta_title or series_id,
        observations=observations,
        units=unit_label,
        source="FRED",
        source_url=f"https://fred.stlouisfed.org/series/{series_id}",
        series_id=series_id,
        frequency=frequency,
        note=note,
    )


def fred_search(config: Config, text: str, limit: int = 5) -> List[Dict[str, str]]:
    """Find series ids by description, for topics not in the table below."""
    key = key_for(config, _BY_ID["fred"])
    if not key:
        raise DataError("no FRED key set (BEASTT_FRED_KEY)")
    payload = _get_json(f"{FRED_BASE}/series/search", {
        "search_text": text,
        "api_key": key,
        "file_type": "json",
        "limit": str(max(1, limit)),
        "order_by": "popularity",
        "sort_order": "desc",
    })
    found = []
    for row in payload.get("seriess") or []:
        found.append({
            "id": str(row.get("id") or ""),
            "title": str(row.get("title") or ""),
            "units": str(row.get("units") or ""),
            "frequency": str(row.get("frequency") or ""),
            "last_updated": str(row.get("last_updated") or ""),
        })
    return [f for f in found if f["id"]]


# --- World Bank -------------------------------------------------------------
WB_BASE = "https://api.worldbank.org/v2"


def worldbank_fetch(country: str, indicator: str, points: int = DEFAULT_POINTS,
                    title: str = "", units: str = "", note: str = "") -> Series:
    """One World Bank indicator for one country, newest observations last."""
    payload = _get_json(f"{WB_BASE}/country/{country}/indicator/{indicator}", {
        # Same trap as FRED: XML is the default.
        "format": "json",
        "per_page": str(max(1, points)),
        # Newest pages first, so page one holds the most recent years.
        "page": "1",
    })
    # The shape is [metadata, [rows]] -- an error is a bare dict or a short list.
    if not isinstance(payload, list) or len(payload) < 2:
        message = ""
        if isinstance(payload, list) and payload:
            head = payload[0]
            if isinstance(head, dict):
                message = str(head.get("message") or "")
        raise DataError(f"World Bank returned no data{': ' + message if message else ''}")

    rows = payload[1] or []
    label, country_name, unit_label = title, "", units
    observations: List[Observation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not label:
            label = str((row.get("indicator") or {}).get("value") or indicator)
        if not country_name:
            country_name = str((row.get("country") or {}).get("value") or "")
        value = row.get("value")
        if value is None:
            # Recent years are routinely blank -- not an error, just not out yet.
            continue
        try:
            observations.append(Observation(date=str(row.get("date") or ""),
                                            value=float(value)))
        except (TypeError, ValueError):
            continue
    observations.sort(key=lambda o: o.date)

    full_title = f"{country_name}: {label}".strip(": ") if country_name else label
    return Series(
        title=full_title or indicator,
        observations=observations,
        units=unit_label,
        source="World Bank",
        source_url=(f"https://data.worldbank.org/indicator/{indicator}"
                    f"?locations={country}"),
        series_id=f"{indicator} / {country}",
        frequency="Annual",
        note=note or "World Bank data is annual and is usually a year or two behind.",
    )


# --- working out what was asked --------------------------------------------
#: Ordered: the first pattern that matches wins, so "gdp per capita" has to be
#: tested before "gdp" and "core inflation" before "inflation".
_FRED_TOPICS: Tuple[Tuple[str, Dict[str, str]], ...] = (
    (r"\bcore\s+(?:pce|inflation)\b",
     {"series_id": "PCEPILFE", "units": "pc1",
      "title": "US core PCE inflation, year over year"}),
    (r"\b(?:pce)\s+inflation\b",
     {"series_id": "PCEPI", "units": "pc1",
      "title": "US PCE inflation, year over year"}),
    (r"\b(?:inflation|cpi|consumer\s+price)\b",
     {"series_id": "CPIAUCSL", "units": "pc1",
      "title": "US CPI inflation, year over year"}),
    (r"\b(?:unemployment|jobless\s+rate)\b", {"series_id": "UNRATE"}),
    (r"\b(?:non[- ]?farm|payrolls?|jobs?\s+(?:added|report))\b",
     {"series_id": "PAYEMS"}),
    (r"\b(?:labour|labor)\s+force\s+participation\b", {"series_id": "CIVPART"}),
    (r"\b(?:fed\s+funds|federal\s+funds|policy\s+rate|fed\s+rate|"
     r"interest\s+rate)\b", {"series_id": "FEDFUNDS"}),
    (r"\b(?:10[- ]?year|ten[- ]?year)\b.*\b(?:treasury|yield|bond)\b|"
     r"\btreasury\s+yield\b", {"series_id": "DGS10"}),
    (r"\byield\s+curve\b|\b10y2y\b",
     {"series_id": "T10Y2Y", "title": "US 10-year minus 2-year Treasury spread"}),
    (r"\bmortgage\s+rate\b", {"series_id": "MORTGAGE30US"}),
    (r"\breal\s+gdp\s+growth\b|\bgdp\s+growth\b",
     {"series_id": "GDPC1", "units": "pc1",
      "title": "US real GDP growth, year over year"}),
    (r"\bgdp\b", {"series_id": "GDP"}),
    (r"\b(?:s\s*&\s*p\s*500|s\s*and\s*p\s*500|sp500|spx)\b",
     {"series_id": "SP500"}),
    (r"\b(?:vix|volatility\s+index)\b", {"series_id": "VIXCLS"}),
    (r"\b(?:crude|oil\s+price|wti)\b", {"series_id": "DCOILWTICO"}),
    (r"\bretail\s+sales\b", {"series_id": "RSAFS"}),
    (r"\bconsumer\s+sentiment\b|\bconsumer\s+confidence\b",
     {"series_id": "UMCSENT"}),
    (r"\b(?:house|home|housing)\s+price", {"series_id": "CSUSHPINSA"}),
    (r"\bhousing\s+starts\b", {"series_id": "HOUST"}),
    (r"\b(?:usd\s*[/-]?\s*inr|inr|rupee|dollar\s+to\s+rupee)\b",
     {"series_id": "DEXINUS", "title": "Indian rupees per US dollar"}),
    (r"\b(?:money\s+supply|m2)\b", {"series_id": "M2SL"}),
    (r"\bindustrial\s+production\b", {"series_id": "INDPRO"}),
    (r"\b(?:national|federal|government)\s+debt\b", {"series_id": "GFDEBTN"}),
    (r"\b(?:savings?\s+rate)\b", {"series_id": "PSAVERT"}),
)

_WB_TOPICS: Tuple[Tuple[str, Dict[str, str]], ...] = (
    (r"\bgdp\s+per\s+capita\b",
     {"indicator": "NY.GDP.PCAP.CD", "units": "current US$"}),
    (r"\bgdp\s+growth\b|\beconomic\s+growth\b",
     {"indicator": "NY.GDP.MKTP.KD.ZG", "units": "annual %"}),
    (r"\bgdp\b|\beconomy\s+size\b",
     {"indicator": "NY.GDP.MKTP.CD", "units": "current US$"}),
    (r"\b(?:inflation|cpi|consumer\s+price)\b",
     {"indicator": "FP.CPI.TOTL.ZG", "units": "annual %"}),
    (r"\b(?:unemployment|jobless)\b",
     {"indicator": "SL.UEM.TOTL.ZS", "units": "% of labour force"}),
    (r"\bpopulation\b", {"indicator": "SP.POP.TOTL", "units": "people"}),
    (r"\blife\s+expectancy\b", {"indicator": "SP.DYN.LE00.IN", "units": "years"}),
    (r"\bexports?\b", {"indicator": "NE.EXP.GNFS.CD", "units": "current US$"}),
    (r"\bimports?\b", {"indicator": "NE.IMP.GNFS.CD", "units": "current US$"}),
    (r"\b(?:fdi|foreign\s+direct\s+investment|foreign\s+investment)\b",
     {"indicator": "BX.KLT.DINV.CD.WD", "units": "current US$, net inflows"}),
    (r"\b(?:government|public|national)\s+debt\b",
     {"indicator": "GC.DOD.TOTL.GD.ZS", "units": "% of GDP"}),
    (r"\bmanufacturing\b", {"indicator": "NV.IND.MANF.ZS",
                            "units": "% of GDP"}),
    (r"\b(?:internet\s+users?|internet\s+penetration)\b",
     {"indicator": "IT.NET.USER.ZS", "units": "% of population"}),
    (r"\bliteracy\b", {"indicator": "SE.ADT.LITR.ZS", "units": "% of adults"}),
    (r"\b(?:gini|inequality)\b", {"indicator": "SI.POV.GINI", "units": "index"}),
    (r"\breal\s+interest\s+rate\b", {"indicator": "FR.INR.RINR",
                                     "units": "annual %"}),
    (r"\b(?:labour|labor)\s+force\s+participation\b",
     {"indicator": "SL.TLF.CACT.ZS", "units": "% of population 15+"}),
)

_FRED_COMPILED = tuple((re.compile(p, re.IGNORECASE), spec)
                       for p, spec in _FRED_TOPICS)
_WB_COMPILED = tuple((re.compile(p, re.IGNORECASE), spec)
                     for p, spec in _WB_TOPICS)

#: Country names to World Bank codes. Not exhaustive -- the ones a person is
#: likely to ask about, plus a passthrough for anyone who knows the ISO code.
COUNTRIES: Dict[str, str] = {
    "india": "IND", "indian": "IND",
    # "us" is deliberately absent: it is an ordinary English pronoun, and
    # matching it case-insensitively turned "tell us India's inflation" into a
    # question about America. It is handled case-sensitively below instead.
    "united states": "USA", "usa": "USA", "america": "USA", "american": "USA",
    "china": "CHN", "chinese": "CHN",
    "united kingdom": "GBR", "uk": "GBR", "britain": "GBR", "british": "GBR",
    "japan": "JPN", "germany": "DEU", "france": "FRA", "italy": "ITA",
    "spain": "ESP", "netherlands": "NLD", "switzerland": "CHE",
    "sweden": "SWE", "norway": "NOR", "denmark": "DNK", "ireland": "IRL",
    "canada": "CAN", "mexico": "MEX", "brazil": "BRA", "argentina": "ARG",
    "chile": "CHL", "colombia": "COL", "peru": "PER",
    "russia": "RUS", "turkey": "TUR", "poland": "POL",
    "australia": "AUS", "new zealand": "NZL",
    "south korea": "KOR", "korea": "KOR", "indonesia": "IDN",
    "vietnam": "VNM", "thailand": "THA", "philippines": "PHL",
    "malaysia": "MYS", "singapore": "SGP",
    "pakistan": "PAK", "bangladesh": "BGD", "sri lanka": "LKA",
    "nepal": "NPL", "bhutan": "BTN",
    "saudi arabia": "SAU", "uae": "ARE", "united arab emirates": "ARE",
    "israel": "ISR", "egypt": "EGY", "qatar": "QAT",
    "nigeria": "NGA", "south africa": "ZAF", "kenya": "KEN",
    "ethiopia": "ETH", "ghana": "GHA", "morocco": "MAR",
    "world": "WLD", "global": "WLD", "globally": "WLD",
    "euro area": "EMU", "eurozone": "EMU", "european union": "EUU", "eu": "EUU",
}

#: Longest first, so "south korea" is not matched as "korea" and "united
#: states" is not matched as the "us" inside it.
_COUNTRY_RE = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in
                      sorted(COUNTRIES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

#: "US" and "U.S." only when actually capitalised, which is the whole difference
#: between the country and the pronoun.
_US_RE = re.compile(r"\bU\.?S\.?\b")

#: The message has to be asking for a figure, not merely mentioning a topic.
_ASKING = re.compile(
    r"\b(what|what'?s|whats|how\s+much|how\s+many|how\s+high|show|give|tell|"
    r"get|fetch|find|look\s+up|pull|current|currently|latest|recent|today|"
    r"now|trend|figures?|numbers?|rate|data|statistics?|stats|compare|"
    r"chart|graph)\b",
    re.IGNORECASE,
)


@dataclass
class Ask:
    """What a question turned out to be asking for."""

    source_id: str
    spec: Dict[str, str]
    countries: List[str]
    #: Where the topic was mentioned, so callers can check it reads as a request.
    match: object = None


def detect_countries(text: str) -> List[str]:
    """World Bank codes mentioned in the text, in order, without duplicates."""
    body = text or ""
    seen: List[Tuple[int, str]] = []
    for found in _COUNTRY_RE.finditer(body):
        seen.append((found.start(), COUNTRIES[found.group(1).lower()]))
    for found in _US_RE.finditer(body):
        seen.append((found.start(), "USA"))
    seen.sort(key=lambda pair: pair[0])

    ordered: List[str] = []
    for _, code in seen:
        if code not in ordered:
            ordered.append(code)
    return ordered


def resolve(config: Config, text: str) -> Optional[Ask]:
    """Work out which source and series a question wants, or None.

    FRED is preferred for the United States: it is monthly or daily where the
    World Bank is annual, so it can actually answer "right now". For every other
    country the World Bank is both broader and always available.
    """
    body = str(text or "")
    if not body.strip():
        return None

    countries = detect_countries(body)
    fred_ready = is_configured(config, _BY_ID["fred"])
    #: US unless another country was named. FRED is US-only data.
    us_question = not countries or countries[0] == "USA"

    if fred_ready and us_question:
        for pattern, spec in _FRED_COMPILED:
            found = pattern.search(body)
            if found:
                return Ask("fred", dict(spec), ["USA"], found)

    for pattern, spec in _WB_COMPILED:
        found = pattern.search(body)
        if found:
            return Ask("worldbank", dict(spec), countries or ["WLD"], found)

    # A US topic FRED covers but the World Bank does not, with no key set.
    if us_question and not fred_ready:
        for pattern, spec in _FRED_COMPILED:
            found = pattern.search(body)
            if found:
                return Ask("fred", dict(spec), ["USA"], found)
    return None


def wanted(config: Config, text: str) -> bool:
    """Is this a question a data source should answer rather than the model?"""
    body = str(text or "")
    if not _ASKING.search(body):
        return False
    return resolve(config, body) is not None


def lookup(config: Config, text: str, points: int = DEFAULT_POINTS,
           max_series: int = 3,
           problems: Optional[List[str]] = None) -> List[Series]:
    """Fetch whatever a question asks for. Empty list if nothing applies.

    Pass a list as `problems` to find out *why* the list came back empty. That
    distinction matters: "this was never a data question" and "the World Bank was
    unreachable" look identical to the caller otherwise, and the second one has to
    be told to the user rather than quietly ignored.
    """
    ask = resolve(config, text)
    if ask is None:
        return []
    return fetch_ask(config, ask, points=points, max_series=max_series,
                     problems=problems)


def fetch_ask(config: Config, ask: Ask, points: int = DEFAULT_POINTS,
              max_series: int = 3,
              problems: Optional[List[str]] = None) -> List[Series]:
    """Carry out a resolved ask. Failures are reported, never raised."""
    series: List[Series] = []

    def note(message: str) -> None:
        print(f"[data] {message}")
        if problems is not None:
            problems.append(message)

    if ask.source_id == "fred":
        try:
            result = fred_fetch(
                config,
                series_id=ask.spec["series_id"],
                points=points,
                units=ask.spec.get("units", "lin"),
                title=ask.spec.get("title", ""),
            )
            if result:
                series.append(result)
            else:
                note(f"FRED {ask.spec.get('series_id')}: no observations returned")
        except DataError as exc:
            note(f"FRED {ask.spec.get('series_id')}: {exc}")
        return series

    indicator = ask.spec.get("indicator", "")
    if not indicator:
        note("no indicator to fetch")
        return series
    for country in (ask.countries or ["WLD"])[:max_series]:
        try:
            result = worldbank_fetch(
                country=country,
                indicator=indicator,
                points=points,
                units=ask.spec.get("units", ""),
                title=ask.spec.get("title", ""),
            )
            if result:
                series.append(result)
            else:
                note(f"World Bank {indicator}/{country}: no values published yet")
        except DataError as exc:
            note(f"World Bank {indicator}/{country}: {exc}")
    return series


def no_data_prompt(reason: str, user_name: str = "the user") -> str:
    """Context for the model when a lookup was warranted but produced nothing.

    Without this the model answers from training data and then describes it as
    having "pulled the latest figures" -- a claim the reader has no way to check.
    An undated number honestly labelled is far more use than a fresh-sounding lie.
    """
    return (
        "DATA LOOKUP FAILED\n"
        f"A figure was expected for this question and could not be fetched: {reason}\n"
        "You therefore have NO live data in front of you.\n"
        "- Do not say you looked anything up, pulled data, checked the latest "
        "figures, or ran a search. You did not, and "
        f"{user_name} has no way to tell that you didn't.\n"
        "- If you give a number at all, say plainly that it is from memory, give "
        "the year you believe it refers to, and say it may be out of date.\n"
        "- Say the source could not be reached and offer to try again."
    )


def describe(series: Sequence[Series]) -> str:
    """A one-line status for the interface, e.g. "FRED: US CPI inflation"."""
    usable = [s for s in series if s]
    if not usable:
        return ""
    first = usable[0]
    extra = f" (+{len(usable) - 1} more)" if len(usable) > 1 else ""
    return f"{first.source}: {first.title}{extra}"
