"""A figure can be real, sourced, and still wrong: sourced is not current.

From the first live run of the research pipeline. Asked for today's top gainers,
the assistant read a Moneycontrol page, lifted five movers and their percentages
off it, and gave them as the day's biggest. Every figure checked out against the
retrieved page, so the grounding check passed it. The page was from **August
2024**.

Grounding proves a number came from a source. It does not prove the source was
current, and those are separate claims -- only the first is checkable by string
comparison. This covers the second.

Also here: the caveat that fired on a *good* answer. A news roundup written as
"Daily Thanthi (about 40 minutes ago) noted an eight-hour power outage" was
reported as containing the unverified figure "40" -- an interval the assistant had
worked out itself from the page's timestamp. There was nothing to verify.
"""

from __future__ import annotations

from datetime import date

import pytest

from beastt import grounding, quotes, search


# --- the caveat that fired on a good answer --------------------------------
class TestDatesAreNotClaims:
    #: The sentence from the reply that produced the wrong caveat.
    ROUNDUP = ("Daily Thanthi (about 40 minutes ago) noted an eight-hour power "
               "outage at a government hospital in Kanchipuram, while another "
               "story from the same outlet (just under an hour ago) said a young "
               "boy died after treatment.")

    def test_a_relative_timestamp_is_not_a_figure(self):
        assert grounding.figures(self.ROUNDUP) == []

    def test_the_reply_no_longer_earns_a_caveat(self):
        verdict = grounding.check(self.ROUNDUP, sources=["some reporting"])
        assert verdict.ok is True
        assert grounding.caveat(verdict) == ""

    @pytest.mark.parametrize("text", [
        "40 minutes ago", "55 minutes ago", "3 hours ago", "2 days ago",
        "6 months ago", "10 years old", "20 minutes earlier", "15 minutes back",
    ])
    def test_every_relative_form(self, text):
        assert grounding.figures(text) == []

    @pytest.mark.parametrize("text", [
        "as of 2026-08-13 10:23 UTC",
        "13/08/2026",
        "at 15:59",
        "at 15:59:07",
        "Aug 13, 2026",
        "13 August 2026",
    ])
    def test_dates_and_clock_times_are_masked(self, text):
        """These passed before only because the quote block happened to contain
        the same digits. A reply that reformatted the timestamp would have been
        flagged for it."""
        assert grounding.figures(text) == []

    def test_a_price_beside_a_timestamp_is_still_checked(self):
        """The masking must not swallow the figure that matters."""
        assert grounding.figures(
            "the price is 184.90 INR as of 2026-08-13 10:23 UTC") == ["184.90"]

    def test_a_duration_that_is_a_claim_is_still_checked(self):
        """"40 minutes ago" is narration. "lasted 40 days" is an assertion."""
        assert "40" in grounding.figures("the strike lasted 40 days")

    def test_the_real_quote_reply_from_the_session_passes(self):
        """> the current share price of Tata Steel is 184.90 INR on the BSE, as of
        2026-08-13 10:23 UTC, with a change of -0.55 (-0.30%) from the previous
        close of 185.45."""
        reply = ("Lingaa, the current share price of Tata Steel is 184.90 INR on "
                 "the BSE, as of 2026-08-13 10:23 UTC, with a change of -0.55 "
                 "(-0.30%) from the previous close of 185.45.")
        source = ("Tata Steel (TATASTEEL.BO): 184.90 INR, previous close 185.45, "
                  "change -0.55 (-0.30%), exchange BSE, as of 2026-08-13 10:23 UTC")

        assert grounding.check(reply, sources=[source]).ok is True


# --- spotting a page that is older than the question ----------------------
TODAY = date(2026, 8, 13)


class TestStaleHint:
    def test_the_moneycontrol_page_that_caused_this(self):
        """The page said "Aug 13 2024, 15:59" and was read as today's."""
        page = ("Top Gainers — Moneycontrol. As on Aug 13 2024, 15:59. "
                "Astral Ltd 8.74%, Solar Industries India 8.51%.")

        hint = search.stale_hint(page, today=TODAY)

        assert "2024" in hint
        assert "not 2026" in hint

    def test_a_current_page_gets_no_hint(self):
        page = "Top Gainers as on 13 Aug 2026, 15:59. Astral Ltd 8.74%."
        assert search.stale_hint(page, today=TODAY) == ""

    def test_a_page_mentioning_both_years_is_treated_as_current(self):
        """An article written now about last year is not itself stale."""
        page = "In 2026 the index recovered the ground it lost during 2024."
        assert search.stale_hint(page, today=TODAY) == ""

    def test_an_undated_page_is_not_accused(self):
        assert search.stale_hint("Top gainers today: Astral up 8.74%.",
                                 today=TODAY) == ""

    @pytest.mark.parametrize("text", ["", None, "no numbers here at all"])
    def test_nothing_to_judge(self, text):
        assert search.stale_hint(text, today=TODAY) == ""

    def test_the_newest_year_present_is_the_one_reported(self):
        page = "Founded 2011. Archive covers 2019 through 2023."
        assert "2023" in search.stale_hint(page, today=TODAY)

    def test_a_future_year_is_ignored(self):
        """A page discussing 2027 targets is not from 2027."""
        page = "Analysts see the target met by 2027. Filed 2024."
        assert "2024" in search.stale_hint(page, today=TODAY)

    def test_early_january_is_forgiving(self):
        """On 3 January, a page dated last month is not worth flagging."""
        page = "Published December 2025."
        assert search.stale_hint(page, today=date(2026, 1, 3)) == ""
        assert search.stale_hint(page, today=date(2026, 6, 1)) != ""


class TestTheHintReachesTheModel:
    def _source(self, body, hint=""):
        return search.Source(title="Moneycontrol", url="https://moneycontrol.com/x",
                             snippet="snip", body=body, age_hint=hint)

    def test_it_is_printed_beside_the_page(self):
        block = self._source("...", "page appears to be from 2024, not 2026").as_block()
        assert "full page; page appears to be from 2024" in block

    def test_a_current_page_says_nothing_extra(self):
        assert self._source("...").as_block().count(";") == 0

    def test_the_instruction_requires_the_date(self):
        findings = search.Findings(
            question="top gainers in india today", kind=search.QUOTE,
            queries=["q"],
            sources=[self._source("Astral 8.74%",
                                  "page appears to be from 2024, not 2026")])

        block = search.format_findings(findings, "Lingaa")

        assert "appears to be from 2024" in block
        assert "give that date beside any figure" in block
        assert "Never present a dated figure as current" in block

    @pytest.mark.parametrize("kind", [search.NEWS, search.PRODUCT, search.QUOTE,
                                      search.FACTUAL])
    def test_every_kind_of_answer_carries_the_rule(self, kind):
        findings = search.Findings(question="q", kind=kind, queries=["q"],
                                   sources=[self._source("body")])
        assert "Never present a dated figure as current" in (
            search.format_findings(findings, "Lingaa"))


class TestGatherMarksStalePages:
    class _Stub(search.WebSearch):
        def __init__(self, pages):
            self.timeout = 1
            self._DDGS = None
            self._pages = pages

        def search(self, query, max_results=5):
            return [{"title": "Moneycontrol", "url": "https://moneycontrol.com/x",
                     "body": "snippet"}]

        def news(self, query, max_results=5):
            return self.search(query, max_results)

        def fetch_page(self, url, limit=6000):
            return self._pages.get(url, "")

    def test_a_stale_page_is_flagged(self):
        stub = self._Stub({"https://moneycontrol.com/x":
                           "Top Gainers as on Aug 13 2024. Astral 8.74%."})

        findings = stub.gather("top gainers in india today", read_pages=1)

        assert findings.sources[0].age_hint
        assert "2024" in findings.sources[0].age_hint

    def test_a_current_page_is_not_flagged(self):
        from datetime import date as _date

        stub = self._Stub({"https://moneycontrol.com/x":
                           f"Top Gainers as on {_date.today().year}. Astral 8.74%."})

        findings = stub.gather("top gainers in india today", read_pages=1)

        assert findings.sources[0].age_hint == ""

    def test_a_dated_snippet_is_flagged_even_when_the_page_will_not_load(self):
        """Snippets were exempted at first, on the grounds that they are too
        short to carry a dateline. That was wrong -- search engines routinely put
        the date at the front of one, and when the page itself will not load the
        snippet is the only place the age is visible at all.
        """
        class _Dated(self._Stub):
            def search(self, query, max_results=5):
                return [{"title": "Moneycontrol",
                         "url": "https://moneycontrol.com/x",
                         "body": "Aug 13, 2024 — Top Gainers: Astral Ltd 8.74%"}]

        findings = _Dated({}).gather("top gainers in india today", read_pages=1)

        assert findings.sources[0].read is False
        assert "2024" in findings.sources[0].age_hint

    def test_an_undated_snippet_is_not_accused(self):
        stub = self._Stub({})

        findings = stub.gather("top gainers in india today", read_pages=1)

        assert findings.sources[0].read is False
        assert findings.sources[0].age_hint == ""

    def test_the_staleness_is_reported_as_progress(self):
        steps = []
        stub = self._Stub({"https://moneycontrol.com/x": "As on Aug 13 2024."})

        stub.gather("top gainers today", read_pages=1, on_step=steps.append)

        assert any("2024" in s for s in steps)


class TestTheMoversAnswer:
    """End to end: the answer that was real, sourced, and two years old."""

    def test_a_dated_page_is_grounded_but_must_be_labelled(self):
        """Grounding passes -- correctly. The date requirement is what stops it
        being presented as today's."""
        reply = ("Astral Ltd up 8.74%, Solar Industries India up 8.51%, Netweb "
                 "Technologies India up 7.10%.")
        page = ("Top Gainers — Moneycontrol, as on Aug 13 2024, 15:59. Astral "
                "Ltd 8.74%. Solar Industries India 8.51%. Netweb Technologies "
                "India 7.10%.")

        assert grounding.check(reply, sources=[page]).ok is True
        assert search.stale_hint(page, today=TODAY) != ""

    def test_the_second_reply_from_the_session_is_the_target_behaviour(self):
        """> the Moneycontrol snapshot (Aug 13 2024, 15:59) highlights a few of
        the biggest movers: Astral Ltd up 8.74% ...

        Named page, its date given, described as a snapshot. That is the answer
        the instruction is now asking for, so it must pass cleanly."""
        reply = ("I don't have a live screener, but the Moneycontrol snapshot "
                 "(Aug 13 2024, 15:59) shows Astral Ltd up 8.74% and Solar "
                 "Industries India up 8.51%.")
        page = ("Moneycontrol Top Gainers as on Aug 13 2024, 15:59. Astral Ltd "
                "8.74%. Solar Industries India 8.51%.")

        verdict = grounding.check(reply, sources=[page])

        assert verdict.ok is True
        assert grounding.caveat(verdict) == ""

    def test_invented_percentages_are_still_caught(self):
        """Relaxing the refusal must not reopen the original hole."""
        reply = "Adani Enterprises up 4.55%, Vedanta up 3.65%."

        verdict = grounding.check(reply, sources=["a page with no movers on it"])

        assert verdict.ok is False
        assert "4.55" in verdict.invented
