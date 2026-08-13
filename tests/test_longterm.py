"""Recall must be relevant, or silent.

HARDENING_LOG entry 6. Asked to generate an image, BEASTT recommended a
particular friend for artistic advice. Asked nothing at all -- just "Hello." --
it volunteered that friend's opinion of the user's mood. She had nothing to do
with either conversation.

The cause was a fallback at the end of `relevant()` that topped the list up to
the limit with the most recently updated facts. A genuine token match is rare and
the limit is eight, so that fallback fired on almost every turn: a question about
drawing a cat arrived carrying seven unrelated facts, and a model handed facts
about a person finds a use for them. Relevance was the entire point of the
method; padding to a quota guaranteed the opposite.

Two notes from the log about tests that failed for the wrong reason, both avoided
here: one grepped the source across wrapped f-string lines (hence
`memory_instruction()` being a function whose wording can be asserted), and one
collided with the store's de-duplication, which had merged two fixtures into one.
The fixtures below are deliberately low-overlap for that reason.
"""

from __future__ import annotations

import pytest

from beastt.assistant import memory_instruction
from beastt.longterm import LongTermMemory, _tokens


@pytest.fixture
def memory(tmp_path):
    """An empty store on disk. Never the developer's real memory file.

    `user_name` matters: it is how the store tells "what the user is called"
    from "what somebody else is called", which decides what bypasses relevance.
    """
    return LongTermMemory(path=str(tmp_path / "memory.json"),
                          user_name="Lingesh")


@pytest.fixture
def distinct_facts():
    """Facts with little token overlap, so the store cannot merge them.

    Numbering alone is not enough: "observation 1" and "observation 2" share
    every token that survives stopword filtering, because a single digit is
    below the minimum token length. Vocabulary has to differ.
    """
    subjects = ["kettle", "bicycle", "umbrella", "notebook", "cactus", "ladder",
                "postcard", "telescope", "sandal", "harmonica"]
    verbs = ["was mentioned during", "turned up in", "came up while discussing",
             "appeared in"]
    places = ["the kitchen", "the balcony", "a phone call", "an old email"]

    def _build(count):
        out = []
        for n in range(count):
            out.append(f"A {subjects[n % len(subjects)]} "
                       f"{verbs[(n // len(subjects)) % len(verbs)]} "
                       f"{places[(n // 3) % len(places)]} number {n * 7 + 11}")
        return out

    return _build


@pytest.fixture
def populated(memory):
    """Facts chosen to share almost no tokens, so de-duplication cannot merge
    them and a match is unambiguous about which fact matched."""
    memory.add("Lingesh likes to be called Lingaa", core=True)
    memory.add("Lingesh has an RTX 3050 laptop")
    memory.add("Priya prefers oat milk in her coffee")
    memory.add("The sprint review happens on Thursdays")
    memory.add("Ravi drives a blue hatchback")
    return memory


# --- the bug ---------------------------------------------------------------
class TestNoPadding:
    def test_an_unrelated_question_recalls_nothing_but_identity(self, populated):
        """The reported failure: a request to draw a picture must not arrive
        carrying facts about people."""
        recalled = populated.relevant("draw me a picture of a cat")

        assert recalled == ["Lingesh likes to be called Lingaa"]
        assert not any("Priya" in fact for fact in recalled)

    def test_a_bare_greeting_recalls_nothing_but_identity(self, populated):
        """The other half of the report: "Hello." came back with a reading of the
        user's mood attributed to a friend."""
        recalled = populated.relevant("Hello.")

        assert recalled == ["Lingesh likes to be called Lingaa"]

    def test_nothing_is_returned_when_there_is_no_core_fact_either(self, memory):
        memory.add("Priya prefers oat milk in her coffee")
        memory.add("Ravi drives a blue hatchback")

        assert memory.relevant("explain quantum tunnelling") == []

    def test_the_limit_is_a_ceiling_not_a_quota(self, populated):
        """The distinction the bug turned on: `limit` caps how much may be sent,
        it does not describe how much should be."""
        assert len(populated.relevant("draw me a picture of a cat", limit=8)) == 1

    def test_recall_does_not_grow_with_the_store(self, memory, distinct_facts):
        memory.add("Lingesh likes to be called Lingaa", core=True)
        for fact in distinct_facts(40):
            memory.add(fact)
        # Some of the generated facts legitimately merge; what matters is that
        # the store is comfortably larger than the recall limit of eight.
        assert len(memory) > 25

        assert memory.relevant("what is the capital of Peru") == [
            "Lingesh likes to be called Lingaa"]


class TestRelevantRecallStillWorks:
    """Removing the padding must not have removed the recall."""

    def test_a_genuine_match_is_returned(self, populated):
        recalled = populated.relevant("what laptop do I have")
        assert "Lingesh has an RTX 3050 laptop" in recalled

    def test_the_matching_fact_is_the_one_returned(self, populated):
        recalled = populated.relevant("does Priya drink coffee")
        assert "Priya prefers oat milk in her coffee" in recalled
        assert not any("hatchback" in f for f in recalled)

    def test_identity_facts_come_first(self, populated):
        """Who someone is and what they like to be called bear on every reply."""
        recalled = populated.relevant("what laptop do I have")
        assert recalled[0] == "Lingesh likes to be called Lingaa"

    def test_the_limit_is_respected(self, memory):
        # Each fact mentions a laptop but shares little else, so the store's
        # de-duplication cannot merge them into one -- the trap the log records
        # one of the original tests falling into.
        memory.add("Lingesh owns a laptop stand made from bamboo")
        memory.add("Lingesh owns a laptop sleeve in grey felt")
        memory.add("Lingesh owns a laptop cooling pad with quiet fans")
        memory.add("Lingesh owns a laptop docking station for two monitors")
        assert len(memory) == 4

        assert len(memory.relevant("laptop", limit=3)) == 3

    def test_better_matches_are_preferred(self, memory):
        memory.add("Lingesh has an RTX 3050 laptop")
        memory.add("Lingesh once mentioned in passing a great many other "
                   "unrelated things including a laptop somewhere in the middle "
                   "of a very long and rambling sentence")

        assert memory.relevant("laptop")[0] == "Lingesh has an RTX 3050 laptop"

    def test_identity_facts_go_through_regardless_of_the_limit(self, memory):
        """Being *asked* to remember something is no longer enough to be sent
        every turn -- only identity is. A friend the user asked about once must
        not ride along on a question about share prices.
        """
        memory.add("Lingesh prefers to be called Lingaa", core=True)
        memory.add("Lingesh works as a data analyst", core=True)
        memory.add("Lingesh lives in Chennai", core=True)
        memory.add("Lingesh speaks Tamil and English", core=True)
        assert len(memory) == 4

        recalled = memory.relevant("anything at all", limit=2)

        assert recalled == ["Lingesh prefers to be called Lingaa"]
        # The other three are kept, and come back when they are asked about.
        assert "Lingesh lives in Chennai" in memory.relevant("how is Chennai")
        assert "Lingesh works as a data analyst" in memory.relevant("my analyst work")


# --- the instruction, which was the other half of the fix -----------------
class TestMemoryInstruction:
    """It used to say only "use them naturally when relevant", which reads as a
    promise that the facts *were* relevant -- and the recall code had just
    finished making that false."""

    def test_it_says_most_replies_need_none_of_it(self):
        assert "Most replies will not need any of it" in memory_instruction("Lingesh")

    def test_it_forbids_steering_the_answer(self):
        assert "Do not steer the answer towards it" in memory_instruction("Lingesh")

    def test_it_names_the_specific_failure(self):
        """Naming the failure rather than gesturing at it: the reply that
        recommended a friend for artistic advice."""
        assert ("Do not bring up a person who has nothing to do with what was "
                "asked") in memory_instruction("Lingesh")

    def test_it_forbids_reciting_the_notes(self):
        instruction = memory_instruction("Lingesh")
        assert "Do not recite it" in instruction
        assert "mention having" in instruction

    def test_it_names_the_user(self):
        assert "Lingesh" in memory_instruction("Lingesh")
        assert "Priya" in memory_instruction("Priya")

    def test_it_never_promises_the_facts_are_relevant(self):
        """The exact wording that caused the bug must not come back."""
        assert "when relevant" not in memory_instruction("Lingesh")


# --- writing ---------------------------------------------------------------
class TestAdd:
    def test_a_new_fact_is_stored(self, memory):
        assert memory.add("Lingesh has an RTX 3050 laptop") is True
        assert len(memory) == 1

    def test_a_near_duplicate_is_merged_not_appended(self, memory):
        memory.add("Lingesh has an RTX 3050 laptop")

        assert memory.add("Lingesh has an RTX 3050 laptop") is False
        assert len(memory) == 1

    def test_the_longer_wording_wins_a_merge(self, memory):
        memory.add("Lingesh has an RTX 3050")
        memory.add("Lingesh has an RTX 3050 laptop with 16GB")

        assert memory.all_texts() == ["Lingesh has an RTX 3050 laptop with 16GB"]

    def test_distinct_facts_are_not_merged(self, memory):
        """The de-duplication collision that made one of the original tests fail
        for the wrong reason. These must stay separate."""
        memory.add("Priya prefers oat milk in her coffee")
        memory.add("Ravi drives a blue hatchback")

        assert len(memory) == 2

    @pytest.mark.parametrize("text", ["", "  ", "ab", "a"])
    def test_too_short_to_be_a_fact(self, memory, text):
        assert memory.add(text) is False
        assert len(memory) == 0

    def test_whitespace_is_normalised(self, memory):
        memory.add("  Lingesh   has\n an  RTX 3050  ")
        assert memory.all_texts() == ["Lingesh has an RTX 3050"]

    def test_a_merge_can_promote_a_fact_to_core(self, memory):
        memory.add("Lingesh has an RTX 3050 laptop")
        memory.add("Lingesh has an RTX 3050 laptop", core=True)

        assert memory.facts[0]["core"] is True
        # Promotion means "keep this and prefer it when it matches", not "recite
        # it on every turn".
        assert memory.relevant("something entirely unrelated") == []
        assert memory.relevant("what laptop do I have") == [
            "Lingesh has an RTX 3050 laptop"]

    def test_core_is_preferred_among_facts_that_match(self, memory):
        memory.add("Lingesh uses a laptop for college work")
        memory.add("Lingesh has an RTX 3050 laptop", core=True)

        assert memory.relevant("laptop")[0] == "Lingesh has an RTX 3050 laptop"

    def test_the_store_is_bounded_and_drops_the_oldest_non_core_first(self,
                                                                     tmp_path):
        memory = LongTermMemory(path=str(tmp_path / "m.json"), max_facts=5)
        memory.add("An identity fact that must survive", core=True)
        for n in range(20):
            memory.add(f"Disposable observation number {n} about subject {n}")

        assert len(memory) <= 5
        assert "An identity fact that must survive" in memory.all_texts()


class TestForget:
    def test_a_matching_fact_is_removed(self, populated):
        removed = populated.forget("oat milk coffee")

        assert removed == ["Priya prefers oat milk in her coffee"]
        assert "Priya prefers oat milk in her coffee" not in populated.all_texts()

    def test_unrelated_facts_survive(self, populated):
        before = len(populated)
        populated.forget("oat milk coffee")
        assert len(populated) == before - 1

    def test_an_empty_query_removes_nothing(self, populated):
        before = len(populated)
        assert populated.forget("the a of") == []
        assert len(populated) == before

    def test_clear_removes_everything(self, populated):
        assert populated.clear() == 5
        assert len(populated) == 0


class TestPersistence:
    def test_facts_survive_a_reload(self, tmp_path):
        path = str(tmp_path / "m.json")
        LongTermMemory(path=path).add("Lingesh has an RTX 3050 laptop")

        assert LongTermMemory(path=path).all_texts() == [
            "Lingesh has an RTX 3050 laptop"]

    def test_a_corrupt_file_starts_fresh_rather_than_crashing(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text("{not json", encoding="utf-8")

        memory = LongTermMemory(path=str(path))

        assert memory.all_texts() == []
        assert memory.add("Lingesh has an RTX 3050 laptop") is True

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        assert LongTermMemory(path=str(tmp_path / "nope.json")).all_texts() == []


class TestRecallSurvivesWordEndings:
    """Recall is exact-token overlap, which is what keeps it dependency-free and
    instant -- and which used to mean "where do I live" never matched "Lingesh
    lives in Chennai". The fact was there and simply never scored, which from the
    outside is indistinguishable from having forgotten it."""

    @pytest.mark.parametrize("question,fact", [
        ("where do I live", "Lingesh lives in Chennai"),
        ("how are my class going", "Lingesh attends classes at college"),
        ("what do I study", "Lingesh studies engineering"),
    ])
    def test_word_endings_do_not_break_recall(self, memory, question, fact):
        memory.add(fact)
        assert fact in memory.relevant(question)


class TestTokens:
    def test_stopwords_and_short_words_are_dropped(self):
        assert _tokens("I am at the coffee shop") == {"coffee", "shop"}

    def test_case_is_ignored(self):
        assert _tokens("Coffee") == _tokens("COFFEE")

    def test_punctuation_is_ignored(self):
        assert _tokens("coffee!") == _tokens("coffee") == {"coffee"}
        assert _tokens("laptop, charger; cable.") == {"laptop", "charger", "cable"}
