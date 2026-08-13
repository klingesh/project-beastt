"""Telling the assistant something is not asking it to look something up.

From a live session, and the worst answer yet produced:

    > i live in chennai jarvis fyi
    Got it, Lingaa -- so you're already enjoying the Jarvis community in
    Chennai! It's the Casagrand-built, 8.5-acre gated enclave with 469
    apartments, 2/3/4 BHK options and over 100 amenities, slated for handover in
    March 2028.

Two faults compounded. The message was a *statement* -- a fact being offered, to
be remembered -- and it triggered a web search anyway, because "live" was a
recency trigger and "i live in chennai" contains it. Then the assistant's own
name went into the search query, because the lead-in stripping had a hardcoded
"beastt" in it and this assistant is called Jarvis. The search found a Chennai
apartment development named *Jarvis*, and every detail in that reply is real,
sourced, and about a housing project the user has nothing to do with.

Worth being precise about why this is worse than the earlier hallucinations: the
grounding check passed it. It had to. Every figure was on the page.

Also covered here: the reference markers the same session produced, the total
size of the retrieved block, and the local model's opaque error.
"""

from __future__ import annotations

import pytest

from beastt import grounding, search

NAME = "Jarvis"

#: Things the user is telling the assistant. None should be researched.
STATEMENTS = [
    "i live in chennai jarvis fyi",
    "i live in pg not casagrand",
    "i live in coimbatore now",
    "i work at infosys",
    "i study engineering at psg",
    "i have an rtx 3050 laptop",
    "my birthday is in june",
    "my laptop is slow these days",
    "i'm in my final year",
    "im at college attending my classes",
    "we moved house last month",
    "i run a trading bot on a vps",
]

#: Things the user is asking. All should be researched.
QUESTIONS = [
    "i heard a school student was murdered in coimbatore",
    "i read that the cpi came in lower",
    "i want to know the cpi",
    "i need the current price of tata steel",
    "i'm curious what happened in tamil nadu",
    "whats tata steel current share price",
    "top gainers in india today",
    "what is happening in tamil nadu",
    "any news on the assembly elections",
    "my portfolio is down, whats the market doing today",
]


class TestStatementsAreNotResearched:
    @pytest.mark.parametrize("text", STATEMENTS)
    def test_a_statement_is_not_a_search(self, text):
        assert search.needs_search(text, NAME) is False, (
            f"{text!r} is the user telling the assistant something; researching "
            "it invents a life for them out of search results")

    @pytest.mark.parametrize("text", STATEMENTS)
    def test_a_statement_is_ordinary_conversation(self, text):
        assert search.classify(text, NAME) == search.CHAT

    def test_the_reported_message(self):
        assert search.needs_search("i live in chennai jarvis fyi", NAME) is False

    def test_the_follow_up_correction(self):
        """> i live in pg not casagrand -- which was researched too, and came
        back with Stanza Living's price list as though it were the user's rent."""
        assert search.needs_search("i live in pg not casagrand", NAME) is False

    def test_an_instruction_to_remember_is_not_researched(self):
        """It is a write, not a read."""
        for text in ["remember that i live in chennai",
                     "remember i live in coimbatore"]:
            assert search.needs_search(text, NAME) is False

    @pytest.mark.parametrize("text", QUESTIONS)
    def test_a_question_still_searches(self, text):
        assert search.needs_search(text, NAME) is True

    def test_hearsay_is_deliberately_not_a_statement(self):
        """"i heard X" is first-person, but it is the clearest possible ask to go
        and check something -- and the session where that mattered is entry 8b."""
        assert search.looks_like_statement("i heard a student was murdered",
                                           NAME) is False
        assert search.needs_search("i heard a student was murdered", NAME) is True

    def test_a_question_mark_settles_it(self):
        assert search.looks_like_statement("i live in chennai", NAME) is True
        assert search.looks_like_statement("i live in chennai?", NAME) is False

    @pytest.mark.parametrize("text", ["", None, "   ", "hello"])
    def test_non_statements(self, text):
        assert search.looks_like_statement(text, NAME) is False


class TestLiveMustBeQualified:
    """"live" was a bare recency trigger, which is how "i live in chennai" ended
    up at a search engine."""

    @pytest.mark.parametrize("text", [
        "i live in chennai", "where do i live", "i have lived here for years",
        "live and let live",
    ])
    def test_the_verb_does_not_trigger(self, text):
        assert not search._TRIGGER_RE.search(text) or "live " not in text

    @pytest.mark.parametrize("text", [
        "live price of tata steel", "live scores", "live market data",
        "give me the live rates", "going live",
    ])
    def test_but_live_data_still_does(self, text):
        assert search.needs_search(text, NAME) is True


class TestTheAssistantsNameNeverReachesTheQuery:
    def test_the_reported_query(self):
        """This is what found the Casagrand development."""
        assert "jarvis" not in search.extract_query(
            "i live in chennai jarvis fyi", NAME).lower()

    @pytest.mark.parametrize("text", [
        "hey jarvis whats the cpi",
        "jarvis look up tata steel",
        "so jarvis, what is happening in tamil nadu",
        "whats the cpi jarvis",
        "JARVIS what is the nifty at",
    ])
    def test_it_is_stripped_wherever_it_appears(self, text):
        query = search.extract_query(text, NAME)
        assert "jarvis" not in query.lower()
        assert query.strip() != ""

    def test_the_shipped_names_are_stripped_even_without_config(self):
        """The lead-in list hardcoded "beastt", so renaming the assistant broke
        it silently. Both shipped names are handled either way now."""
        assert "beastt" not in search.extract_query("hey beastt whats the cpi").lower()
        assert "jarvis" not in search.extract_query("hey jarvis whats the cpi").lower()

    def test_a_renamed_assistant_is_stripped(self):
        assert "orion" not in search.extract_query(
            "orion, what is the cpi", "Orion").lower()

    def test_mishearings_of_the_name_are_stripped_too(self):
        """Voice input arrives as whatever Whisper heard."""
        for heard in ["javis", "jervis", "jarviss"]:
            assert heard not in search.extract_query(
                f"{heard} what is the cpi", NAME).lower()

    def test_the_subject_survives(self):
        assert search.extract_query("jarvis look up tata steel", NAME) == "tata steel"

    def test_a_name_that_is_also_the_subject_is_a_known_cost(self):
        """Stripping is unconditional, so asking about a company called Jarvis
        loses the word. Accepted deliberately: the assistant's own name appearing
        in a message addressed to it is far commoner than a question about
        something else of the same name, and the alternative was the Casagrand
        answer. Recorded here so the trade-off is visible rather than surprising.
        """
        query = search.extract_query("what is jarvis the company", NAME)

        assert "jarvis" not in query.lower()
        assert "company" in query.lower()

    def test_planning_and_classifying_use_the_cleaned_text(self):
        plans = search.plan_queries("jarvis what is happening in tamil nadu",
                                    name=NAME)
        assert plans
        assert all("jarvis" not in p.lower() for p in plans)


class TestReferenceMarkersAreNotFigures:
    #: The reply from the session, with the markers the model emitted.
    REPLY = ("a triple-share in Porur starts around Rs 19,199 per month "
             "(Rs 18,399 off-peak)\u30102\u2020L31-L35\u3011, while a "
             "double-share in Pallavaram can be as low as Rs "
             "9,499\u30102\u2020L61-L64\u3011")

    def test_only_the_prices_are_treated_as_claims(self):
        assert grounding.figures(self.REPLY) == ["19,199", "18,399", "9,499"]

    def test_the_caveat_no_longer_lists_line_numbers(self):
        """It read "I could not verify these figures: 35, 61, 64, 17, 22" --
        which are line numbers, and meaningless to anyone reading it."""
        verdict = grounding.check(self.REPLY, sources=["Rs 19,199 Rs 18,399 Rs 9,499"])
        assert verdict.ok is True

    @pytest.mark.parametrize("marker", [
        "\u30102\u2020L31-L35\u3011", "[2\u2020L31-L35]", "[1]", "[^2]",
        # A marker whose own numbers are large enough to look like claims. The
        # session's markers cited source 2, and a single digit is filtered as
        # structure anyway -- so without this case the whole bracket rule is
        # redundant with the line-reference rule and a test cannot tell.
        "\u301012\u2020moneycontrol.com\u3011",
        "[27\u2020nseindia.com]",
    ])
    def test_every_bracketed_marker_shape(self, marker):
        """These arrive attached to the preceding word, as the session showed."""
        assert grounding.figures(f"the price is Rs 500{marker}") == ["500"]

    @pytest.mark.parametrize("marker", ["L31-L35", "L31", "(L31-L35)"])
    def test_bare_line_references(self, marker):
        """Unbracketed, so they need a boundary to be recognised -- which is how
        they appear in prose."""
        assert grounding.figures(f"the price is Rs 500 {marker}") == ["500"]

    def test_the_instruction_asks_for_plain_citations(self):
        findings = search.Findings(
            question="q", kind=search.NEWS, queries=["q"],
            sources=[search.Source(title="T", url="u", body="b")])
        block = search.format_findings(findings, "Lingaa")

        assert "(source 2)" in block
        assert "Do not emit bracketed reference markers" in block


class TestTheRetrievedBlockIsBounded:
    """Three pages at six thousand characters plus a dozen snippets came to over
    twenty thousand, which a local model rejected outright -- surfacing to the
    user as "I hit a snag trying to think that through (HTTPError)"."""

    def _sources(self, count, size):
        return [search.Source(title=f"S{i}", url=f"https://e{i}.com/x",
                              snippet="snip", body="word " * size)
                for i in range(count)]

    def test_a_large_haul_is_trimmed_to_the_budget(self):
        findings = search.Findings(question="q", kind=search.NEWS,
                                  queries=["a", "b", "c"],
                                  sources=self._sources(4, 3000))

        block = search.format_findings(findings, "Lingaa")

        assert len(block) < search.MAX_RESEARCH_CHARS + 2000
        assert "trimmed to fit" in block

    def test_every_source_is_still_listed(self):
        """Knowing a page exists and was not included is worth a line."""
        findings = search.Findings(question="q", kind=search.NEWS, queries=["a"],
                                  sources=self._sources(6, 3000))

        block = search.format_findings(findings, "Lingaa")

        for index in range(1, 7):
            assert f"[{index}]" in block

    def test_a_small_haul_is_untouched(self):
        findings = search.Findings(question="q", kind=search.NEWS, queries=["a"],
                                  sources=self._sources(2, 50))

        block = search.format_findings(findings, "Lingaa")

        assert "trimmed to fit" not in block
        assert "no room left" not in block

    def test_the_earliest_sources_keep_their_text(self):
        """Ordered as the engines ranked them, so what gets cut is what was least
        promising."""
        findings = search.Findings(question="q", kind=search.NEWS, queries=["a"],
                                  sources=self._sources(5, 2000))

        blocks = search._within_budget(findings.sources)

        assert "trimmed" not in blocks[0]
        assert "trimmed" in blocks[-1] or "no room left" in blocks[-1]


class TestTheLocalModelsErrorIsReadable:
    """`raise_for_status()` produced "(HTTPError)", which names the exception
    class and nothing about the cause."""

    class _Resp:
        def __init__(self, status_code, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload
            self.text = text

        def json(self):
            if self._payload is None:
                raise ValueError("no json")
            return self._payload

    def test_a_context_length_refusal_says_what_to_do(self):
        from beastt.brain.ollama_brain import OllamaError, _check

        resp = self._Resp(400, {"error": "input length exceeds context length"})

        with pytest.raises(OllamaError) as err:
            _check(resp)

        message = str(err.value)
        assert "too long" in message
        assert "BEASTT_SEARCH_READ_PAGES=1" in message

    def test_another_refusal_carries_ollamas_own_reason(self):
        from beastt.brain.ollama_brain import OllamaError, _check

        resp = self._Resp(404, {"error": "model 'llama9' not found"})

        with pytest.raises(OllamaError, match="not found"):
            _check(resp)

    def test_a_body_with_no_json_still_explains_itself(self):
        from beastt.brain.ollama_brain import OllamaError, _check

        with pytest.raises(OllamaError, match="502"):
            _check(self._Resp(502, None, ""))

    def test_a_good_response_passes_silently(self):
        from beastt.brain.ollama_brain import _check

        assert _check(self._Resp(200, {"message": {"content": "hi"}})) is None


class TestTheAssistantPassesItsNameThrough:
    """A regression guard with a specific history: the stub used in the pipeline
    tests did not accept the new `name` argument, so `gather()` raised TypeError,
    the broad catch in _research turned it into "nothing found", and three tests
    failed for a reason that had nothing to do with what they were testing."""

    def test_gather_accepts_a_name(self):
        import inspect

        signature = inspect.signature(search.WebSearch.gather)
        assert "name" in signature.parameters

    def test_the_name_reaches_the_search_decision_too(self, tmp_path,
                                                      monkeypatch):
        """Pins the argument rather than a behaviour.

        For an assistant called Jarvis or Beastt this line is near-redundant --
        those names are stripped whether or not the configured one is passed, so
        removing the argument changes nothing observable and no behavioural test
        can catch it. It matters for a renamed assistant, and an unpinned line is
        exactly the drift that already broke three tests once, when a stub's
        signature fell behind the caller's.
        """
        from dataclasses import replace

        import beastt.assistant as assistant_module
        from beastt.assistant import Assistant
        from beastt.config import Config

        seen = {}
        real = assistant_module.needs_search

        def recording(text, name=""):
            seen["name"] = name
            return real(text, name)

        monkeypatch.setattr(assistant_module, "needs_search", recording)

        class _Brain:
            def is_available(self):
                return True

            def reply(self, _messages, **_kwargs):
                return "ok"

            def stream(self, _messages, **_kwargs):
                yield "ok"

        config = replace(Config(), name="Orion", user_name="Lingaa",
                         search_enabled=True, longterm_enabled=False,
                         documents_enabled=False, code_enabled=False,
                         imagegen_enabled=False, bot_status_repo="",
                         data_enabled=False, deliberate=False,
                         quotes_enabled=False)
        class _Search(search.WebSearch):
            def __init__(self):
                self.timeout = 1
                self._DDGS = None

            def gather(self, question, kind="", max_results=5, read_pages=3,
                       on_step=None, name=""):
                return search.Findings(question=question, kind=kind)

        assistant = Assistant(config=config, brain=_Brain(), verbose=False)
        # A stub rather than None: `_should_search` checks `self.search is None`
        # first, so leaving it unset short-circuits before needs_search is ever
        # reached and the test would pass while measuring nothing.
        assistant.search = _Search()

        assistant.respond("what is happening in tamil nadu")

        assert seen.get("name") == "Orion"

    def test_the_assistant_hands_its_name_to_the_researcher(self, tmp_path):
        from dataclasses import replace

        from beastt.assistant import Assistant
        from beastt.config import Config

        seen = {}

        class _Search(search.WebSearch):
            def __init__(self):
                self.timeout = 1
                self._DDGS = None

            def gather(self, question, kind="", max_results=5, read_pages=3,
                       on_step=None, name=""):
                seen["name"] = name
                return search.Findings(question=question, kind=kind)

        class _Brain:
            def is_available(self):
                return True

            def reply(self, _messages, **_kwargs):
                return "ok"

            def stream(self, _messages, **_kwargs):
                yield "ok"

        config = replace(Config(), name="Jarvis", user_name="Lingaa",
                         search_enabled=True, longterm_enabled=False,
                         documents_enabled=False, code_enabled=False,
                         imagegen_enabled=False, bot_status_repo="",
                         data_enabled=False, deliberate=False,
                         quotes_enabled=False)
        assistant = Assistant(config=config, brain=_Brain(), verbose=False)
        assistant.search = _Search()

        assistant.respond("what is happening in tamil nadu")

        assert seen.get("name") == "Jarvis"
