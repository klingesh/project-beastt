"""Correcting a stored fact, and not storing the model's guesses as facts.

Reported, with a screenshot of the store. Told the day before that BEASTT was a
friend's project and that its user studied engineering, both wrong, the user
corrected it:

    > remember beast is my project and i am studying MBA

Asked again the next morning, the answer was unchanged, and the recited memory
showed why:

    - Lingaa likes my friend prahathi she is my home girl that advises and
      corrects me most of the time ...
    - Lingaa owns Beastt, a project of hers
    - Lingaa studies engineering
    - Lingaa is studying engineering
    - Klingesh (presumably a nickname for Lingaa) works on project-beastt

Four separate faults are visible in those five lines.

**The correction could not displace anything.** Superseding lived in
`remember_statement()`, and neither of the other two ways a fact can arrive goes
through it: the explicit "remember ..." skill calls `add()`, and end-of-session
reflection calls `add()`. Two of the three writers could not correct the third.

**The same fact was stored twice, differently phrased.** "studies engineering"
and "is studying engineering" are alternatives that nothing recognised as such --
the same bug seen from the other side.

**First person survived into the store.** Only the leading clause was rewritten,
so "my friend", "corrects me" and "i am" went in verbatim. Recall then hands those
sentences to the model as its own background knowledge, where "my friend prahathi"
says the assistant has a friend called Prahathi -- and two lines later it had lost
track of whose project BEASTT was and what gender the owner was.

**A hedge became a fact.** Nobody said Klingesh was a nickname. Reflection worked
it out, marked it "presumably", and stored it -- after which the hedge is
indistinguishable from something the user said, because recall passes the sentence
along with no note of where it came from.
"""

from __future__ import annotations

import json

import pytest

from beastt import statements
from beastt.longterm import LongTermMemory
from beastt.skills.memory_skill import MemorySkill

USER = "Lingaa"


@pytest.fixture
def store(tmp_path):
    return LongTermMemory(path=str(tmp_path / "memory.json"), user_name=USER)


@pytest.fixture
def skill(store):
    """The explicit "remember ..." path, which is how the user corrected it."""
    return MemorySkill(store, USER)


def texts(store):
    return [f["text"] for f in store.facts]


class TestTheReportedFailure:
    def test_the_correction_now_displaces_the_wrong_fact(self, store, skill):
        store.add("Lingaa studies engineering")
        store.add("Lingaa is studying engineering")

        skill.run("remember beast is my project and i am studying MBA")

        assert not [t for t in texts(store) if "engineering" in t]
        assert any("MBA" in t for t in texts(store))

    def test_the_duplicate_pair_never_forms(self, store):
        """Two reflections phrasing one fact two ways. Both were on disk."""
        store.add("Lingaa studies engineering")
        store.add("Lingaa is studying engineering")

        assert len(store.facts) == 1

    def test_the_hedge_is_refused(self, store):
        assert store.add(
            "Klingesh (presumably a nickname for Lingaa) works on project-beastt"
        ) is False
        assert texts(store) == []

    def test_no_stored_fact_talks_in_the_first_person(self, store):
        store.add("Lingaa likes my friend prahathi she is my home girl that "
                  "advises and corrects me most of the time")

        stored, = texts(store)
        assert "my " not in stored.lower()
        assert " me " not in stored.lower()
        assert "Lingaa's friend prahathi" in stored

    def test_the_whole_scenario(self, store, skill):
        """Everything above at once, from the reported store to the answer."""
        for fact in ("Lingaa owns Beastt, a project of hers",
                     "Lingaa studies engineering",
                     "Lingaa is studying engineering",
                     "Klingesh (presumably a nickname for Lingaa) works on beastt",
                     "Prahathi studies engineering"):
            store.add(fact)

        skill.run("remember beast is my project and i am studying MBA")
        remaining = texts(store)

        # The user's own studies are corrected...
        assert not [t for t in remaining if "Lingaa" in t and "engineering" in t]
        assert [t for t in remaining if "MBA" in t]
        # ...her friend's are untouched, and the guess never arrived.
        assert "Prahathi studies engineering" in remaining
        assert not [t for t in remaining if "presumably" in t]


class TestEveryWriterCanCorrect:
    """The root of it. Three ways a fact arrives; all three must supersede.

    Parametrised over the writers rather than tested once, because the bug was
    not that superseding was wrong -- it was that only one caller had it.
    """

    def as_reflection(self, store, text):
        return store.add(text)

    def as_dictated(self, store, text):
        return store.add(text, core=True)

    def as_statement(self, store, text):
        """The deterministic capture path, which had it all along."""
        return store.remember_statement(statements.Statement(text, "studies"))

    @pytest.fixture(params=["as_reflection", "as_dictated", "as_statement"])
    def write(self, request, store):
        return lambda text: getattr(self, request.param)(store, text)

    def test_it_replaces_what_it_contradicts(self, store, write):
        store.add("Lingaa studies engineering")

        write("Lingaa studies MBA")

        assert texts(store) == ["Lingaa studies MBA"]

    def test_it_replaces_a_differently_worded_version(self, store, write):
        store.add("Lingaa is studying engineering")

        write("Lingaa studies MBA")

        assert texts(store) == ["Lingaa studies MBA"]

    def test_it_leaves_unrelated_facts_alone(self, store, write):
        store.add("Lingaa owns a laptop with an RTX 3050")
        store.add("Prahathi takes care of Lingaa")

        write("Lingaa studies MBA")

        assert len(store.facts) == 3


class TestTheKeyKnowsWhoItIsAbout:
    """Superseding moving into `add()` brought facts about *other people* into
    the mechanism, where a bare predicate is actively dangerous."""

    @pytest.mark.parametrize("text, expected", [
        ("Lingaa studies MBA", "lingaa"),
        ("Lingaa is studying MBA", "lingaa"),
        ("Lingaa currently studies MBA", "lingaa"),
        ("Prahathi studies engineering", "prahathi"),
        ("Lingaa's mother lives in Delhi", "mother"),
        ("Lingaa lives in Chennai", "lingaa"),
        ("Klingesh works at Infosys", "klingesh"),
    ])
    def test_the_subject_is_the_word_before_the_predicate(self, text, expected):
        assert statements.subject_of(text) == expected

    def test_two_people_can_study_different_things(self, store):
        store.add("Prahathi studies engineering")

        store.add("Lingaa studies MBA")

        assert len(store.facts) == 2

    def test_a_relative_can_live_somewhere_else(self, store):
        """The reason the subject is taken from the end and not the start: the
        first word is often a possessor."""
        store.add("Lingaa's mother lives in Delhi")

        store.add("Lingaa lives in Chennai")

        assert len(store.facts) == 2

    def test_the_key_is_subject_and_predicate(self, store):
        store.add("Lingaa lives in Chennai")

        assert store.facts[0]["key"] == "lingaa:lives-in"

    @pytest.mark.parametrize("text", [
        "she studies engineering",
        "he works at Infosys",
        "they live in Chennai",
    ])
    def test_an_unresolved_subject_gets_no_key(self, text):
        """It is about somebody, and guessing who is how a friend's degree
        overwrites the user's. No key means it accumulates, which is survivable."""
        assert statements.full_key(text) == ""

    def test_a_modifier_is_not_mistaken_for_the_subject(self):
        """Found by running the reported store through this: "her full name is
        Prahadhesvaryaa K S" keyed itself on *full*, so Lingaa's own full name
        would have collided with it and erased her friend's."""
        assert statements.subject_of("her full name is Prahadhesvaryaa K S") == ""
        assert statements.full_key("her full name is Prahadhesvaryaa K S") == ""

    def test_a_named_full_name_still_keys_properly(self):
        assert statements.full_key("Lingaa's full name is Lingesh K") == "lingaa:name"


class TestTheDeclaredKeyIsNotDecoration:
    """A capture pattern knows what it matched; reading the predicate back off a
    sentence is a guess that happens to agree.

    Every keyed template in `_PATTERNS` today re-infers correctly from its own
    output, so dropping the declared key changes nothing -- four separate
    mutations proved that by surviving. Which means it is decoration until
    something depends on it, and the next refactor deletes it. These are the
    checks that make it load-bearing: a predicate the sentence does not reveal.
    """

    def test_a_predicate_the_sentence_hides_still_supersedes(self, store):
        store.remember_statement(statements.Statement("Lingaa is a vegetarian",
                                                     "diet"))

        replaced = store.remember_statement(
            statements.Statement("Lingaa is a vegan", "diet"))

        assert replaced == ["Lingaa is a vegetarian"]
        assert texts(store) == ["Lingaa is a vegan"]

    def test_such_a_key_is_read_off_the_leading_subject(self):
        """There is no predicate match to look in front of, so the subject comes
        from the front -- which is safe here because these are template outputs."""
        assert statements.full_key("Lingaa is a vegetarian", "diet") == "lingaa:diet"

    def test_a_rephrasing_of_it_still_supersedes(self, store):
        store.remember_statement(statements.Statement("Lingaa is a vegetarian",
                                                     "diet"))
        store.add("Lingaa is a vegetarian mostly", key="diet")

        replaced = store.remember_statement(
            statements.Statement("Lingaa is a vegan", "diet"))

        assert replaced and texts(store) == ["Lingaa is a vegan"]

    def test_superseding_runs_before_merging(self, store):
        """Which is why the merge branch never needs to write a key: anything
        sharing this fact's key is gone by the time a merge could happen. Asserted
        because the reverse order would look identical here and quietly leave
        near-duplicate alternatives on disk."""
        store.add("Lingaa is a vegetarian", key="diet")

        store.add("Lingaa is a vegetarian mostly", key="diet")

        assert len(store.facts) == 1
        assert store.facts[0]["key"] == "lingaa:diet"

    def test_a_legacy_bare_declared_key_is_still_honoured(self, tmp_path):
        """On disk from before keys carried a subject, and not re-derivable from
        the sentence -- so the upgrade has to use the stored predicate, not throw
        it away and start again."""
        path = tmp_path / "memory.json"
        path.write_text(json.dumps({"facts": [
            {"text": "Lingaa is a vegetarian", "key": "diet",
             "core": False, "created": 1, "updated": 1},
        ]}), encoding="utf-8")
        store = LongTermMemory(path=str(path), user_name=USER)

        store.add("Lingaa is a vegan", key="diet")

        assert texts(store) == ["Lingaa is a vegan"]


class TestOldStoresStillWork:
    def test_a_legacy_bare_key_is_upgraded_for_comparison(self, tmp_path):
        """Keys were `predicate` before they were `subject:predicate`, and a real
        install is full of the old shape. Without the upgrade this change would
        pass every test and supersede nothing on the machine that reported it --
        which is precisely how the first version of superseding failed.
        """
        path = tmp_path / "memory.json"
        path.write_text(json.dumps({"facts": [
            {"text": "Lingaa studies engineering", "key": "studies",
             "core": False, "created": 1, "updated": 1},
        ]}), encoding="utf-8")
        store = LongTermMemory(path=str(path), user_name=USER)

        store.add("Lingaa studies MBA")

        assert texts(store) == ["Lingaa studies MBA"]

    def test_a_fact_with_no_key_field_at_all_is_read_from_its_sentence(self,
                                                                     tmp_path):
        """Older still: written before keys existed."""
        path = tmp_path / "memory.json"
        path.write_text(json.dumps({"facts": [
            {"text": "Lingaa lives with Prahadhesvaryaa K S",
             "core": False, "created": 1, "updated": 1},
        ]}), encoding="utf-8")
        store = LongTermMemory(path=str(path), user_name=USER)

        store.add("Lingaa lives with his parents")

        assert texts(store) == ["Lingaa lives with his parents"]


class TestWhatYouSaidOutranksWhatItGuessed:
    """Reflection re-reads the last twenty messages every six turns. Without this
    rule an old transcript could reinstate the fact just corrected."""

    def test_an_inference_cannot_overwrite_a_dictated_fact(self, store):
        store.add("Lingaa studies MBA", core=True)

        assert store.add("Lingaa studies engineering") is False
        assert texts(store) == ["Lingaa studies MBA"]

    def test_the_refused_inference_is_not_kept_alongside(self, store):
        """The first attempt declined to overwrite and appended anyway, which
        reproduced the reported bug: two contradictory facts, and the model
        choosing between them."""
        store.add("Lingaa studies MBA", core=True)

        store.add("Lingaa studies engineering")

        assert len(store.facts) == 1

    def test_a_dictated_fact_can_overwrite_an_inference(self, store):
        store.add("Lingaa studies engineering")

        store.add("Lingaa studies MBA", core=True)

        assert texts(store) == ["Lingaa studies MBA"]

    def test_a_dictated_fact_can_overwrite_another(self, store):
        """Someone is allowed to change their mind, or fix a typo."""
        store.add("Lingaa studies MBA", core=True)

        store.add("Lingaa studies MBA finance", core=True)

        assert texts(store) == ["Lingaa studies MBA finance"]

    def test_an_inference_can_overwrite_another(self, store):
        store.add("Lingaa studies engineering")

        store.add("Lingaa studies MBA")

        assert texts(store) == ["Lingaa studies MBA"]


class TestSpeculationIsNotAFact:
    @pytest.mark.parametrize("text", [
        "Klingesh (presumably a nickname for Lingaa) works on project-beastt",
        "Lingaa probably lives in Chennai",
        "Lingaa might be studying MBA",
        "Lingaa may be allergic to peanuts",
        "Lingaa seems to prefer tea",
        "Lingaa apparently owns a laptop",
        "I think Lingaa studies MBA",
        "It is unclear where Lingaa lives",
        "Lingaa is likely to travel often",
        "This suggests Lingaa works at Infosys",
    ])
    def test_a_hedged_sentence_is_refused(self, store, text):
        assert store.add(text) is False
        assert store.facts == []

    @pytest.mark.parametrize("text", [
        "Lingaa studies MBA",
        "Lingaa lives in Chennai",
        "Lingaa owns a laptop with an RTX 3050",
        "Prahathi takes care of Lingaa",
    ])
    def test_a_plain_sentence_is_kept(self, store, text):
        assert store.add(text) is True

    def test_the_user_may_dictate_a_hedge_if_they_want_to(self, store):
        """Their sentence, their call. This function's job is to stop the *model*
        promoting its own guesses, not to argue with the person."""
        assert store.add("Lingaa is probably moving to Bangalore",
                         core=True) is True

    def test_a_refused_guess_displaces_nothing(self, store):
        """It must be dropped before it can supersede -- otherwise a hedge would
        delete a good fact and leave nothing in its place."""
        store.add("Lingaa studies MBA")

        store.add("Lingaa probably studies engineering")

        assert texts(store) == ["Lingaa studies MBA"]


class TestFirstPersonIsRewritten:
    @pytest.mark.parametrize("text, expected", [
        ("my friend prahathi", "Lingaa's friend prahathi"),
        ("i am studying MBA", "Lingaa is studying MBA"),
        ("i'm studying MBA", "Lingaa is studying MBA"),
        ("i've got a laptop", "Lingaa has got a laptop"),
        ("i'll be in Chennai", "Lingaa will be in Chennai"),
        ("i'd like tea", "Lingaa would like tea"),
        ("she corrects me most of the time",
         "she corrects Lingaa most of the time"),
        ("i have her as my friend", "Lingaa has her as Lingaa's friend"),
        ("i don't drink coffee", "Lingaa doesn't drink coffee"),
        ("that laptop is mine", "that laptop is Lingaa's"),
        ("i did it myself", "Lingaa did it Lingaa"),
    ])
    def test_the_pronouns_and_the_verb_after_them(self, text, expected):
        assert statements.depersonalise(text, USER) == expected

    def test_a_first_person_verb_is_not_left_stranded(self):
        """"Lingaa am studying MBA" is the sort of sentence that makes a model
        distrust its own context."""
        assert "am" not in statements.depersonalise("i am studying MBA", USER)

    @pytest.mark.parametrize("text", [
        "Lingaa owns a laptop with an RTX i7",
        "Lingaa is building an AI assistant",
        "Lingaa uses Spss and Excel",
    ])
    def test_it_leaves_words_containing_i_alone(self, text):
        assert statements.depersonalise(text, USER) == text

    def test_without_a_name_it_says_the_user(self):
        assert statements.depersonalise("my laptop", "") == "the user's laptop"

    def test_a_third_person_fact_passes_through_untouched(self):
        text = "Prahathi takes care of Lingaa"
        assert statements.depersonalise(text, USER) == text

    def test_the_store_applies_it(self, store):
        """Not only the helper: the rewrite has to be on the way in, or the
        sentences on disk stay as they were."""
        store.add("Lingaa likes my friend prahathi")

        assert texts(store) == ["Lingaa likes Lingaa's friend prahathi"]

    def test_the_statement_path_applies_it_too(self, store):
        store.remember_statement(statements.Statement("Lingaa lives with my "
                                                      "parents", "lives-with"))

        assert texts(store) == ["Lingaa lives with Lingaa's parents"]

    def test_repeating_a_fact_is_not_reported_as_replacing_it(self, store):
        """`remember_statement` rewrites before it works out what it displaced,
        and this is why. Comparing the raw sentence against the stored one makes
        an unchanged fact look like a different fact, and the assistant announces
        "noted: ... (replacing 1)" for a fact it merely heard again.
        """
        store.add("Lingaa lives with my parents")

        replaced = store.remember_statement(
            statements.Statement("Lingaa lives with my parents", "lives-with"))

        assert replaced == []
        assert len(store.facts) == 1


class TestNothingElseChanged:
    def test_facts_without_a_key_still_accumulate(self, store):
        """Somebody can own two laptops."""
        store.add("Lingaa has a laptop")
        store.add("Lingaa has a bike")

        assert len(store.facts) == 2

    def test_near_duplicates_still_merge(self, store):
        store.add("Lingaa owns a laptop with an RTX 3050")
        store.add("Lingaa owns a laptop with an RTX 3050 graphics card")

        assert len(store.facts) == 1

    def test_forget_still_removes(self, store):
        store.add("Lingaa studies MBA")

        removed = store.forget("MBA")

        assert removed and store.facts == []

    def test_a_statement_still_reports_what_it_replaced(self, store):
        """The caller says "noted: ... (replacing 1)", so the return value is
        part of the behaviour and not a detail."""
        store.add("Lingaa lives in Bangalore")

        replaced = store.remember_statement(
            statements.Statement("Lingaa lives in Chennai", "lives-in"))

        assert replaced == ["Lingaa lives in Bangalore"]

    def test_a_short_fact_is_still_refused(self, store):
        assert store.add("hi") is False

    def test_it_survives_a_reload(self, store, tmp_path):
        store.add("Lingaa studies MBA", core=True)

        reloaded = LongTermMemory(path=store.path, user_name=USER)
        reloaded.add("Lingaa studies engineering")

        assert [f["text"] for f in reloaded.facts] == ["Lingaa studies MBA"]


class TestTheReasoningIsRecorded:
    def flat(self, text):
        return " ".join((text or "").split())

    def test_add_says_why_superseding_lives_there(self):
        text = self.flat(LongTermMemory.add.__doc__)

        assert "two could not correct the third" in text

    def test_the_speculation_filter_says_where_it_came_from(self):
        text = self.flat(statements.is_speculation.__doc__)

        assert "presumably a nickname" in text
        assert "with no note of where" in text

    def test_the_rewrite_records_the_confusion_it_prevents(self):
        text = self.flat(statements.depersonalise.__doc__)

        assert "a project of hers" in text
        assert "resolved the pronouns to itself" in text

    def test_the_core_rule_says_why_it_exists(self):
        text = self.flat(LongTermMemory._blocked_by_core.__doc__)

        assert "reinstate the very fact" in text
        assert "reproduced the original bug" in text
