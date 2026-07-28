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
from typing import Dict, List

import requests

# --- intent detection -------------------------------------------------------
_TRIGGERS = [
    r"\bsearch\b",
    r"\blook up\b",
    r"\bgoogle\b",
    r"\bwhat'?s happening\b",
    r"\bwhat is happening\b",
    r"\blatest\b",
    r"\bnews\b",
    r"\bcurrent(ly)?\b",
    r"\btoday'?s\b",
    r"\bright now\b",
    r"\bweather\b",
    r"\bprice of\b",
    r"\bstock price\b",
    r"\bwho won\b",
    r"\bupdates?\s+(on|about|regarding|for)\b",
    r"\bwhat'?s the latest\b",
    r"\bin the world\b",
]
_TRIGGER_RE = re.compile("|".join(_TRIGGERS), re.IGNORECASE)
_NEWS_RE = re.compile(r"\b(news|happening|headlines?|world|updates?)\b", re.IGNORECASE)

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
    return bool(_TRIGGER_RE.search(text))


def is_news(text: str) -> bool:
    return bool(_NEWS_RE.search(text))


def extract_query(text: str) -> str:
    """Turn a natural request into a concise search query."""
    q = _LEAD_RE.sub(" ", text)
    q = re.sub(r"[?!.]+$", "", q)  # trailing punctuation
    q = re.sub(r"\s+", " ", q).strip(" ,.")
    return q or text.strip()


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
