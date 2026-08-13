"""The line the monitor will not cross.

HARDENING_LOG entry 1: requests to *act* are matched and refused explicitly
rather than left to fall through to the model, which would answer as though it
might comply. A language model in the order path is a bad idea however good the
reporting around it gets.

The governing trade-off, quoted from the log: **a false refusal is harmless and a
missed one is not.** These tests are written to that priority -- which is also why
two of them are marked as known gaps rather than deleted: they are places where
the current patterns get the trade-off backwards.
"""

from __future__ import annotations

import pytest

from beastt.skills.intent import directive
from beastt.skills.trading_skill import _ACT, TradingBotSkill

#: Phrasings that must never reach the model.
ACTION_REQUESTS = [
    # The two gaps found while testing, named in the log.
    "open a buy on gold",              # no trading noun after the verb
    "sell 0.04 lots of brent",         # the gap pattern could not span a decimal
    # Worth noting: the log's own example above is caught twice over -- by the
    # widened gap AND by the "direction with a size" branch -- so it does not on
    # its own prove the gap fix is still in place. These do: the verb is outside
    # buy/sell/long/short, so only a gap that can span a decimal reaches the noun.
    "close 0.04 lots of brent",
    "exit 0.5 lots",
    "modify 1.5 lots",
    # Closing and opening positions.
    "close my brent position",
    "close all positions",
    "close the trade",
    "exit the position now",
    "open a sell on gbpusd",
    "place a buy order",
    "enter a long position",
    "liquidate my positions",
    # Sizing and modifying.
    "double the lots",
    "increase the position size",
    "move my stop loss",
    "modify the tp on brent",
    "cancel the order",
    "hedge the position",
    "reverse the trade",
    # Bare directions with a size.
    "buy 1.5",
    "sell 0.04",
    "short 2",
    # Controlling the process itself.
    "stop the bot",
    "restart the bot",
    "pause the trading bot",
    "kill the bot",
    "shut down the ea",
    "disable the algo",
]

#: Questions that must be answered, not refused.
REPORTING_QUESTIONS = [
    "how's my bot",
    "how is my bot doing",
    "is the bot alive",
    "bot status",
    "what's the status of the trading bot",
    "any update on my bot",
    "check on the bot",
    "any trades?",
    "how many trades are open",
    "show me the trades",
    "tell me about the bot",
]


class TestActionsAreRefused:
    @pytest.mark.parametrize("text", ACTION_REQUESTS)
    def test_every_action_request_is_recognised(self, text):
        assert directive(_ACT, text) is not None, (
            f"{text!r} would fall through to the model, which would answer as "
            "though it might comply")

    @pytest.mark.parametrize("text", ACTION_REQUESTS)
    def test_the_skill_claims_the_turn(self, config, text):
        """Claiming the turn is what keeps the model out of the order path."""
        assert TradingBotSkill(config).matches(text) is True

    @pytest.mark.parametrize("text", [
        "close my brent position", "open a buy on gold", "restart the bot",
    ])
    def test_the_refusal_says_what_it_will_not_do_and_why(self, config, text):
        reply = TradingBotSkill(config).run(text)

        assert "can't place, close or change trades" in reply
        assert "your decision" in reply
        # And it redirects to what it *can* do, so the turn is not a dead end.
        assert "how's my bot" in reply

    def test_the_refusal_never_sounds_like_agreement(self, config):
        """It may say "I'll tell you where it stands" -- that is the redirect.
        What it must never imply is that the trade itself is being handled."""
        reply = TradingBotSkill(config).run("close all positions").lower()
        for phrase in ("i'll close", "i will close", "i'll place", "i'll open",
                       "closing your", "order placed", "position closed",
                       "on it", "consider it done"):
            assert phrase not in reply

    def test_an_action_request_never_reaches_the_status_reader(self, config,
                                                              monkeypatch):
        """run() must return the refusal before any fetch is attempted."""
        import beastt.trading as trading_module

        def explode(*_args, **_kwargs):
            raise AssertionError("an action request must not trigger a fetch")

        monkeypatch.setattr(trading_module, "fetch_status", explode)

        assert "can't place" in TradingBotSkill(config).run("sell 0.04 lots of brent")

    def test_the_act_check_comes_first(self, config):
        """A message that is both a question and an order is refused, not
        answered -- a false refusal is the cheaper mistake."""
        text = "how's my bot, and close the brent position"
        assert directive(_ACT, text) is not None
        assert "can't place" in TradingBotSkill(config).run(text)


class TestReportingStillWorks:
    @pytest.mark.parametrize("text", REPORTING_QUESTIONS)
    def test_questions_are_not_mistaken_for_orders(self, text):
        assert directive(_ACT, text) is None, (
            f"{text!r} is a question and would be refused instead of answered")

    @pytest.mark.parametrize("text", REPORTING_QUESTIONS)
    def test_the_skill_claims_reporting_questions(self, config, text):
        assert TradingBotSkill(config).matches(text) is True


class TestOnlyWhenConfigured:
    """With no bot configured the skill must be invisible, so nothing about an
    install without one changes."""

    @pytest.mark.parametrize("text", ACTION_REQUESTS + REPORTING_QUESTIONS)
    def test_nothing_is_claimed(self, no_bot, text):
        assert TradingBotSkill(no_bot).matches(text) is False


class TestPastedTextIsNotAnOrder:
    """The intent.directive guard: a long document that happens to contain
    "close the position" is not someone asking."""

    def test_a_buried_order_in_a_long_document_is_ignored(self, config):
        pasted = (
            "Trading journal, week 32. " + "Notes on discipline. " * 40
            + "The rule I keep breaking is that I close the position too early."
        )
        assert len(pasted) > 400
        assert directive(_ACT, pasted) is None

    def test_but_a_short_message_is_taken_at_face_value(self, config):
        assert directive(_ACT, "close the position") is not None


# --- known gaps -------------------------------------------------------------
# Both of these get the log's own trade-off backwards. They are written as the
# behaviour that *should* hold and marked xfail(strict=True), so the suite stays
# green while the bug is documented -- and turns red the moment someone fixes it
# without removing the marker.
class TestKnownGaps:
    @pytest.mark.known_gap
    @pytest.mark.xfail(strict=True, reason=(
        "_ACT's first branch matches the verb 'open' reaching the noun "
        "'positions', so a plain reporting question -- 'what are my open "
        "positions' -- is answered with the refusal instead of the position "
        "list. This is the one place a false refusal is NOT harmless: it "
        "withholds the report the skill exists to give."))
    def test_asking_about_open_positions_should_be_answered(self, config):
        text = "what are my open positions"
        assert directive(_ACT, text) is None
        assert "can't place" not in TradingBotSkill(config).run(text)

    @pytest.mark.known_gap
    @pytest.mark.xfail(strict=True, reason=(
        "'reduce' and 'trim' are natural ways to say 'decrease', but are not in "
        "_ACT's verb list -- so 'reduce 2.5 lots of gold' reaches the model. By "
        "the module's own rule (a false refusal is harmless, a missed one is "
        "not) a missing action verb is the expensive direction to err in."))
    @pytest.mark.parametrize("text", [
        "reduce 2.5 lots of gold",
        "trim the position",
    ])
    def test_synonyms_for_resizing_should_be_refused(self, text):
        assert directive(_ACT, text) is not None

    @pytest.mark.known_gap
    @pytest.mark.xfail(strict=True, reason=(
        "The skill's docstring says it declines to opine on positions, because "
        "that would be investment advice. But 'should I close brent?' names no "
        "trading noun and no bot word, so neither _ACT nor _ASK matches, the "
        "skill does not claim the turn, and the model answers it -- giving "
        "exactly the advice the module says is out of scope."))
    @pytest.mark.parametrize("text", [
        "should I close brent?",
        "do you think I should sell gold",
        "is it a good time to buy",
    ])
    def test_requests_for_advice_should_not_reach_the_model(self, config, text):
        assert TradingBotSkill(config).matches(text) is True
        assert "can't place" in TradingBotSkill(config).run(text)
