"""Researching a question properly: several angles, pages read, nothing invented.

Driven by the reported session. Three failures are pinned here:

* **"...state 5 nos in india today" triggered no search at all**, because the
  trigger was `today'?s` and matched "today's" but not "today". Nothing was
  retrieved, and the model invented a five-and-five table of movers.
* **"what's happening in Tamil Nadu" returned background as news** -- one query,
  and the top result was an encyclopaedia entry about the state's culture and
  tourism, which the reply presented as current developments.
* **"give me an update about top stocks that has gained"** was rewritten into the
  search query "so now an top stocks that has gained".

Nothing here touches the network: the searcher is a stub and pages are fixtures.
"""

from __future__ import annotations

import pytest

from beastt import search

#: Every question from the transcript that should have been researched.
SHOULD_SEARCH = [
    "can you get me an update on what is happening around the world",
    "so whats happening in tamilnadu, india",
    "how about any important news in my state?",
    "any details regarding to drugs i heard a news in coimbatore",
    "so any victims ? like i heard a kill has happened",
    "so i heard that a school student has been murdered in coimbatore",
    "so any update on the world news related to cpi and finance",
    "how about top gainer stocks and loser stocks state 5 nos in india today",
    "whats tata steel current share price",
    "so now give me an update about top stocks that has gained",
    "the values are wrong. can you please do some research and answer",
]

#: Conversation that must not fire off a search.
SHOULD_NOT_SEARCH = [
    "hey jarvis",
    "nothing much jarvis",
    "jarvissssss",
    "ohh how fucked up is this",
    "yeah jarvis its sad",
    "can you stop mentioning my friends name unnecessarily",
    "umm lots going on for now i am at college attending my classes",
]


class TestTheMissedTriggers:
    @pytest.mark.parametrize("text", SHOULD_SEARCH)
    def test_every_researchable_question_triggers(self, text):
        assert search.needs_search(text) is True, (
            f"{text!r} would reach the model with nothing retrieved, and a model "
            "asked for figures it does not have invents them")

    def test_bare_today_counts(self):
        """The single character that cost the whole answer: the pattern was
        `today'?s`, so "today" alone did not match."""
        assert search.needs_search("top gainers in india today") is True
        assert search.needs_search("today's gainers") is True

    #: Each pair isolates one trigger: the same sentence with and without it.
    #:
    #: Needed because the real questions match several triggers at once, so a
    #: test built from them keeps passing when an individual pattern is broken --
    #: which is exactly what happened. Reverting `today'?s?` to `today'?s` broke
    #: nothing visible, because "top gainer stocks ... today" also matches on
    #: "top" and on "gainer". Redundancy is good in the code and useless in a
    #: test.
    @pytest.mark.parametrize("with_trigger,without_it", [
        ("what did he say today", "what did he say"),
        ("i heard about it", "about it"),
        ("is it true", "tell me"),
        ("any victims", "any people"),
        ("it happened", "it occurred"),
        ("what is going on in chennai", "what is going on for me"),
        ("details about it", "info about it"),
        ("he was murdered", "he was upset"),
        ("there was a crash", "there was a party"),
        ("a raid took place", "a meeting took place"),
    ])
    def test_each_trigger_pulls_its_own_weight(self, with_trigger, without_it):
        assert search.needs_search(with_trigger) is True, (
            f"{with_trigger!r} relies on a trigger that is no longer firing")
        assert search.needs_search(without_it) is False, (
            f"{without_it!r} has no trigger and must not search -- if it does, "
            "the pair no longer isolates anything")

    @pytest.mark.parametrize("text", [
        "tata steel share price", "how much is a pixel 9",
        "top 5 stocks", "best laptop under 60000", "cheapest flight to delhi",
        "cpi report", "gdp growth", "nifty level", "bitcoin",
        "who won the match", "weather in chennai", "iphone 17 launch date",
    ])
    def test_the_classes_that_live_outside_a_models_weights(self, text):
        assert search.needs_search(text) is True

    @pytest.mark.parametrize("text", SHOULD_NOT_SEARCH)
    def test_ordinary_conversation_does_not_search(self, text):
        assert search.needs_search(text) is False

    @pytest.mark.parametrize("text", ["", None, "   "])
    def test_empty_input(self, text):
        assert search.needs_search(text) is False


class TestClassify:
    @pytest.mark.parametrize("text,kind", [
        ("whats tata steel current share price", search.QUOTE),
        ("how about top gainer stocks and loser stocks today", search.QUOTE),
        ("nifty today", search.QUOTE),
        ("so whats happening in tamilnadu, india", search.NEWS),
        ("any important news in my state", search.NEWS),
        ("cheapest iphone 16 price in india", search.PRODUCT),
        ("best laptop under 60000 review", search.PRODUCT),
        ("iphone 16 vs pixel 9", search.PRODUCT),
        ("who won the match yesterday", search.FACTUAL),
        ("weather in chennai today", search.FACTUAL),
        ("nothing much jarvis", search.CHAT),
        ("hey jarvis", search.CHAT),
    ])
    def test_questions_are_sorted_by_what_they_need(self, text, kind):
        assert search.classify(text) == kind

    def test_a_quote_question_beats_a_news_question(self):
        """"any update on tata steel share price" wants the price, not coverage."""
        assert search.classify(
            "any update on tata steel share price") == search.QUOTE


class TestExtractQuery:
    def test_the_mangled_query_from_the_transcript(self):
        """Was rewritten to "so now an top stocks that has gained", which is not
        a phrase anybody has written down, so it matched nothing useful."""
        query = search.extract_query(
            "so now give me an update about top stocks that has gained")

        assert not query.lower().startswith("so ")
        assert "an top" not in query.lower()
        assert "top stocks" in query.lower()
        assert "gained" in query.lower()

    @pytest.mark.parametrize("text,expected_words", [
        ("can you get me an update on what is happening around the world",
         ["world"]),
        ("so whats happening in tamilnadu, india", ["tamilnadu", "india"]),
        ("hey jarvis, look up the latest on cpi", ["cpi"]),
        ("please tell me the current price of tata steel",
         ["price", "tata", "steel"]),
    ])
    def test_the_subject_survives(self, text, expected_words):
        query = search.extract_query(text).lower()
        for word in expected_words:
            assert word in query

    def test_leading_filler_is_removed(self):
        for opener in ("so ", "and ", "ok ", "umm ", "well "):
            assert not search.extract_query(
                opener + "what is the cpi").lower().startswith(opener.strip())

    def test_it_never_returns_nothing(self):
        assert search.extract_query("latest") != ""


class TestPlanQueries:
    def test_news_is_asked_several_ways(self):
        """One query is one engine's opinion. Asked what was happening in a
        state, that opinion was a Wikipedia culture-and-tourism page."""
        plans = search.plan_queries("what is happening in tamil nadu",
                                    search.NEWS)

        assert len(plans) >= 3
        assert any("news" in p.lower() for p in plans)
        assert any("headlines" in p.lower() for p in plans)
        assert all("tamil nadu" in p.lower() for p in plans)

    def test_a_product_gets_price_and_review_angles(self):
        plans = search.plan_queries("cheapest iphone 16 in india",
                                    search.PRODUCT)
        joined = " ".join(plans).lower()
        assert "price" in joined
        assert "review" in joined

    def test_queries_are_unique(self):
        plans = search.plan_queries("latest news", search.NEWS)
        assert len(plans) == len(set(p.lower() for p in plans))

    def test_nothing_to_search_for(self):
        assert search.plan_queries("", search.NEWS) == []


# --- reading pages ---------------------------------------------------------
ARTICLE = """<!DOCTYPE html><html><head>
<title>Coimbatore drug crackdown</title>
<script>var tracker = {id: 99999, spend: 4200};</script>
<style>.headline { font-size: 42px; }</style>
</head><body>
<nav><a href="/sports">Sports</a><a href="/tv">TV</a></nav>
<header>Subscribe for 499 a year</header>
<article>
<h1>Police arrest 273 in month-long drive</h1>
<p>Coimbatore police registered 215 cases and seized 10kg of ganja.</p>
<p>The operation ran across the district.</p>
</article>
<footer>Copyright 2026. All rights reserved.</footer>
</body></html>"""


class TestReadableText:
    def test_the_article_survives(self):
        text = search.readable_text(ARTICLE)
        assert "Police arrest 273 in month-long drive" in text
        assert "registered 215 cases" in text

    def test_scripts_and_styles_are_dropped(self):
        """On a news page that is where most of the words are, and none of them
        are the article -- including numbers, which would otherwise 'ground' a
        figure that was never reported."""
        text = search.readable_text(ARTICLE)
        assert "tracker" not in text
        assert "99999" not in text
        assert "42px" not in text

    def test_navigation_and_footers_are_dropped(self):
        text = search.readable_text(ARTICLE)
        assert "Sports" not in text
        assert "Subscribe" not in text
        assert "All rights reserved" not in text

    def test_entities_are_decoded(self):
        assert "Tata & Sons" in search.readable_text("<p>Tata &amp; Sons</p>")

    def test_output_is_capped(self):
        text = search.readable_text("<p>" + ("word " * 5000) + "</p>", limit=500)
        assert len(text) <= 520
        assert text.endswith("...")

    @pytest.mark.parametrize("source", ["", None, "<html></html>"])
    def test_nothing_readable(self, source):
        assert search.readable_text(source) == ""


# --- gathering -------------------------------------------------------------
#: Captured before the class body, because _StubSearch defines a method called
#: `search`, which shadows the module of the same name for any default argument
#: evaluated later in that body.
_MAX_PAGE_CHARS = search.MAX_PAGE_CHARS


class _StubSearch(search.WebSearch):
    """A WebSearch with the network replaced."""

    def __init__(self, results=None, pages=None, fail=False):
        self.timeout = 1
        self._DDGS = None
        self._results = results if results is not None else {}
        self._pages = pages or {}
        self._fail = fail
        self.searched = []
        self.fetched = []

    def search(self, query, max_results=5):
        self.searched.append(query)
        if self._fail:
            raise RuntimeError("engine down")
        return self._results.get(query, self._results.get("*", []))

    def news(self, query, max_results=5):
        return self.search(query, max_results)

    def fetch_page(self, url, limit=_MAX_PAGE_CHARS):
        self.fetched.append(url)
        return self._pages.get(url, "")


def hit(title, url, body=""):
    return {"title": title, "url": url, "body": body}


class TestGather:
    def test_it_searches_every_planned_angle(self):
        stub = _StubSearch(results={"*": [hit("A", "https://a.com/1")]})

        findings = stub.gather("what is happening in tamil nadu")

        assert len(stub.searched) >= 3
        assert findings.kind == search.NEWS

    def test_the_same_page_from_two_queries_appears_once(self):
        stub = _StubSearch(results={"*": [
            hit("A", "https://www.thehindu.com/story/"),
            hit("A again", "https://thehindu.com/story"),
        ]})

        findings = stub.gather("tamil nadu news")

        urls = [s.url for s in findings.sources]
        assert len(urls) == 1, f"duplicates survived: {urls}"

    def test_the_top_pages_are_read_in_full(self):
        stub = _StubSearch(
            results={"*": [hit("A", "https://a.com/1", "snippet a"),
                           hit("B", "https://b.com/2", "snippet b")]},
            pages={"https://a.com/1": "the full article text"})

        findings = stub.gather("tata steel news", read_pages=2)

        assert findings.sources[0].read is True
        assert findings.sources[0].body == "the full article text"
        assert findings.sources[1].read is False
        assert findings.sources[1].snippet == "snippet b"

    def test_read_pages_can_be_turned_off(self):
        stub = _StubSearch(results={"*": [hit("A", "https://a.com/1")]})
        stub.gather("tata steel news", read_pages=0)
        assert stub.fetched == []

    def test_an_unreadable_page_is_noted_not_fatal(self):
        stub = _StubSearch(results={"*": [hit("A", "https://a.com/1", "snip")]},
                           pages={})

        findings = stub.gather("tamil nadu news", read_pages=1)

        assert findings.sources[0].read is False
        assert any("couldn't read" in p for p in findings.problems)

    def test_a_failing_engine_is_reported_not_raised(self):
        findings = _StubSearch(fail=True).gather("anything at all")

        assert findings.sources == []
        assert findings.problems

    def test_nothing_found_is_recorded(self):
        findings = _StubSearch(results={"*": []}).gather("tamil nadu news")

        assert not findings
        assert any("nothing found" in p for p in findings.problems)

    def test_progress_is_reported(self):
        steps = []
        stub = _StubSearch(results={"*": [hit("A", "https://a.com/1")]},
                           pages={"https://a.com/1": "text"})

        stub.gather("tamil nadu news", read_pages=1, on_step=steps.append)

        assert any("Searching" in s for s in steps)
        assert any("Reading" in s for s in steps)
        assert any("a.com" in s for s in steps)


class TestFindings:
    def test_texts_are_what_the_grounding_check_verifies_against(self):
        findings = search.Findings(sources=[
            search.Source(title="T", url="u", snippet="snip 273", body="body 215"),
        ])
        pooled = " ".join(findings.texts())
        assert "273" in pooled and "215" in pooled

    def test_a_source_says_whether_it_was_read_or_only_glimpsed(self):
        read = search.Source(title="T", url="u", snippet="s", body="full text")
        glimpsed = search.Source(title="T", url="u", snippet="s")

        assert "full page" in read.as_block()
        assert "search snippet only" in glimpsed.as_block()


class TestFormatFindings:
    def _findings(self, kind):
        return search.Findings(
            question="q", kind=kind, queries=["q1", "q2"],
            sources=[search.Source(title="The Hindu", url="https://thehindu.com/x",
                                   snippet="snip", body="full text here")])

    def test_sources_are_numbered_for_citation(self):
        block = search.format_findings(self._findings(search.FACTUAL), "Lingaa")
        assert "[1] The Hindu" in block
        assert "https://thehindu.com/x" in block

    def test_every_kind_forbids_unsupported_figures(self):
        for kind in (search.NEWS, search.PRODUCT, search.QUOTE, search.FACTUAL):
            block = search.format_findings(self._findings(kind), "Lingaa")
            assert "must appear in it" in block
            assert "according to" in block

    def test_news_asks_for_what_matters_not_a_headline_list(self):
        block = search.format_findings(self._findings(search.NEWS), "Lingaa")
        assert "most important first" in block
        assert "not a list of headlines" in block

    def test_news_forbids_passing_background_off_as_reporting(self):
        """The Tamil Nadu answer: a culture-and-tourism page presented as the
        day's developments."""
        block = search.format_findings(self._findings(search.NEWS), "Lingaa")
        assert "tourism page" in block
        assert "could not find current reporting" in block

    def test_a_product_answer_is_asked_for_links_per_price(self):
        block = search.format_findings(self._findings(search.PRODUCT), "Lingaa")
        assert "link for each" in block

    def test_a_quote_answer_prefers_the_live_block_over_pages(self):
        block = search.format_findings(self._findings(search.QUOTE), "Lingaa")
        assert "prefer its figures" in block


class TestNoResultsPrompt:
    """The counterweight to the persona telling the model it can look things up
    in real time -- true, and still sitting there when a search returns nothing."""

    def test_it_forbids_stating_anything_as_current(self):
        findings = search.Findings(question="q", queries=["top gainers india"],
                                   problems=["nothing found"])
        note = search.format_findings(findings, "Lingaa")

        assert "NO current information" in note
        assert "do not attribute anything to a publication" in note
        assert "top gainers india" in note

    def test_it_forbids_echoing_the_users_number_back(self):
        note = search.no_results_prompt(search.Findings(), "Lingaa")
        assert "repeat a number Lingaa mentioned" in note

    def test_it_prefers_an_admission_to_a_guess(self):
        note = search.no_results_prompt(search.Findings(), "Lingaa")
        assert "Admitting the gap is the correct answer" in note
