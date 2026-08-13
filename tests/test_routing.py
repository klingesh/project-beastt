"""Which skill answers, and why it matters.

HARDENING_LOG entry 3, from a live session:

    > any update on my bot
    I'm up to date (latest change: Merge pull request #25 from ...)

That is BEASTT's git revision. The question was about a trading bot.

Two causes were fixed rather than either alone: the pattern (a bare "any
updates?" matched the "any update" inside "any update on my bot") and the
registration order (MaintenanceSkill was checked first, though TradingBotSkill is
the more specific of the two). Either fix alone would have handled this phrase.
The pair handles the ones nobody thought of -- so both are tested here.
"""

from __future__ import annotations

import pytest

from beastt.skills.maintenance_skill import MaintenanceSkill
from beastt.skills.trading_skill import TradingBotSkill


class _DummyBrain:
    """Stands in for a real brain so no model is ever contacted."""

    def is_available(self):
        return True

    def reply(self, _messages, **_kwargs):
        raise AssertionError(
            "the model was asked to answer a message a skill should have claimed")

    def stream(self, messages, **kwargs):
        yield self.reply(messages, **kwargs)


@pytest.fixture
def assistant_for():
    """Build a real Assistant with a dummy brain, so registration order is the
    genuine article rather than a copy of it."""
    from beastt.assistant import Assistant

    def _build(config):
        return Assistant(config=config, brain=_DummyBrain(), verbose=False)

    return _build


# --- the reported bug -------------------------------------------------------
class TestTheReportedBug:
    def test_maintenance_no_longer_claims_a_question_about_the_bot(self, config):
        """"any updates?" alone asks about BEASTT. "any update ON something"
        asks about the something."""
        assert MaintenanceSkill(config).matches("any update on my bot") is False

    def test_trading_claims_it(self, config):
        assert TradingBotSkill(config).matches("any update on my bot") is True

    def test_the_assistant_routes_it_to_the_trading_skill(self, config,
                                                          assistant_for):
        assistant = assistant_for(config)

        claimed = [s.name for s in assistant.skills
                   if s.matches("any update on my bot")]

        assert claimed and claimed[0] == "trading-bot"

    @pytest.mark.parametrize("preposition", [
        "on", "for", "about", "to", "regarding", "from", "with",
    ])
    def test_every_preposition_in_the_lookahead(self, config, preposition):
        text = f"any update {preposition} my bot"
        assert MaintenanceSkill(config).matches(text) is False


class TestRegistrationOrder:
    """Specific before general. The more specific skill must be checked first."""

    def test_trading_is_checked_before_maintenance(self, config, assistant_for):
        names = [s.name for s in assistant_for(config).skills]
        assert names.index("trading-bot") < names.index("maintenance")

    def test_the_trading_skill_is_absent_without_a_bot(self, no_bot,
                                                       assistant_for):
        names = [s.name for s in assistant_for(no_bot).skills]
        assert "trading-bot" not in names
        assert "maintenance" in names

    def test_every_skill_has_a_distinct_name(self, config, assistant_for):
        names = [s.name for s in assistant_for(config).skills]
        assert len(names) == len(set(names))


# --- BEASTT's own update questions still work -------------------------------
class TestMaintenanceStillWorks:
    """The log notes this explicitly: confirm that with no bot configured the
    update questions behave exactly as before."""

    OWN_UPDATE_QUESTIONS = [
        "any updates?",
        "any update",
        "are you up to date",
        "check for updates",
        "is there an update",
        "new version",
    ]

    @pytest.mark.parametrize("text", OWN_UPDATE_QUESTIONS)
    def test_maintenance_claims_questions_about_itself(self, config, text):
        assert MaintenanceSkill(config).matches(text) is True

    @pytest.mark.parametrize("text", OWN_UPDATE_QUESTIONS)
    def test_the_trading_skill_leaves_them_alone(self, config, text):
        assert TradingBotSkill(config).matches(text) is False

    @pytest.mark.parametrize("text", OWN_UPDATE_QUESTIONS)
    def test_unchanged_when_no_bot_is_configured(self, no_bot, text):
        assert MaintenanceSkill(no_bot).matches(text) is True

    @pytest.mark.parametrize("text", OWN_UPDATE_QUESTIONS)
    def test_the_assistant_routes_them_to_maintenance(self, config,
                                                      assistant_for, text):
        assistant = assistant_for(config)
        claimed = [s.name for s in assistant.skills if s.matches(text)]
        assert claimed and claimed[0] == "maintenance"


# --- the two incidental findings in the same file --------------------------
class TestNaturalPhrasings:
    """Both reached the model instead of the skill, which is the quiet kind of
    failure: a plausible answer to a question that was never routed."""

    @pytest.mark.parametrize("text", [
        "run a diagnostic",      # failed on the article
        "run diagnostics",       # worked before
        "run a diagnostics",
        "health check",
        "check yourself",
        "diagnose",
        "self check",
        "are you ok",
        "is everything working",
    ])
    def test_diagnosis_phrasings(self, config, text):
        assert MaintenanceSkill(config).matches(text) is True

    @pytest.mark.parametrize("text", [
        "what's wrong",          # the present tense is what someone types when
        "whats wrong",           # something is wrong *now* -- and it was missing
        "what is wrong",
        "what went wrong",
        "show me your errors",
        "any errors?",
        "last error",
        "error log",
    ])
    def test_error_phrasings(self, config, text):
        assert MaintenanceSkill(config).matches(text) is True

    @pytest.mark.parametrize("text", [
        "fix yourself", "fix it", "repair yourself", "fix any problems",
        "sort out the errors", "fix",
    ])
    def test_repair_phrasings(self, config, text):
        assert MaintenanceSkill(config).matches(text) is True

    @pytest.mark.parametrize("text", [
        "update yourself", "upgrade yourself", "update everything", "update now",
        "update",
    ])
    def test_update_phrasings(self, config, text):
        assert MaintenanceSkill(config).matches(text) is True

    def test_rollback_needs_to_say_what_it_is_rolling_back(self, config):
        """Bare "undo" is far too broad to claim a turn."""
        assert MaintenanceSkill(config).matches("roll back the last update") is True
        assert MaintenanceSkill(config).matches("revert to the previous version") is True
        assert MaintenanceSkill(config).matches("undo") is False


class TestOrdinaryChatIsLeftAlone:
    """Neither skill may claim a message that is simply conversation."""

    @pytest.mark.parametrize("text", [
        "hello",
        "what's the weather like",
        "tell me a joke",
        "most of the time I work from home",
        "explain how promises work in javascript",
        "what did you think of the film",
        "I need to update my CV",
        "the robot in that film was called Marvin",
    ])
    def test_neither_skill_claims_it(self, config, text):
        assert MaintenanceSkill(config).matches(text) is False
        assert TradingBotSkill(config).matches(text) is False

    def test_a_pasted_document_mentioning_a_bot_is_not_a_question(self, config):
        pasted = (
            "Project retrospective. " + "We reviewed the sprint. " * 40
            + "One action item: check on the bot before the next release."
        )
        assert len(pasted) > 400
        assert TradingBotSkill(config).matches(pasted) is False
