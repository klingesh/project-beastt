"""Web search for BEASTT -- lets it answer questions about live/current events.

Two backends, tried in order:
  1. `ddgs` (DuckDuckGo Search library) if installed -- best quality.
  2. A dependency-free fallback that queries DuckDuckGo's HTML endpoint with
     `requests` (already a core dependency), so search works out of the box.

Both return a normalised list of results: {"title", "body", "url"}.
"""

from __future__ import annotations

import html as _html
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

# --- intent detection -------------------------------------------------------
#: What makes a question one the web should answer.
#:
#: This list used to be much shorter, and the gaps were not obvious until they
#: were reported. `\btoday'?s\b` matched "today's" but not "today", so "top gainer
#: stocks and loser stocks state 5 nos in india **today**" triggered no search at
#: all -- and the model, asked a question it had no material for, invented five
#: gainers and five losers with two-decimal percentages and credited them to
#: Moneycontrol. "the values are wrong ... do some research" did not trigger one
#: either.
#:
#: So the bias is now the other way round. A needless search costs a second and
#: some tokens; a missing one costs a fabricated answer that looks checkable.
_TRIGGERS = [
    # Being asked outright.
    r"\bsearch\b", r"\blook (?:it )?up\b", r"\bgoogle\b", r"\bresearch\b",
    r"\bfind out\b", r"\bcheck (?:on|for|the)\b", r"\bverify\b", r"\bsource\b",
    # Recency.
    r"\blatest\b", r"\bnewest\b", r"\bmost recent\b", r"\brecent(ly)?\b",
    r"\bcurrent(ly)?\b", r"\bright now\b", r"\bat the moment\b", r"\blive\b",
    r"\btoday'?s?\b", r"\btonight\b", r"\byesterday\b", r"\btomorrow\b",
    r"\bthis (?:week|month|year|morning|evening)\b",
    r"\blast (?:week|month|night)\b", r"\bso far\b", r"\bup to date\b",
    r"\b20\d\d\b",
    # News.
    r"\bnews\b", r"\bheadlines?\b", r"\bwhat'?s happening\b",
    r"\bwhat is happening\b", r"\bwhat'?s going on\b", r"\bin the world\b",
    r"\bupdates?\b", r"\bany (?:update|news)\b", r"\bwho won\b",
    r"\bhappening (?:in|around|with)\b",
    # Numbers and money -- the class that hurt most when it was missed.
    r"\bprice\b", r"\bprices\b", r"\bcost(s|ing)?\b", r"\bhow much\b",
    r"\bhow many\b", r"\brate[s]?\b", r"\bquote\b", r"\bvaluation\b",
    r"\bshare price\b", r"\bstock price\b", r"\bstocks?\b", r"\bshares?\b",
    r"\bmarket\b", r"\bnifty\b", r"\bsensex\b", r"\bindex\b",
    r"\bgainer|loser|mover\b", r"\binflation\b", r"\bcpi\b", r"\bgdp\b",
    r"\bexchange rate\b", r"\bcrypto|bitcoin\b",
    # Rankings and superlatives, which are always about a live state of affairs.
    r"\btop\s+\d*\s*\b", r"\bbest\b", r"\bworst\b", r"\bbiggest\b",
    r"\bhighest\b", r"\blowest\b", r"\bmost\s+\w+", r"\branking|ranked\b",
    # Products.
    r"\bbuy\b", r"\bcheapest\b", r"\bdiscount\b", r"\bdeal[s]?\b",
    r"\breview[s]?\b", r"\bspec(s|ification)?\b", r"\bcompare\b", r"\bvs\.?\b",
    r"\bwhere can i (?:get|buy|find)\b", r"\bavailable\b", r"\bin stock\b",
    r"\blaunch(ed|ing)?\b", r"\brelease(d)?\b",
    # Other things that live outside a model's weights.
    r"\bweather\b", r"\bforecast\b", r"\bscore\b", r"\bresult[s]?\b",
    r"\bschedule\b", r"\btimings?\b", r"\bstatus of\b", r"\bopen (?:now|today)\b",
    # Checking something heard elsewhere. "so any victims? like i heard a kill
    # has happened" and "i heard that a school student has been murdered in
    # coimbatore" both went unresearched, which is the worst case for this class:
    # the user is explicitly asking whether a thing is true, and an unverified
    # answer is taken as confirmation. Events are named as nouns because the
    # question is often not phrased as a question at all.
    r"\bi (?:heard|read|saw)\b", r"\bis (?:it|that) true\b",
    # "happening" and "going on" need a place or an interrogative attached. Bare,
    # they match "lots going on for now, I'm at college", which is someone
    # talking about their day, not asking to have it researched.
    r"\bhappened\b", r"\bgoing on (?:in|with|around|at)\b",
    r"\bdetails? (?:about|on|regarding|of)\b", r"\bconfirm(ed|ation)?\b",
    r"\bany (?:victims?|casualt(?:y|ies)|deaths?|survivors?|arrests?|injur\w+)\b",
    r"\b(?:murder(?:ed|s)?|killed|kill|died|deaths?|accident|arrest(?:ed|s)?|"
    r"protest|riot|fire|flood|earthquake|crash|attack|scam|raid|seiz(?:ed|ure))\b",
]
_TRIGGER_RE = re.compile("|".join(_TRIGGERS), re.IGNORECASE)
_NEWS_RE = re.compile(
    r"\b(news|happening|headlines?|world|updates?|going on|breaking|"
    r"reported|incident|arrested|election|murder|crime)\b",
    re.IGNORECASE,
)

#: Shopping-shaped questions, where a spread of retailers is the useful answer
#: rather than one summarised page.
_PRODUCT_RE = re.compile(
    r"\b(?:buy|cheapest|price of|cost of|deal[s]?|discount|review[s]?|"
    r"spec(?:s|ification)?|compare|vs\.?|where can i (?:get|buy|find)|"
    r"available|in stock|worth (?:it|buying)|which (?:one|model|phone|laptop))\b",
    re.IGNORECASE,
)

#: Kinds of question, which decide how many sources to gather and how to read
#: them. Returned by classify().
QUOTE, NEWS, PRODUCT, FACTUAL, CHAT = "quote", "news", "product", "factual", "chat"

# Filler phrases we strip to turn a spoken request into a clean search query.
_LEAD_INS = [
    r"^(hey |ok |okay )?beastt[,\s]*",
    r"\b(can|could|would) you\b",
    r"\bplease\b",
    r"\b(get|tell|give|find|fetch|show) me\b",
    r"\bi want to know\b",
    r"\bi'?d like to know\b",
    r"\bdo you know\b",
    r"\bsearch (the web )?(for )?\b",
    r"\blook up\b",
    r"\bgoogle\b",
    r"\bwhat'?s the latest (on|about|regarding)?\b",
    r"\bwhat'?s happening (with|in|on|around)?\b",
    r"\bwhat is happening (with|in|on|around)?\b",
    r"\bupdates?\s+(on|about|regarding|for)\b",
    r"\bthe latest\b",
    r"\bfor me\b",
    r"\bright now\b",
]
_LEAD_RE = re.compile("|".join(_LEAD_INS), re.IGNORECASE)


def needs_search(text: str) -> bool:
    return bool(_TRIGGER_RE.search(str(text or "")))


def is_news(text: str) -> bool:
    return bool(_NEWS_RE.search(str(text or "")))


def classify(text: str) -> str:
    """What kind of question this is, so it can be researched appropriately.

    Asking "what's happening in Tamil Nadu" and "what's Tata Steel trading at"
    both need the web, but they do not need the same thing from it: one wants
    several outlets read and reconciled, the other wants one authoritative
    figure. Treating every question as "run one search, paste five snippets" is
    why the answers read like a shrug.
    """
    text = str(text or "")
    if not needs_search(text):
        return CHAT

    from . import quotes

    if quotes.wanted(text) or quotes.is_ranked_list(text):
        return QUOTE
    if _PRODUCT_RE.search(text):
        return PRODUCT
    if is_news(text):
        return NEWS
    return FACTUAL


def extract_query(text: str) -> str:
    """Turn a natural request into a concise search query.

    The lead-in stripping is deliberately conservative about word order: an
    earlier version removed "give me" from the middle of "so now give me an
    update about top stocks that has gained" and searched for "so now an top
    stocks that has gained", which is not a phrase anybody has ever written down.
    Filler words left stranded by a removal are cleaned up afterwards.
    """
    query = _LEAD_RE.sub(" ", str(text or ""))
    query = re.sub(r"[?!.]+$", "", query)
    # Tidy up connectives left dangling by the removals above.
    query = re.sub(r"^\s*(?:so|and|but|ok|okay|well|umm?|hey|also)\b[\s,]*", " ",
                   query, flags=re.IGNORECASE)
    query = re.sub(r"\b(?:an?|the)\s+(?=\b(?:top|best|latest)\b)", " ", query,
                   flags=re.IGNORECASE)
    query = re.sub(r"\s+", " ", query).strip(" ,.")
    return query or str(text or "").strip()


def plan_queries(text: str, kind: str = "") -> List[str]:
    """Several angles on one question, because one search is one opinion.

    Asked what is happening somewhere, a single query returns whatever that
    engine ranked first -- which in the reported session was a Wikipedia page
    about the state's culture and tourism, dressed up in the reply as today's
    news. Asking for the topic's news, its latest news and its headlines
    separately gets actual outlets.
    """
    kind = kind or classify(text)
    topic = extract_query(text)
    if not topic:
        return []

    if kind == NEWS:
        plans = [f"{topic} news", f"{topic} latest news today",
                 f"{topic} news headlines this week"]
    elif kind == PRODUCT:
        plans = [f"{topic} price", f"{topic} review", f"{topic} buy online price"]
    elif kind == QUOTE:
        plans = [f"{topic} live price", f"{topic} share price today"]
    else:
        plans = [topic, f"{topic} latest"]

    seen, unique = set(), []
    for plan in plans:
        key = plan.lower()
        if key not in seen:
            seen.add(key)
            unique.append(plan)
    return unique


def format_results(results: List[Dict[str, str]]) -> str:
    if not results:
        return "SEARCH RESULTS: (none found)"
    lines = ["SEARCH RESULTS:"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "").strip()
        body = r.get("body", "").strip()
        url = r.get("url", "").strip()
        lines.append(f"{i}. {title}\n   {body}\n   Source: {url}")
    return "\n".join(lines)


# --- reading the pages, not just the snippets -------------------------------

#: A snippet is roughly twenty words chosen by a search engine to look relevant.
#: It is almost never enough to answer with, and asking a model to answer from
#: snippets alone is asking it to fill the gaps -- which it does, plausibly.
#: Fetching the pages is the difference between summarising and guessing.
_DROP_BLOCKS = re.compile(
    r"<(script|style|noscript|svg|nav|header|footer|form|aside|iframe)\b[^>]*>"
    r".*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_BREAKS = re.compile(r"</(?:p|div|li|h[1-6]|tr|section|article|br)\s*>|<br\s*/?>",
                     re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_BLANKS = re.compile(r"\n{3,}")

#: Enough of a page to answer from, without swamping an 8B context window.
MAX_PAGE_CHARS = 6000
#: A page bigger than this is a download, not an article.
MAX_PAGE_BYTES = 3_000_000


def readable_text(html_source: str, limit: int = MAX_PAGE_CHARS) -> str:
    """Strip a page down to its prose.

    Deliberately regex-based rather than pulling in a parser: BEASTT's core
    dependencies are `requests` and `python-dotenv`, and this does not need to be
    perfect. Scripts, styles and navigation go first -- they are where most of the
    words are on a news page, and none of them are the article.
    """
    text = _DROP_BLOCKS.sub(" ", str(html_source or ""))
    text = _BREAKS.sub("\n", text)
    text = _TAGS.sub(" ", text)
    text = _html.unescape(text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _BLANKS.sub("\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    if len(text) > limit:
        cut = text.rfind(" ", 0, limit)
        text = text[: cut if cut > limit // 2 else limit].rstrip() + " ..."
    return text


@dataclass
class Source:
    """One thing that was read, and where it came from."""

    title: str = ""
    url: str = ""
    snippet: str = ""
    #: The page's own text, when it could be fetched. Empty means snippet only.
    body: str = ""

    @property
    def read(self) -> bool:
        return bool(self.body)

    def as_block(self) -> str:
        head = f"{self.title}\n   {self.url}".strip()
        content = self.body or self.snippet
        marker = "full page" if self.read else "search snippet only"
        return f"{head}\n   [{marker}]\n   {content}".strip()


@dataclass
class Findings:
    """Everything gathered for one question."""

    question: str = ""
    kind: str = FACTUAL
    queries: List[str] = field(default_factory=list)
    sources: List[Source] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.sources)

    def texts(self) -> List[str]:
        """The raw material, for the grounding check to verify figures against."""
        return [f"{s.title} {s.snippet} {s.body}" for s in self.sources]

    def urls(self) -> List[str]:
        return [s.url for s in self.sources if s.url]


def format_findings(findings: "Findings", user_name: str = "you") -> str:
    """The block handed to the model, with instructions matched to the question."""
    if not findings.sources:
        return no_results_prompt(findings, user_name)

    read = sum(1 for s in findings.sources if s.read)
    header = (
        f"RESEARCH FOR THIS QUESTION (searched {len(findings.queries)} way(s), "
        f"{len(findings.sources)} source(s), {read} read in full):"
    )
    body = "\n\n".join(f"[{i}] {s.as_block()}"
                       for i, s in enumerate(findings.sources, 1))
    return f"{header}\n\n{body}\n\n{_instruction_for(findings.kind, user_name)}"


def _instruction_for(kind: str, user_name: str) -> str:
    common = (
        "Answer only from the material above. Every figure, name and date you "
        "state must appear in it -- if it does not, you do not know it, and "
        f"saying so is the correct answer. Never write \"according to\" a "
        f"publication that is not listed above. If the sources disagree, say so "
        f"rather than picking one."
    )
    if kind == NEWS:
        return (
            f"[{common} Give {user_name} the few things that actually matter, "
            f"most important first, in your own voice -- not a list of headlines. "
            f"Attribute each to the outlet that reported it and include the date "
            f"where the source gives one. If the sources are only background "
            f"(an encyclopaedia entry, a tourism page) say that you could not "
            f"find current reporting rather than presenting background as news.]"
        )
    if kind == PRODUCT:
        return (
            f"[{common} Give {user_name} the options with their prices and the "
            f"link for each, so they can go and look. Note which listing each "
            f"price came from, and say if a price looks stale or unavailable.]"
        )
    if kind == QUOTE:
        return (
            f"[{common} If a live quote block appears elsewhere in this "
            f"conversation, prefer its figures over anything in these pages -- "
            f"pages go stale. State the time the figure refers to.]"
        )
    return f"[{common} Answer directly, then cite which numbered source each "\
           f"claim came from.]"


def no_results_prompt(findings: "Findings", user_name: str = "you") -> str:
    """What to say when nothing was retrieved.

    The persona tells the model it can look things up in real time and should
    never claim otherwise, which is true and useful -- right up to the moment a
    search returns nothing, at which point that instruction is still sitting
    there and the model narrates a lookup that did not happen. This is the
    counterweight, and the mirror of data.no_data_prompt.
    """
    detail = "; ".join(findings.problems[:2])
    tried = ", ".join(repr(q) for q in findings.queries[:3]) or "the question"
    because = f" ({detail})" if detail else ""
    return (
        f"[A web search was run for {tried} and returned nothing usable{because}. "
        f"You therefore have NO current information on this. Do not state any "
        f"figure, headline, price or date as current, do not attribute anything to "
        f"a publication, and do not repeat a number {user_name} mentioned as though "
        f"you had confirmed it. Say plainly that you couldn't find anything "
        f"reliable just now, and suggest where {user_name} could look. Admitting "
        f"the gap is the correct answer; a plausible invented one is not.]"
    )



_PAGE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(str(url or "")).netloc or str(url or "")
    except Exception:
        return str(url or "")


def _canonical(url: str) -> str:
    """Enough of a URL to spot the same page arriving from two queries."""
    try:
        parts = urllib.parse.urlparse(str(url or "").lower())
        host = parts.netloc[4:] if parts.netloc.startswith("www.") else parts.netloc
        return f"{host}{parts.path.rstrip('/')}"
    except Exception:
        return str(url or "").lower()


# --- the search engine ------------------------------------------------------
class WebSearch:
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self._DDGS = None
        try:
            try:
                from ddgs import DDGS  # new package name
            except ImportError:
                from duckduckgo_search import DDGS  # older name
            self._DDGS = DDGS
        except Exception:
            self._DDGS = None  # fine -- we fall back to the requests backend.

    @property
    def backend(self) -> str:
        return "ddgs" if self._DDGS else "requests-fallback"

    # public ---------------------------------------------------------------
    def search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        if self._DDGS:
            hits = self._ddgs_text(query, max_results)
            if hits:
                return hits
        return self._fallback(query, max_results)

    def news(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        if self._DDGS:
            hits = self._ddgs_news(query, max_results)
            if hits:
                return hits
        # The fallback endpoint doesn't separate news; a normal search still
        # surfaces recent articles well enough.
        return self._fallback(query, max_results)

    # ddgs backend ---------------------------------------------------------
    def _ddgs_text(self, query: str, max_results: int) -> List[Dict[str, str]]:
        try:
            with self._DDGS() as d:
                raw = d.text(query, max_results=max_results)
                return [
                    {
                        "title": r.get("title", ""),
                        "body": r.get("body", ""),
                        "url": r.get("href", r.get("url", "")),
                    }
                    for r in raw
                ]
        except Exception as exc:
            print(f"[search] ddgs text failed ({exc}); using fallback.")
            return []

    def _ddgs_news(self, query: str, max_results: int) -> List[Dict[str, str]]:
        try:
            with self._DDGS() as d:
                raw = d.news(query, max_results=max_results)
                return [
                    {
                        "title": r.get("title", ""),
                        "body": r.get("body", ""),
                        "url": f"{r.get('source', '')} - {r.get('url', '')}".strip(" -"),
                    }
                    for r in raw
                ]
        except Exception as exc:
            print(f"[search] ddgs news failed ({exc}); using fallback.")
            return []

    # requests fallback ----------------------------------------------------
    _LINK_RE = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S
    )
    _SNIP_RE = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', re.S
    )
    _TAG_RE = re.compile(r"<[^>]+>")

    def _clean(self, raw: str) -> str:
        return _html.unescape(self._TAG_RE.sub("", raw)).strip()

    def _decode_url(self, href: str) -> str:
        # DuckDuckGo wraps links as //duckduckgo.com/l/?uddg=<encoded>
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))
        return href if href.startswith("http") else f"https:{href}"

    # reading pages ---------------------------------------------------------
    def fetch_page(self, url: str, limit: int = MAX_PAGE_CHARS) -> str:
        """The readable text of one page, or "" if it can't be had.

        Never raises: a page that will not load is a page that is skipped, and
        the caller still has its snippet.
        """
        if not str(url or "").startswith(("http://", "https://")):
            return ""
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": _PAGE_UA, "Accept": "text/html,*/*"},
                timeout=self.timeout,
                allow_redirects=True,
            )
            if resp.status_code >= 400:
                return ""
            kind = resp.headers.get("Content-Type", "")
            if kind and not kind.lower().startswith(("text/", "application/xhtml")):
                return ""          # a PDF or an image is not prose to read
            if len(resp.content or b"") > MAX_PAGE_BYTES:
                return ""
            return readable_text(resp.text, limit=limit)
        except Exception as exc:
            print(f"[search] couldn't read {url} ({exc.__class__.__name__})")
            return ""

    def gather(self, question: str, kind: str = "", max_results: int = 5,
               read_pages: int = 3, on_step=None) -> "Findings":
        """Search several ways, read the best pages, and return what was found.

        This is the part that was missing. Previously one query was run and its
        snippets were pasted in; asked what was happening in a state, that
        returned an encyclopaedia entry about its culture, which duly came back
        as though it were the day's news.
        """
        kind = kind or classify(question)
        queries = plan_queries(question, kind)
        findings = Findings(question=question, kind=kind, queries=queries)
        if not queries:
            return findings

        seen_urls = set()
        for query in queries:
            if on_step:
                on_step(f"Searching: {query}")
            try:
                hits = (self.news(query, max_results) if kind == NEWS
                        else self.search(query, max_results))
            except Exception as exc:
                findings.problems.append(
                    f"search for {query!r} failed ({exc.__class__.__name__})")
                continue
            if not hits:
                findings.problems.append(f"nothing found for {query!r}")
            for hit in hits:
                url = str(hit.get("url") or "").strip()
                key = _canonical(url)
                if not url or key in seen_urls:
                    continue
                seen_urls.add(key)
                findings.sources.append(Source(
                    title=str(hit.get("title") or "").strip(),
                    url=url,
                    snippet=str(hit.get("body") or "").strip(),
                ))

        # Read the most promising handful in full. Ordered as the engines ranked
        # them, which is the only signal available here.
        for source in findings.sources[:max(0, read_pages)]:
            if on_step:
                on_step(f"Reading {_host(source.url)}")
            source.body = self.fetch_page(source.url)
            if not source.body:
                findings.problems.append(f"couldn't read {_host(source.url)}")

        return findings

    def _fallback(self, query: str, max_results: int) -> List[Dict[str, str]]:
        try:
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0 Safari/537.36"
                    )
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            html_text = resp.text
            links = self._LINK_RE.findall(html_text)
            snippets = self._SNIP_RE.findall(html_text)
            results: List[Dict[str, str]] = []
            for i, (href, title) in enumerate(links[:max_results]):
                body = self._clean(snippets[i]) if i < len(snippets) else ""
                results.append(
                    {
                        "title": self._clean(title),
                        "body": body,
                        "url": self._decode_url(href),
                    }
                )
            return results
        except Exception as exc:
            print(f"[search] Web search failed ({exc}).")
            return []
