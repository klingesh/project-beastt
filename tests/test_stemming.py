"""Two spellings of the same word have to meet, and unrelated ones must not.

The gap this closes: "where do I live" never matched "Lingesh lives in Chennai".
Recall is exact-token overlap, so the fact was present and simply never scored --
which from the user's side is indistinguishable from the assistant having
forgotten, and is the sort of thing a person notices once and then stops trusting.

The reason it stayed open as a recorded gap rather than being fixed casually is
that `_tokens` feeds three things, not one:

  * **recall**, where a missed match costs a fact,
  * **near-duplicate merging** at 0.8 overlap, where a *false* match silently
    destroys one of two distinct memories,
  * **forget()** at 0.5, where a false match deletes something nobody asked to
    lose.

So the collapsing cases are only half of this file. The other half is the words
that must stay apart.
"""

from __future__ import annotations

import pytest

from beastt.longterm import LongTermMemory, _tokens, stem


class TestFormsThatMustMeet:
    @pytest.mark.parametrize("first,second", [
        # Plain plurals, and the -s verb form.
        ("live", "lives"),
        ("laptop", "laptops"),
        ("friend", "friends"),
        ("price", "prices"),
        ("attend", "attends"),
        ("need", "needs"),
        ("bot", "bots"),
        # -ies and the sibilant plurals.
        ("study", "studies"),
        ("city", "cities"),
        ("class", "classes"),
        ("box", "boxes"),
        ("watch", "watches"),
        ("wish", "wishes"),
        # -ing and -ed, including the doubled and elided-e cases.
        ("attend", "attending"),
        ("work", "working"),
        ("trade", "trading"),
        ("move", "moved"),
        ("hope", "hoped"),
        ("live", "lived"),
        ("run", "running"),
        ("hop", "hopped"),
        ("play", "played"),
        ("ask", "asked"),
        # -y verbs, which lose the y before -ed.
        ("study", "studied"),
        ("carry", "carried"),
        ("apply", "applied"),
        ("try", "tried"),
        ("engineer", "engineering"),
    ])
    def test_the_same_word_reaches_the_same_key(self, first, second):
        assert stem(first) == stem(second), (
            f"{first!r} and {second!r} are the same word and must meet")

    def test_stemming_is_idempotent(self):
        """Applying it twice must not keep eating the word."""
        for word in ["lives", "classes", "studies", "running", "hoped", "cities",
                     "attending", "engineering", "trading"]:
            once = stem(word)
            assert stem(once) == once, f"{word!r} -> {once!r} -> {stem(once)!r}"


class TestFormsThatMustStayApart:
    """A false match here is worse than a missed one: it merges two memories."""

    @pytest.mark.parametrize("first,second", [
        ("live", "liver"),
        ("class", "clash"),
        ("bot", "both"),
        ("price", "prick"),
        ("study", "student"),
        ("run", "rune"),
        ("cot", "cats"),
        ("hope", "hop"),
        ("brand", "brandy"),
        ("chennai", "chennam"),
    ])
    def test_different_words_keep_different_keys(self, first, second):
        assert stem(first) != stem(second), (
            f"{first!r} and {second!r} collapsed together, which would let one "
            "memory overwrite the other")

    @pytest.mark.parametrize("word", [
        # Words ending in "s" that are not plurals. Stripping it would fuse
        # "analysis" with "analysi", and worse, match unrelated facts.
        "analysis", "diagnosis", "thesis", "basis", "crisis", "series",
        "species", "news", "physics", "business", "address", "access",
        "progress", "gas", "bus", "class", "glass", "chess", "boss",
        "always", "perhaps", "sometimes",
    ])
    def test_words_that_only_look_plural_are_left_alone(self, word):
        assert stem(word) == word

    @pytest.mark.parametrize("word", [
        "lingaa", "chennai", "coimbatore", "prahadhesvaryaa", "moneycontrol",
        "tradingview", "jarvis", "beastt", "nifty", "sensex",
    ])
    def test_names_are_not_mangled(self, word):
        """A name is the one thing recall most needs to match exactly."""
        assert stem(word) == word

    @pytest.mark.parametrize("word", [
        # Three-letter words ending in "s" that the -s rule would happily
        # shorten into something meaningless. The length guard is the only thing
        # standing between "gps" and "gp".
        "gps", "sms", "abs", "ops", "ups", "ips", "cms",
        # And the ordinary short words, which no rule should reach.
        "cat", "his", "was", "the", "and", "bot", "run",
    ])
    def test_short_words_are_never_touched(self, word):
        assert stem(word) == word

    @pytest.mark.parametrize("word", ["", None])
    def test_empty_input(self, word):
        assert stem(word) == ""


class TestTokens:
    def test_a_sentence_collapses_to_stems(self):
        assert _tokens("Lingesh lives in Chennai") == {"lingesh", "live",
                                                       "chennai"}

    def test_the_question_and_the_fact_meet(self):
        assert _tokens("where do I live") & _tokens("Lingesh lives in Chennai")

    def test_stopwords_are_still_dropped(self):
        assert _tokens("I am at the coffee shop") == {"coffee", "shop"}


class TestRecall:
    @pytest.fixture
    def memory(self, tmp_path):
        store = LongTermMemory(path=str(tmp_path / "m.json"),
                               user_name="Lingaa")
        store.add("Lingaa lives in Chennai")
        store.add("Lingaa studies engineering at a college in Coimbatore")
        store.add("Lingaa runs an automated trading bot on a Windows VPS")
        store.add("Lingaa has two laptops, one with an RTX 3050")
        return store

    @pytest.mark.parametrize("question,expected", [
        ("where do I live", "Chennai"),
        ("which city am I living in", "Chennai"),
        ("what do I study", "engineering"),
        ("what am I studying", "engineering"),
        ("tell me about my college", "engineering"),
        ("how is my trading bot", "trading bot"),
        ("do my bots run ok", "trading bot"),
        ("what laptop do I have", "laptops"),
        ("my laptops", "laptops"),
    ])
    def test_questions_reach_the_fact_however_they_are_phrased(self, memory,
                                                              question,
                                                              expected):
        recalled = " ".join(memory.relevant(question))
        assert expected in recalled, f"{question!r} recalled: {recalled!r}"

    def test_an_unrelated_question_still_recalls_nothing(self, memory):
        """Stemming widens matching, so this is the guard that it has not been
        widened into the padding this whole design removed."""
        assert memory.relevant("what is the capital of Peru") == []

    def test_a_greeting_still_recalls_nothing(self, memory):
        assert memory.relevant("nothing much jarvis") == []


class TestMergingIsNotMadeReckless:
    """The 0.8 near-duplicate threshold now compares stems, so it merges more
    readily. These are the facts that must survive as two."""

    @pytest.fixture
    def memory(self, tmp_path):
        return LongTermMemory(path=str(tmp_path / "m.json"), user_name="Lingaa")

    @pytest.mark.parametrize("first,second", [
        ("Lingaa lives in Chennai", "Lingaa studies in Coimbatore"),
        ("Prahadhesvaryaa is Lingaa's close friend", "Ravi is Lingaa's cousin"),
        ("Lingaa has an RTX 3050 laptop", "Lingaa has a Windows VPS"),
        ("Lingaa likes filter coffee", "Lingaa dislikes instant coffee"),
    ])
    def test_distinct_facts_are_still_two_facts(self, memory, first, second):
        memory.add(first)
        memory.add(second)
        assert len(memory) == 2, f"{first!r} and {second!r} were merged"

    def test_a_genuine_restatement_still_merges(self, memory):
        memory.add("Lingaa has two laptops")
        memory.add("Lingaa has two laptop")
        assert len(memory) == 1

    def test_a_plural_restatement_merges(self, memory):
        """The point of stemming, on the write path: the same fact told twice in
        slightly different words should not be stored twice."""
        memory.add("Lingaa attends classes at college")
        memory.add("Lingaa attends class at college")
        assert len(memory) == 1


class TestForgetIsNotMadeReckless:
    """forget() matches at 0.5, so it is the most dangerous of the three."""

    @pytest.fixture
    def memory(self, tmp_path):
        store = LongTermMemory(path=str(tmp_path / "m.json"),
                               user_name="Lingaa")
        store.add("Prahadhesvaryaa K S is Lingaa's close friend")
        store.add("Lingaa lives in Chennai")
        store.add("Lingaa runs an automated trading bot")
        return store

    def test_forgetting_one_thing_leaves_the_rest(self, memory):
        removed = memory.forget("prahadhesvaryaa")

        assert removed == ["Prahadhesvaryaa K S is Lingaa's close friend"]
        assert len(memory) == 2

    def test_a_plural_query_still_finds_the_fact(self, memory):
        assert memory.forget("trading bots") != []

    def test_an_unrelated_query_removes_nothing(self, memory):
        assert memory.forget("the capital of Peru") == []
        assert len(memory) == 3
