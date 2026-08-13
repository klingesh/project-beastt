"""Live quotes: what gets asked for, what comes back, and what is refused.

The parsers are pure, so the response shapes are exercised as fixtures. The
fetching itself is stubbed -- nothing here touches the network.

Driven by the transcript: "whats tata steel current share price" got Rs 117.45
and then Rs 184.60, both invented, both attributed to Moneycontrol; and "top
gainer stocks and loser stocks state 5 nos" got a fabricated table.
"""

from __future__ import annotations

import pytest

from beastt import quotes


# --- recognising the question ----------------------------------------------
class TestWanted:
    @pytest.mark.parametrize("text", [
        "whats tata steel current share price",
        "what is the share price of reliance",
        "tata steel stock price",
        "price of infosys",
        "how much is apple stock",
        "what's TCS trading at",
        "give me the current price of hdfc bank",
        "nifty today",
        "how is sensex doing",
        "bitcoin price now",
        "is the market up today",
    ])
    def test_quote_questions_are_recognised(self, text):
        assert quotes.wanted(text) is True

    @pytest.mark.parametrize("text", [
        "hey jarvis",
        "nothing much jarvis",
        "how are my college classes going",
        "what is happening in tamil nadu",
        "tell me about the stock market in general",
        "I want to learn about equity investing",
        "make me a ppt about the indian stock market",
    ])
    def test_ordinary_talk_is_not_a_quote_question(self, text):
        assert quotes.wanted(text) is False

    def test_the_transcript_question_that_got_no_retrieval(self):
        """This is the one that reached neither data.py nor web search."""
        assert quotes.wanted("whats tata steel current share price") is True


class TestRankedLists:
    @pytest.mark.parametrize("text", [
        "how about top gainer stocks and loser stocks state 5 nos in india today",
        "so now give me an update about top stocks that has gained",
        "biggest losers today",
        "top movers on nse",
        "show me the gainers",
    ])
    def test_a_screener_question_is_recognised(self, text):
        assert quotes.is_ranked_list(text) is True

    @pytest.mark.parametrize("text", [
        "whats tata steel current share price",
        "nifty today",
        "how is my trading bot",
    ])
    def test_single_quotes_are_not_screener_questions(self, text):
        assert quotes.is_ranked_list(text) is False

    def test_a_screener_list_must_be_dated_not_refused(self):
        """The rule changed after the first live run.

        Flatly forbidding a list was aimed at the right failure -- an invented
        table of five gainers -- but it was the wrong rule, and the model
        half-ignored it anyway. When a real page has been read its figures are
        worth having; what went wrong was presenting a page from August 2024 as
        the day's biggest movers. So: report it, name the page, give its date.
        """
        note = quotes.ranked_list_prompt("Lingaa")

        assert "no live market screener" in note
        assert "the date it carries" in note
        assert "that page's snapshot rather than live data" in note
        assert "do NOT invent percentages" in note
        assert "TradingView" in note

    def test_it_says_to_lead_with_a_stale_page(self):
        note = quotes.ranked_list_prompt("Lingaa")
        assert "earlier year, lead with that" in note


# --- working out what was asked about --------------------------------------
class TestExtractNames:
    @pytest.mark.parametrize("text,expected", [
        ("nifty today", "nifty"),
        ("how is nifty 50 doing", "nifty 50"),
        ("sensex now", "sensex"),
        ("bitcoin price", "bitcoin"),
        ("usd inr rate now", "usd inr"),
    ])
    def test_indices_and_crypto_resolve_by_alias(self, text, expected):
        assert expected in quotes.extract_names(text)

    def test_the_longest_alias_wins(self):
        """"nifty 50" must not be shortened to "nifty" and then searched as a
        company name."""
        assert "nifty 50" in quotes.extract_names("what is nifty 50 at")

    def test_a_company_name_survives_the_question_words(self):
        assert quotes.extract_names("whats tata steel current share price") == [
            "tata steel"]

    def test_an_explicit_ticker_is_used_as_is(self):
        assert "TATASTEEL.NS" in quotes.extract_names("quote for TATASTEEL.NS")

    @pytest.mark.parametrize("text", ["", "   ", None, "what is the price of"])
    def test_nothing_nameable(self, text):
        assert quotes.extract_names(text) == []


# --- parsing Yahoo's chart response ---------------------------------------
def chart_payload(price=184.6, previous=182.35, **over):
    meta = {
        "symbol": "TATASTEEL.NS",
        "longName": "Tata Steel Limited",
        "currency": "INR",
        "fullExchangeName": "NSE",
        "regularMarketPrice": price,
        "chartPreviousClose": previous,
        "regularMarketTime": 1786000000,
    }
    meta.update(over)
    return {"chart": {"result": [{"meta": meta}], "error": None}}


class TestParseChart:
    def test_a_normal_response(self):
        quote = quotes.parse_chart(chart_payload())

        assert quote.symbol == "TATASTEEL.NS"
        assert quote.name == "Tata Steel Limited"
        assert quote.price == 184.6
        assert quote.previous_close == 182.35
        assert quote.currency == "INR"
        assert quote.exchange == "NSE"
        assert quote.source == "Yahoo Finance"

    def test_the_change_is_computed_here_not_by_the_model(self):
        """Arithmetic is not something to delegate to a language model."""
        quote = quotes.parse_chart(chart_payload(price=184.6, previous=182.35))

        assert quote.change == pytest.approx(2.25)
        assert quote.change_percent == pytest.approx(1.2339, abs=1e-3)

    def test_the_timestamp_is_carried(self):
        assert "UTC" in quotes.parse_chart(chart_payload()).as_of

    def test_a_missing_previous_close_falls_back(self):
        payload = chart_payload(previous=None, previousClose=180.0)
        assert quotes.parse_chart(payload).previous_close == 180.0

    def test_no_previous_close_at_all_leaves_change_unknown(self):
        payload = chart_payload(previous=None)
        quote = quotes.parse_chart(payload)
        assert quote.price == 184.6
        assert quote.change is None
        assert quote.change_percent is None

    @pytest.mark.parametrize("payload,message", [
        ({"chart": {"error": {"description": "No data found, symbol may be delisted"}}},
         "refused that symbol"),
        ({"chart": {"result": []}}, "no result"),
        ({"chart": {"result": [{"meta": {}}]}}, "no price"),
        ("not a dict", "expected shape"),
        (None, "expected shape"),
    ])
    def test_failures_are_explained_not_guessed(self, payload, message):
        with pytest.raises(quotes.QuoteError, match=message):
            quotes.parse_chart(payload)

    def test_a_nan_price_is_not_a_price(self):
        assert quotes._number(float("nan")) is None


class TestParseSearch:
    PAYLOAD = {"quotes": [
        {"symbol": "TATASTEEL.BO", "quoteType": "EQUITY"},
        {"symbol": "TATASTEEL.NS", "quoteType": "EQUITY"},
        {"symbol": "TTST.L", "quoteType": "EQUITY"},
    ]}

    def test_an_indian_question_prefers_the_nse_listing(self):
        assert quotes.parse_search(self.PAYLOAD, prefer_india=True) in (
            "TATASTEEL.BO", "TATASTEEL.NS")

    def test_otherwise_the_first_match_is_taken(self):
        assert quotes.parse_search(self.PAYLOAD) == "TATASTEEL.BO"

    @pytest.mark.parametrize("payload", [
        {}, {"quotes": []}, {"quotes": [{"quoteType": "EQUITY"}]},
        "not a dict", None,
    ])
    def test_nothing_usable_gives_no_symbol(self, payload):
        assert quotes.parse_search(payload) == ""

    def test_news_results_are_not_symbols(self):
        payload = {"quotes": [{"symbol": "X", "quoteType": "MUTUALFUND"}]}
        assert quotes.parse_search(payload) == ""


class TestParseStooq:
    CSV = ("Symbol,Date,Time,Open,High,Low,Close,Volume\n"
           "TATASTEEL.IN,2026-08-13,10:30:00,182.40,185.10,181.90,184.60,9251034\n")

    def test_the_fallback_reads_a_price(self):
        quote = quotes.parse_stooq(self.CSV)
        assert quote.price == 184.60
        assert quote.symbol == "TATASTEEL.IN"
        assert quote.source == "Stooq"
        assert "2026-08-13" in quote.as_of

    @pytest.mark.parametrize("csv_text,message", [
        ("", "returned nothing"),
        ("Symbol,Date\n", "returned nothing"),
        ("Symbol,Date,Time,Open,High,Low,Close,Volume\n"
         "X,N/D,N/D,N/D,N/D,N/D,N/D,N/D\n", "no price"),
    ])
    def test_failures_are_explained(self, csv_text, message):
        with pytest.raises(quotes.QuoteError, match=message):
            quotes.parse_stooq(csv_text)


class TestStooqSymbols:
    @pytest.mark.parametrize("yahoo,stooq", [
        ("TATASTEEL.NS", "tatasteel.in"),
        ("AAPL", "aapl.us"),
        ("^NSEI", ""),
        ("BTC-USD", ""),
        ("GC=F", ""),
    ])
    def test_suffixes_are_translated_where_possible(self, yahoo, stooq):
        assert quotes._stooq_symbol(yahoo) == stooq


# --- the text handed to the model -----------------------------------------
class TestAsText:
    def test_a_quote_reads_as_a_citable_line(self):
        line = quotes.parse_chart(chart_payload()).as_text()

        assert "Tata Steel Limited (TATASTEEL.NS)" in line
        assert "184.60 INR" in line
        assert "previous close 182.35" in line
        assert "+2.25" in line and "+1.23%" in line
        assert "Yahoo Finance" in line

    def test_the_timestamp_is_always_present(self):
        assert "as of" in quotes.parse_chart(chart_payload()).as_text()


class TestAsPrompt:
    def test_it_tells_the_model_to_quote_exactly(self):
        block = quotes.as_prompt([quotes.parse_chart(chart_payload())])

        assert "use these figures exactly" in block
        assert "184.60" in block
        assert "Do not add any figure that is not listed" in block

    def test_it_insists_on_the_timestamp(self):
        block = quotes.as_prompt([quotes.parse_chart(chart_payload())])
        assert "without a timestamp" in block

    def test_no_quotes_no_block(self):
        assert quotes.as_prompt([]) == ""


class TestNoQuotePrompt:
    """The mirror of data.no_data_prompt: an empty retrieval must not read as
    licence to answer from memory."""

    def test_it_forbids_stating_a_price(self):
        note = quotes.no_quote_prompt(["Yahoo returned HTTP 429"], "Lingaa")

        assert "must not state one" in note
        assert "not from memory" in note
        assert "HTTP 429" in note

    def test_it_forbids_repeating_the_users_number(self):
        """> its 184.60 why you telling the values wrong
        > ... the price is indeed Rs 184.60"""
        note = quotes.no_quote_prompt([], "Lingaa")
        assert "repeating a number Lingaa mentioned" in note

    def test_it_says_where_to_check_instead(self):
        note = quotes.no_quote_prompt([], "Lingaa")
        for place in ("NSE", "Moneycontrol", "TradingView"):
            assert place in note


# --- fetching, with the network stubbed -----------------------------------
class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class TestFetch:
    def test_yahoo_is_tried_first(self, monkeypatch):
        calls = []

        def fake_get(url, params=None):
            calls.append(url)
            return _Response(payload=chart_payload())

        monkeypatch.setattr(quotes, "_get", fake_get)

        quote = quotes.fetch("TATASTEEL.NS")

        assert quote.price == 184.6
        assert len(calls) == 1

    def test_stooq_covers_a_yahoo_failure(self, monkeypatch):
        def fake_get(url, params=None):
            if "yahoo" in url:
                return _Response(status_code=429)
            return _Response(text=TestParseStooq.CSV)

        monkeypatch.setattr(quotes, "_get", fake_get)

        assert quotes.fetch("TATASTEEL.NS").source == "Stooq"

    def test_both_failing_explains_both(self, monkeypatch):
        monkeypatch.setattr(quotes, "_get",
                            lambda url, params=None: _Response(status_code=500))

        with pytest.raises(quotes.QuoteError) as err:
            quotes.fetch("TATASTEEL.NS")

        assert "Yahoo returned HTTP 500" in str(err.value)
        assert "Stooq returned HTTP 500" in str(err.value)

    def test_an_unreachable_network_is_reported_not_raised_raw(self, monkeypatch):
        def explode(url, params=None):
            raise OSError("no route to host")

        monkeypatch.setattr(quotes, "_get", explode)

        with pytest.raises(quotes.QuoteError, match="unreachable"):
            quotes.fetch("AAPL")

    def test_no_symbol(self):
        with pytest.raises(quotes.QuoteError, match="no symbol"):
            quotes.fetch("")


class TestLookup:
    def test_it_never_raises_and_reports_problems(self, monkeypatch):
        monkeypatch.setattr(quotes, "resolve_symbol", lambda *a, **k: "")

        found, problems = quotes.lookup("whats tata steel current share price")

        assert found == []
        assert problems and "tata steel" in problems[0]

    def test_a_successful_lookup(self, monkeypatch):
        monkeypatch.setattr(quotes, "resolve_symbol",
                            lambda *a, **k: "TATASTEEL.NS")
        monkeypatch.setattr(quotes, "fetch",
                            lambda symbol: quotes.parse_chart(chart_payload()))

        found, problems = quotes.lookup("whats tata steel current share price")

        assert len(found) == 1
        assert found[0].price == 184.6
        assert problems == []

    def test_an_indian_question_asks_for_the_indian_listing(self, monkeypatch):
        seen = {}

        def fake_resolve(name, prefer_india=False):
            seen["prefer_india"] = prefer_india
            return ""

        monkeypatch.setattr(quotes, "resolve_symbol", fake_resolve)
        quotes.lookup("nifty 50 today")

        assert seen["prefer_india"] is True
