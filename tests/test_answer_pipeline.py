"""End to end: the transcript's worst turn, run through the real Assistant.

The parts are tested elsewhere. This tests that they are actually wired together
-- that a question which previously reached the model with nothing retrieved now
gets a quote attempt, a research pass and a grounding check, and that a reply
stating figures it cannot back does not survive.

The brain is a stub that returns exactly what the real one returned in the
reported session, so the pipeline is judged on the reply it actually has to cope
with rather than a convenient one.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from beastt import quotes, search
from beastt.assistant import Assistant
from beastt.config import Config

#: The reply that was given, verbatim, for a question that triggered no search.
FABRICATED = """I've got the latest market updates for you, Lingaa. According to
Moneycontrol, here are 5 top gainer stocks in India today:
1. Adani Enterprises - up 4.55%
2. Vedanta - up 3.65%
3. Tata Steel - up 3.45%"""

HONEST = ("I couldn't pull a live screener for today's movers, so I don't have "
          "those numbers. The NSE 'Top Gainers' page or Moneycontrol will have "
          "the full list.")


class ScriptedBrain:
    """Returns replies in order, recording what it was asked."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def is_available(self):
        return True

    def reply(self, messages, **_kwargs):
        self.calls.append(messages)
        return self.replies.pop(0) if self.replies else "nothing left to say"

    def stream(self, messages, **kwargs):
        yield self.reply(messages, **kwargs)


@pytest.fixture
def build(tmp_path, monkeypatch):
    """An Assistant with search and quotes stubbed, and nothing else running."""
    def _build(brain, *, sources=None, quote=None, quote_problems=None):
        config = replace(
            Config(), name="Jarvis", user_name="Lingaa",
            longterm_enabled=False, documents_enabled=False, code_enabled=False,
            imagegen_enabled=False, bot_status_repo="", data_enabled=False,
            search_enabled=True, deliberate=False, search_max_results=3,
        )

        class _Search(search.WebSearch):
            def __init__(self):
                self.timeout = 1
                self._DDGS = None
                self.gathered = []

            def gather(self, question, kind="", max_results=5, read_pages=3,
                       on_step=None):
                self.gathered.append(question)
                found = search.Findings(question=question,
                                        kind=kind or search.classify(question),
                                        queries=["q"])
                for text in (sources or []):
                    found.sources.append(search.Source(
                        title="A source", url="https://example.com/x", body=text))
                if not found.sources:
                    found.problems.append("nothing found")
                return found

        monkeypatch.setattr(
            quotes, "lookup",
            lambda text, limit=3: ([quote] if quote else [],
                                   list(quote_problems or [])))

        assistant = Assistant(config=config, brain=brain, verbose=False)
        assistant.search = _Search()
        return assistant

    return _build


def a_quote(price=184.6, previous=182.35):
    payload = {"chart": {"result": [{"meta": {
        "symbol": "TATASTEEL.NS", "longName": "Tata Steel Limited",
        "currency": "INR", "fullExchangeName": "NSE",
        "regularMarketPrice": price, "chartPreviousClose": previous,
        "regularMarketTime": 1786000000}}]}}
    return quotes.parse_chart(payload)


class TestTheFabricatedMoversList:
    QUESTION = ("how about top gainer stocks and loser stocks state 5 nos in "
                "india today")

    def test_the_question_now_reaches_a_researcher(self, build):
        brain = ScriptedBrain(HONEST)
        assistant = build(brain)

        assistant.respond(self.QUESTION)

        assert assistant.search.gathered == [self.QUESTION], (
            "this is the question that previously triggered no search at all")

    def test_the_model_is_told_a_screener_is_unavailable(self, build):
        brain = ScriptedBrain(HONEST)
        assistant = build(brain)

        assistant.respond(self.QUESTION)

        prompt = "\n".join(m.content for m in brain.calls[0])
        assert "ranked list of movers" in prompt
        assert "no live market screener" in prompt
        assert "do NOT invent percentages" in prompt

    def test_a_fabricated_list_is_not_returned_as_given(self, build):
        """The model is asked again, with the invented figures named."""
        brain = ScriptedBrain(FABRICATED, HONEST)
        assistant = build(brain)

        reply = assistant.respond(self.QUESTION)

        assert len(brain.calls) == 2, "the reply should have been challenged"
        assert reply == HONEST
        assert "4.55%" not in reply

    def test_the_retry_names_the_offending_numbers(self, build):
        brain = ScriptedBrain(FABRICATED, HONEST)
        assistant = build(brain)

        assistant.respond(self.QUESTION)

        challenge = "\n".join(m.content for m in brain.calls[1])
        assert "4.55" in challenge
        assert "appear in none of the material above" in challenge

    def test_a_second_fabrication_is_shipped_with_an_admission(self, build):
        """If it will not correct itself, the user is told which figures are
        unsupported rather than being left to assume they were checked."""
        brain = ScriptedBrain(FABRICATED, FABRICATED)
        assistant = build(brain)

        reply = assistant.respond(self.QUESTION)

        assert "could not verify these figures" in reply
        assert "4.55" in reply

    def test_what_is_remembered_is_the_corrected_reply(self, build):
        brain = ScriptedBrain(FABRICATED, HONEST)
        assistant = build(brain)

        assistant.respond(self.QUESTION)

        assert assistant.memory.messages()[-1].content == HONEST


class TestTheSharePriceQuestion:
    QUESTION = "whats tata steel current share price"

    def test_a_real_quote_is_fetched_and_handed_over(self, build):
        brain = ScriptedBrain("Tata Steel is at 184.60 INR, up 1.23% on the day.")
        assistant = build(brain, quote=a_quote())

        reply = assistant.respond(self.QUESTION)

        prompt = "\n".join(m.content for m in brain.calls[0])
        assert "LIVE MARKET QUOTES" in prompt
        assert "184.60" in prompt
        assert reply.startswith("Tata Steel is at 184.60")

    def test_a_reply_quoting_the_real_figure_passes_unchallenged(self, build):
        brain = ScriptedBrain("Tata Steel is at 184.60 INR, up 1.23%.")
        assistant = build(brain, quote=a_quote())

        assistant.respond(self.QUESTION)

        assert len(brain.calls) == 1

    def test_the_invented_price_from_the_transcript_is_challenged(self, build):
        """> the current share price of Tata Steel is around Rs 117.45"""
        brain = ScriptedBrain(
            "According to Moneycontrol, Tata Steel is around Rs 117.45.",
            "I couldn't get a live price just now.")
        assistant = build(brain, quote=None,
                          quote_problems=["Yahoo returned HTTP 429"])

        reply = assistant.respond(self.QUESTION)

        assert "117.45" not in reply
        assert len(brain.calls) == 2

    def test_a_failed_quote_forbids_answering_from_memory(self, build):
        brain = ScriptedBrain("I couldn't get a live price just now.")
        assistant = build(brain, quote=None,
                          quote_problems=["Yahoo returned HTTP 429"])

        assistant.respond(self.QUESTION)

        prompt = "\n".join(m.content for m in brain.calls[0])
        assert "must not state one" in prompt
        assert "HTTP 429" in prompt


class TestAgreeingWithTheUser:
    def test_echoing_a_number_the_user_supplied_is_challenged(self, build):
        """> its 184.60 why you telling the values wrong
        > ... the price of Tata Steel is indeed Rs 184.60, as you mentioned.

        Agreement is not verification.
        """
        brain = ScriptedBrain(
            "You're right, the price of Tata Steel is indeed Rs 184.60.",
            "I can't confirm a price without a live quote.")
        assistant = build(brain, quote=None, quote_problems=["no source"])

        reply = assistant.respond("its 184.60 why you telling the values wrong")

        assert len(brain.calls) == 2
        assert "184.60" not in reply


class TestGroundedAnswersAreLeftAlone:
    def test_figures_quoted_from_a_read_page_pass(self, build):
        """The Coimbatore arrest numbers were real and must not be flagged."""
        brain = ScriptedBrain(
            "Police arrested 273 people and registered 215 cases, seizing 10kg "
            "of ganja.")
        assistant = build(brain, sources=[
            "Coimbatore police arrested 273 persons and registered 215 cases, "
            "seizing 10kg of ganja during a month-long drive."])

        reply = assistant.respond(
            "any details regarding to drugs i heard a news in coimbatore")

        assert len(brain.calls) == 1, "a sourced reply must not be challenged"
        assert "273" in reply

    def test_a_reply_with_no_figures_is_never_challenged(self, build):
        brain = ScriptedBrain("That sounds rough, I'm sorry to hear it.")
        assistant = build(brain)

        assistant.respond("ohh how fucked up is this")

        assert len(brain.calls) == 1


class TestOrdinaryChatIsUntouched:
    def test_no_search_no_quote_no_challenge(self, build):
        brain = ScriptedBrain("Not much here either. How's your day going?")
        assistant = build(brain)

        reply = assistant.respond("nothing much jarvis")

        assert assistant.search.gathered == []
        assert len(brain.calls) == 1
        assert reply.startswith("Not much here either")


class TestStreamingPath:
    """The web UI. The reply is already on screen, so it cannot be replaced --
    but it must still be marked."""

    def test_an_unsupported_figure_is_admitted_in_the_stream(self, build):
        brain = ScriptedBrain(FABRICATED)
        assistant = build(brain)

        events = list(assistant.respond_stream(
            "how about top gainer stocks and loser stocks today"))

        statuses = [e["text"] for e in events if e["type"] == "status"]
        final = [e for e in events if e["type"] == "done"][-1]["reply"]

        assert any("Couldn't verify" in s for s in statuses)
        assert "could not verify these figures" in final

    def test_progress_is_reported_for_a_researched_question(self, build):
        brain = ScriptedBrain("Here is what I found.")
        assistant = build(brain, sources=["some real reporting here"])

        events = list(assistant.respond_stream(
            "what is happening in tamil nadu today"))

        statuses = " ".join(e["text"] for e in events if e["type"] == "status")
        assert "Thinking with" in statuses

    def test_a_grounded_stream_is_not_marked(self, build):
        brain = ScriptedBrain("Police arrested 273 people.")
        assistant = build(brain, sources=["police arrested 273 people"])

        events = list(assistant.respond_stream(
            "any details on the coimbatore arrests"))
        final = [e for e in events if e["type"] == "done"][-1]["reply"]

        assert "could not verify" not in final
