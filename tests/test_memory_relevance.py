"""A remembered friend must be recalled when she comes up, and only then.

The reported failure, from two live sessions:

    > nothing much jarvis
    Sometimes doing nothing can be nice too, Lingaa. ... How's Prahathi doing,
    by the way? Haven't heard about her in a while.

    > so whats happening in tamilnadu, india
    [news summary] ... How's your friend Prahadhesvaryaa doing, by the way?

    > the values are wrong. can you stop hallucinating
    I couldn't find a direct connection between the information about you,
    possibly referring to the 2014 Indian film starring Rajinikanth, and
    Prahadhesvaryaa K S to the complaint about incorrect values.

Entry 6 of the hardening log removed the padding that used to cause this, but
every fact marked `core` still went through unconditionally -- and `core` is set
by exactly one thing: the user saying "remember that ...". So being asked once to
remember a friend put her in every prompt for good, and the third reply shows the
same facts reaching the research planner, which went off and researched the user's
own nickname.

The requirement is not to forget her. It is to bring her up when she is the
subject and stay quiet when she is not.
"""

from __future__ import annotations

import pytest

from beastt.longterm import MAX_IDENTITY, LongTermMemory, is_identity

USER = "Lingaa"


@pytest.fixture
def memory(tmp_path):
    """The store as it stood when the failure was reported."""
    store = LongTermMemory(path=str(tmp_path / "memory.json"), user_name=USER)
    # What MemorySkill writes when the user says "remember that ...".
    store.add("Lingaa likes to be called Lingaa", core=True)
    store.add("Prahadhesvaryaa K S is Lingaa's close friend", core=True)
    store.add("Lingaa studies engineering at a college in Coimbatore")
    store.add("Lingaa runs an automated trading bot on a Windows VPS")
    return store


#: The turns from the transcript that must not mention her.
UNRELATED_TURNS = [
    "nothing much jarvis",
    "hey jarvis",
    "Hello.",
    "umm lots going on for now i am at college attending my classes",
    "can you get me an update on what is happening around the world",
    "so whats happening in tamilnadu, india",
    "any details regarding to drugs i heard a news in coimbatore",
    "how about top gainer stocks and loser stocks state 5 nos in india today",
    "whats tata steel current share price",
    "the values are wrong. can you please stop ai hallucinating",
    "yeah jarvis its sad so any update on the world news related to cpi",
    "fix yourself",
]


class TestSheIsNotVolunteered:
    @pytest.mark.parametrize("turn", UNRELATED_TURNS)
    def test_the_friend_is_not_recalled_out_of_nowhere(self, memory, turn):
        recalled = memory.relevant(turn)
        assert not any("Prahadhesvaryaa" in fact for fact in recalled), (
            f"{turn!r} would arrive carrying a fact about a friend, and the model "
            "handed a name finds a use for it")

    @pytest.mark.parametrize("turn", UNRELATED_TURNS)
    def test_only_identity_arrives_unprompted(self, memory, turn):
        """What is left is what should always be there: what to call him."""
        recalled = memory.relevant(turn)
        for fact in recalled:
            assert is_identity(fact, USER) or _shares_a_word(fact, turn), (
                f"{fact!r} is neither identity nor relevant to {turn!r}")

    def test_a_bare_greeting_carries_only_his_name(self, memory):
        assert memory.relevant("Hello.") == ["Lingaa likes to be called Lingaa"]


class TestSheIsStillRemembered:
    """The point is not to forget her."""

    def test_she_is_still_in_the_store(self, memory):
        assert any("Prahadhesvaryaa" in fact for fact in memory.all_texts())

    @pytest.mark.parametrize("turn", [
        "how is prahadhesvaryaa doing",
        "did I tell you about Prahadhesvaryaa",
        "what do you know about prahadhesvaryaa k s",
        "prahadhesvaryaa messaged me today",
    ])
    def test_she_is_recalled_the_moment_she_comes_up(self, memory, turn):
        recalled = memory.relevant(turn)
        assert any("Prahadhesvaryaa" in fact for fact in recalled), (
            f"{turn!r} is about her and should recall what is known")

    def test_asking_about_friends_recalls_her(self, memory):
        recalled = memory.relevant("who are my close friends")
        assert any("Prahadhesvaryaa" in fact for fact in recalled)

    def test_what_do_you_remember_is_unaffected(self, memory):
        """MemorySkill answers that from all_texts() and never comes through
        relevance, so everything is still listable on request."""
        assert len(memory.all_texts()) == 4

    def test_an_explicitly_remembered_fact_is_preferred_when_it_matches(self,
                                                                       memory):
        """`core` still counts for something -- it just has to match first."""
        recalled = memory.relevant("tell me about prahadhesvaryaa and coimbatore")
        assert any("Prahadhesvaryaa" in fact for fact in recalled)


class TestOtherFactsStillRecall:
    def test_college_comes_up_when_college_does(self, memory):
        assert any("Coimbatore" in f
                   for f in memory.relevant("how are my college classes going"))

    def test_the_bot_comes_up_when_the_bot_does(self, memory):
        assert any("trading bot" in f
                   for f in memory.relevant("how is my trading bot doing"))


class TestIsIdentity:
    @pytest.mark.parametrize("fact", [
        "Lingaa likes to be called Lingaa",
        "Lingaa prefers to be called Lingaa",
        "Lingaa wants to be called by his nickname",
        "Lingaa goes by Lingaa",
        "Lingaa is known as Lingaa to his friends",
        "Lingaa's name is Lingesh",
        "likes to be called Lingaa",
        "The user prefers the name Lingaa",
    ])
    def test_what_the_user_is_called(self, fact):
        assert is_identity(fact, USER) is True

    @pytest.mark.parametrize("fact", [
        # Somebody else's name is not the user's identity.
        "Prahadhesvaryaa likes to be called Praha",
        "Prahadhesvaryaa K S is known as Praha",
        "His name is Ravi",
        "Her name is Priya",
        # Nor is anything else about the user, however durable.
        "Prahadhesvaryaa K S is Lingaa's close friend",
        "Lingaa studies engineering at a college in Coimbatore",
        "Lingaa runs an automated trading bot on a Windows VPS",
        "Lingaa has an RTX 3050 laptop",
        "Lingaa is from Tamil Nadu",
        "Lingaa likes filter coffee",
    ])
    def test_everything_else_must_earn_its_place(self, fact):
        assert is_identity(fact, USER) is False

    @pytest.mark.parametrize("fact", ["", "   ", None])
    def test_empty_input(self, fact):
        assert is_identity(fact, USER) is False

    def test_the_users_name_matters(self):
        """The same sentence is identity for one user and gossip for another."""
        fact = "Prahadhesvaryaa likes to be called Praha"
        assert is_identity(fact, "Prahadhesvaryaa") is True
        assert is_identity(fact, "Lingaa") is False

    def test_case_and_possessives_are_tolerated(self):
        assert is_identity("LINGAA LIKES TO BE CALLED LINGAA", USER) is True
        assert is_identity("Lingaa's name is Lingesh Kumar", USER) is True


class TestTheAlwaysOnSetCannotGrowBack:
    def test_identity_facts_are_capped(self, tmp_path):
        """Otherwise the every-turn dossier reassembles itself one fact at a
        time, which is how the first version of this got out of hand."""
        store = LongTermMemory(path=str(tmp_path / "m.json"), user_name=USER)
        for n in range(10):
            store.add(f"Lingaa goes by nickname number {n} on platform {n}")

        assert len(store.identity_facts()) <= MAX_IDENTITY
        assert MAX_IDENTITY == 3

    def test_recall_stays_small_however_much_is_remembered(self, memory):
        for n in range(60):
            memory.add(f"Lingaa once mentioned topic {n} in passing somewhere")

        assert len(memory.relevant("nothing much jarvis")) <= MAX_IDENTITY

    def test_the_limit_is_still_respected(self, memory):
        memory.add("Lingaa likes filter coffee from a local shop")
        memory.add("Lingaa drinks coffee every morning before class")
        assert len(memory.relevant("coffee", limit=2)) <= 2


class TestThePlannerGetsNoMemory:
    """The third reply in the transcript: the research planner was handed the
    user's own facts as context and researched them."""

    def test_deliberate_context_carries_attachments_but_not_facts(self,
                                                                 monkeypatch,
                                                                 tmp_path,
                                                                 make_config):
        from dataclasses import replace

        from beastt import deliberate
        from beastt.assistant import Assistant
        from beastt.config import Config

        captured = {}

        def fake_work(_brain, request, **kwargs):
            captured.update(kwargs)
            captured["request"] = request
            yield {"type": "give_up"}

        monkeypatch.setattr(deliberate, "work", fake_work)

        config = make_config(
            user_name=USER, longterm_enabled=True,
            memory_path=str(tmp_path / "memory.json"),
            documents_enabled=False, code_enabled=False, imagegen_enabled=False,
            search_enabled=False, bot_status_repo="", data_enabled=False,
        )

        class _Brain:
            def is_available(self):
                return True

            def reply(self, _messages, **_kwargs):
                return "fine"

            def stream(self, _messages, **_kwargs):
                yield "fine"

        assistant = Assistant(config=config, brain=_Brain(), verbose=False)
        assistant.longterm.add("Prahadhesvaryaa K S is Lingaa's close friend",
                               core=True)
        assistant.set_attachments("the attached report text", ["report.pdf"])

        list(assistant._deliberate("check these share prices for me"))

        context = captured.get("context", "")
        assert "report.pdf" in context, "attachments are still handed over"
        assert "Prahadhesvaryaa" not in context, (
            "the planner was given a fact about a friend and will research it")
        assert "Things you remember" not in context


def _shares_a_word(fact: str, question: str) -> bool:
    from beastt.longterm import _tokens

    return bool(_tokens(fact) & _tokens(question))
