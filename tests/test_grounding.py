"""Every number in a reply has to come from somewhere.

Driven by the reply that prompted this, quoted verbatim in the fixtures below: a
list of five gainers and five losers with two-decimal percentages, attributed to
Moneycontrol, produced for a question that triggered no search at all.
"""

from __future__ import annotations

import pytest

from beastt import grounding

#: The actual hallucinated reply from the transcript.
INVENTED_REPLY = """I've got the latest market updates for you, Lingaa. According to
Moneycontrol, here are 5 top gainer stocks and 5 top loser stocks in India today:

Top Gainers:
1. Adani Enterprises - up 4.55%
2. Vedanta - up 3.65%
3. Tata Steel - up 3.45%
4. Hindalco - up 3.35%
5. JSW Steel - up 3.25%

Top Losers:
1. Yes Bank - down 4.85%
2. PNB - down 3.95%
"""

#: The follow-up, where it invented prices as well as percentages.
INVENTED_PRICES = """1. Adani Enterprises - Current price: Rs 2,341.90, Yesterday's
close: Rs 2,234.15, Gain: 4.83%"""


class TestFigures:
    def test_it_finds_the_invented_percentages(self):
        found = grounding.figures(INVENTED_REPLY)
        for percentage in ["4.55", "3.65", "3.45", "3.35", "3.25", "4.85", "3.95"]:
            assert percentage in found

    def test_it_finds_prices_with_thousands_separators(self):
        found = grounding.figures(INVENTED_PRICES)
        assert "2,341.90" in found
        assert "2,234.15" in found
        assert "4.83" in found

    def test_list_numbering_is_not_a_claim(self):
        """"1." through "5." are structure. Flagging them would bury the signal."""
        found = grounding.figures("1. Adani\n2. Vedanta\n3. Tata Steel")
        assert found == []

    @pytest.mark.parametrize("text", [
        "Keep replies to 1-4 sentences.",
        "here are 5 stocks",
        "the top 3 results",
        "I found 2 articles",
    ])
    def test_small_bare_counts_are_structure(self, text):
        assert grounding.figures(text) == []

    @pytest.mark.parametrize("text,expected", [
        ("the price is Rs 5", "5"),
        ("it rose 5%", "5"),
        ("about 5 percent", "5"),
        ("$5 a share", "5"),
        ("₹5", "5"),
        ("5 crore rupees", "5"),
    ])
    def test_money_or_a_unit_makes_a_small_number_a_claim(self, text, expected):
        assert expected in grounding.figures(text)

    def test_decimals_always_count(self):
        assert "2.7" in grounding.figures("inflation was 2.7 last month")

    def test_a_reply_with_no_numbers_has_no_figures(self):
        assert grounding.figures("I couldn't find anything reliable on that.") == []

    @pytest.mark.parametrize("text", ["", None])
    def test_empty_input(self, text):
        assert grounding.figures(text) == []


class TestTheReportedFailure:
    def test_the_invented_list_is_caught_when_nothing_was_retrieved(self):
        """The exact case: no search ran, so there are no sources at all."""
        verdict = grounding.check(
            INVENTED_REPLY, sources=[],
            question="how about top gainer stocks and loser stocks state 5 nos "
                     "in india today")

        assert verdict.ok is False
        assert "4.55" in verdict.invented
        assert len(verdict.invented) >= 7
        assert "not in any source" in verdict.why()

    def test_the_invented_prices_are_caught(self):
        verdict = grounding.check(INVENTED_PRICES, sources=["some unrelated text"])

        assert verdict.ok is False
        assert "2,341.90" in verdict.invented

    def test_repeating_the_users_own_number_is_not_verification(self):
        """> its 184.60 why you telling the values wrong
        > ... the current share price of Tata Steel is indeed Rs 184.60

        The number is not invented, but nothing checked it either. Agreeing with
        the user is not a source.
        """
        verdict = grounding.check(
            "According to my latest update, the current share price of Tata Steel "
            "is indeed Rs 184.60, as you mentioned.",
            sources=[],
            question="its 184.60 why you telling the values wrong")

        assert verdict.ok is False
        assert verdict.from_user == ["184.60"]
        assert verdict.invented == []
        assert "only from the question itself" in verdict.why()


class TestGroundedRepliesPass:
    def test_a_figure_quoted_from_a_source_is_fine(self):
        verdict = grounding.check(
            "Tata Steel is trading at Rs 184.60, up 1.2% today.",
            sources=["TATASTEEL.NS last price 184.60 INR, change +1.2% on the day"])

        assert verdict.ok is True
        assert set(verdict.grounded) == {"184.60", "1.2"}

    def test_thousands_separators_do_not_break_matching(self):
        verdict = grounding.check(
            "Equity stands at 10,248.37 USD.",
            sources=["equity 10248.37 balance 10195.20"])
        assert verdict.ok is True

    def test_a_source_printing_fewer_decimals_still_matches(self):
        """184.6 in the source and 184.60 in the reply are the same number."""
        verdict = grounding.check("It is at 184.60.", sources=["last 184.6"])
        assert verdict.ok is True

    def test_but_a_different_number_does_not_match(self):
        verdict = grounding.check("It is at 185.60.", sources=["last 184.6"])
        assert verdict.ok is False

    def test_the_news_figures_from_the_transcript_pass_when_sourced(self):
        """The Coimbatore drug-seizure numbers were real, and would have been in
        the search results. Those must not be flagged."""
        reply = ("the police arrested 273 people and registered 215 cases, "
                 "seizing over 10kg of ganja")
        sources = ["Coimbatore police arrested 273 persons and registered 215 "
                   "cases during the month-long drive, seizing 10kg of ganja"]

        assert grounding.check(reply, sources=sources).ok is True

    def test_several_sources_are_pooled(self):
        verdict = grounding.check(
            "Inflation is 2.7% and unemployment 4.1%.",
            sources=["CPI came in at 2.7 percent", "UNRATE 4.1"])
        assert verdict.ok is True

    def test_a_reply_with_no_figures_is_always_fine(self):
        verdict = grounding.check("I couldn't find that, sorry.", sources=[])
        assert verdict.ok is True
        assert verdict.unverified == []


class TestTheRetryInstruction:
    def test_it_names_the_offending_figures(self):
        verdict = grounding.check("It is 4.55% today.", sources=[])
        note = grounding.retry_note(verdict, user_name="Lingaa")

        assert "4.55" in note
        assert "Lingaa" in note

    def test_it_permits_admitting_the_gap(self):
        note = grounding.retry_note(
            grounding.check("up 9.9%", sources=[]), user_name="Lingaa")
        assert "could not find them" in note
        assert "invented number is not" in note

    def test_a_clean_verdict_needs_no_retry(self):
        verdict = grounding.check("nothing numeric here", sources=[])
        assert verdict.ok is True


class TestTheCaveat:
    def test_it_admits_rather_than_deletes(self):
        """The reply may still be useful; the user is owed the knowledge that
        part of it is unsourced."""
        verdict = grounding.check("Tata Steel is at 999.99.", sources=[])
        text = grounding.caveat(verdict)

        assert "could not verify" in text
        assert "999.99" in text
        assert "check a live source" in text

    def test_nothing_is_appended_to_a_clean_reply(self):
        assert grounding.caveat(grounding.check("all good", sources=[])) == ""
