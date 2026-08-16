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
from pathlib import Path

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



# ---------------------------------------------------------------------------
# Second report, after the fixes above shipped. The store was unchanged, and
# the reason was not superseding at all.
#
#   for fuck sake remember this forget beastt is a project of hers
#   forget klingesh is presumably a nickname
#   remember beastt is my project and i am studying MBA
#
#   Jarvis: Got it, Lingaa—I'll keep it straight from now on: BEASTT is your
#           own project, and you're studying for an MBA.
#
# Not one of those three instructions ran. The patterns are anchored with
# `.match()` against the *whole message*, which begins "for fuck sake", so the
# skill declined it and the model answered instead -- sounding exactly like it
# had complied. Two days were spent believing a correction had landed.
#
# And the facts already on disk were written before any of these rules existed,
# so guarding the entrance could never reach them.
# ---------------------------------------------------------------------------

THE_MESSAGE = (
    "for fuck sake remember this forget beastt is a project of hers\n"
    "forget klingesh is presumably a nickname\n"
    "remember beastt is my project and i am studying MBA"
)


class TestTheMessageAsItWasActuallyTyped:
    def test_all_three_instructions_are_found(self):
        from beastt.skills.memory_skill import instructions

        assert instructions(THE_MESSAGE) == [
            ("forget", "beastt is a project of hers"),
            ("forget", "klingesh is presumably a nickname"),
            ("remember", "beastt is my project and i am studying MBA"),
        ]

    def test_the_skill_claims_it(self, skill):
        """It declined the whole message, which is how the model came to answer."""
        assert skill.matches(THE_MESSAGE) is True

    def test_every_instruction_is_reported_separately(self, store, skill):
        store.add("Lingaa owns Beastt, a project of hers")
        store.add("Lingaa studies engineering")

        reply = skill.run(THE_MESSAGE)

        assert reply.count("\n") == 2
        assert "Forgotten: Lingaa owns Beastt" in reply
        assert "nothing to forget" in reply          # the guess was never stored
        assert "That replaces: Lingaa studies engineering" in reply

    def test_the_correction_lands(self, store, skill):
        store.add("Lingaa owns Beastt, a project of hers")
        store.add("Lingaa studies engineering")

        skill.run(THE_MESSAGE)

        assert not [t for t in texts(store) if "engineering" in t]
        assert not [t for t in texts(store) if "of hers" in t]
        assert any("MBA" in t for t in texts(store))


class TestReadingInstructionsOffAMessage:
    @pytest.mark.parametrize("text, expected", [
        ("remember i study MBA", [("remember", "i study MBA")]),
        ("hey jarvis remember i study MBA", [("remember", "i study MBA")]),
        ("ok remember i study MBA", [("remember", "i study MBA")]),
        ("for fuck sake remember i study MBA", [("remember", "i study MBA")]),
        ("cmon remember i study MBA", [("remember", "i study MBA")]),
        ("seriously, remember i study MBA", [("remember", "i study MBA")]),
        ("i said remember i study MBA", [("remember", "i study MBA")]),
    ])
    def test_filler_in_front_is_stepped_over(self, text, expected):
        from beastt.skills.memory_skill import instructions

        assert instructions(text) == expected

    def test_remember_this_before_another_instruction_is_just_attention(self):
        """"remember this forget X" is not a fact called "this forget X"."""
        from beastt.skills.memory_skill import instructions

        assert instructions("remember this forget my old address") == [
            ("forget", "my old address")]

    def test_remember_this_colon_still_carries_its_fact(self):
        """Only stripped when a directive follows. Otherwise the fact is lost."""
        from beastt.skills.memory_skill import instructions

        assert instructions("remember this: i study MBA") == [
            ("remember", "i study MBA")]

    def test_several_lines_are_all_read(self):
        from beastt.skills.memory_skill import instructions

        found = instructions("remember i study MBA\nforget engineering\n"
                             "remember i live in chennai")

        assert [kind for kind, _ in found] == ["remember", "forget", "remember"]

    def test_a_blank_line_is_skipped(self):
        from beastt.skills.memory_skill import instructions

        assert len(instructions("remember i study MBA\n\n\nforget engineering")) == 2

    @pytest.mark.parametrize("text", [
        "what is the weather like",
        "i remember when this used to work",
        "can you forget about it later",
        "",
    ])
    def test_ordinary_conversation_is_not_an_instruction(self, text):
        """Anchored per line on purpose. "i remember when ..." is reminiscing."""
        from beastt.skills.memory_skill import instructions

        assert instructions(text) == []

    def test_a_pasted_document_does_not_file_itself(self):
        """The reason this scans lines rather than searching anywhere: an
        unanchored "remember" would let any pasted text write to memory, which is
        a bug this project has already had once."""
        from beastt.skills.memory_skill import instructions

        pasted = ("The report notes that readers should remember the following "
                  "figures.\nAnalysts forget the base rate at their peril.")

        assert instructions(pasted) == []

    def test_filler_is_not_peeled_away_indefinitely(self):
        """Two passes, not a loop. A paragraph must not be stripped one word at a
        time until an instruction appears somewhere in the middle."""
        from beastt.skills.memory_skill import instructions

        assert instructions("ok so and also now just please listen note "
                            "remember i study MBA") == []


class TestTheReplyCannotLieAboutWhatHappened:
    """The failure being fixed was a confident "Got it" over an unchanged store."""

    def test_a_correction_names_what_it_replaced(self, store, skill):
        store.add("Lingaa studies engineering")

        reply = skill.run("remember i am studying MBA")

        assert "That replaces: Lingaa studies engineering" in reply

    def test_a_forget_that_matched_nothing_says_so(self, store, skill):
        reply = skill.run("forget that i studied medicine")

        assert "Nothing stored" in reply
        assert "nothing to forget" in reply

    def test_a_fact_already_known_is_not_dressed_up_as_new(self, store, skill):
        store.add("Lingaa studies MBA", core=True)

        reply = skill.run("remember i study MBA")

        assert "Already had that one" in reply

    def test_what_the_skill_stores_counts_as_dictated(self, store, skill):
        """Not a detail: dictated facts are exempt from the hedge filter, outrank
        anything reflection infers, and survive eviction. A fact stored through
        this path without that flag is a correction that reflection can undo."""
        skill.run("remember i am probably moving to Bangalore")

        assert len(store.facts) == 1

    def test_a_correction_cannot_be_undone_by_reflection(self, store, skill):
        """The whole point, through the path the user actually types."""
        skill.run("remember i am studying MBA")

        assert store.add("Lingaa studies engineering") is False
        assert any("MBA" in t for t in texts(store))

    def test_the_store_reports_what_a_dictated_fact_displaced(self, store):
        store.add("Lingaa studies engineering")

        was_new, replaced = store.remember_dictated("Lingaa studies MBA")

        assert was_new is True
        assert replaced == ["Lingaa studies engineering"]


class TestRepairingAStoreWrittenBeforeTheRules:
    """Guarding the entrance cannot reach what is already inside."""

    def seeded(self, tmp_path, facts):
        path = tmp_path / "memory.json"
        path.write_text(json.dumps({"facts": [
            {"text": t, "core": False, "created": 1, "updated": i}
            for i, t in enumerate(facts)]}), encoding="utf-8")
        return LongTermMemory(path=str(path), user_name=USER)

    def test_first_person_is_rewritten_on_load(self, tmp_path):
        store = self.seeded(tmp_path, ["Lingaa likes my friend prahathi"])

        assert texts(store) == ["Lingaa likes Lingaa's friend prahathi"]

    def test_a_stored_guess_is_dropped_on_load(self, tmp_path):
        store = self.seeded(
            tmp_path, ["Klingesh (presumably a nickname for Lingaa) works on beastt"])

        assert texts(store) == []

    def test_a_duplicate_pair_collapses_on_load(self, tmp_path):
        store = self.seeded(tmp_path, ["Lingaa studies engineering",
                                       "Lingaa is studying engineering"])

        assert len(store.facts) == 1

    def test_different_values_are_left_for_the_user_to_correct(self, tmp_path):
        """Conservative on purpose. These share a key, but throwing one away on a
        guess is how a repair becomes the next bug report -- and a correction now
        supersedes both anyway, which is the user's call."""
        store = self.seeded(tmp_path, ["Lingaa studies engineering",
                                       "Lingaa is studying renewable energy"])

        assert len(store.facts) == 2

    def test_a_dictated_hedge_is_kept(self, tmp_path):
        """The user is allowed to record uncertainty. Only inferred hedges go."""
        path = tmp_path / "memory.json"
        path.write_text(json.dumps({"facts": [
            {"text": "Lingaa is probably moving to Bangalore", "core": True,
             "created": 1, "updated": 1}]}), encoding="utf-8")

        store = LongTermMemory(path=str(path), user_name=USER)

        assert len(store.facts) == 1

    def test_a_dictated_fact_is_still_depersonalised(self, tmp_path):
        """"my friend" in the store is wrong whoever typed it."""
        path = tmp_path / "memory.json"
        path.write_text(json.dumps({"facts": [
            {"text": "Lingaa likes my friend prahathi", "core": True,
             "created": 1, "updated": 1}]}), encoding="utf-8")

        store = LongTermMemory(path=str(path), user_name=USER)

        assert texts(store) == ["Lingaa likes Lingaa's friend prahathi"]

    def test_it_is_written_back_to_disk(self, tmp_path):
        """The file itself, not a reloaded store -- a store repairs on load, so
        reading it back through one would pass whether or not anything persisted.
        The file matters: the CLI and the browser both open it, and somebody
        opening memory.json to check what is in there deserves the truth.
        """
        store = self.seeded(tmp_path, ["Lingaa likes my friend prahathi"])

        on_disk = json.loads(Path(store.path).read_text(encoding="utf-8"))

        assert [f["text"] for f in on_disk["facts"]] == [
            "Lingaa likes Lingaa's friend prahathi"]

    def test_it_does_nothing_the_second_time(self, tmp_path):
        """Idempotent, so it is safe to run on every load."""
        store = self.seeded(tmp_path, ["Lingaa likes my friend prahathi",
                                       "Lingaa studies engineering",
                                       "Lingaa is studying engineering"])

        assert store.repair() == []

    def test_a_clean_store_is_not_rewritten(self, tmp_path):
        store = self.seeded(tmp_path, ["Lingaa studies MBA",
                                       "Lingaa owns a laptop with an RTX 3050"])

        assert store.repair() == []
        assert len(store.facts) == 2

    def test_it_says_what_it_did(self, tmp_path, capsys):
        """Silent data surgery is not something to do to somebody's memory file.
        The log names each change, so an unwelcome one is traceable."""
        self.seeded(tmp_path, ["Lingaa likes my friend prahathi",
                               "Lingaa probably studies medicine"])

        out = capsys.readouterr().out

        assert "Tidied 2 fact(s)" in out
        assert "rewrote first person" in out
        assert "dropped a guess" in out

    def test_the_whole_reported_store(self, tmp_path):
        """Every line from the screenshot, then the message as typed."""
        store = self.seeded(tmp_path, [
            "Lingaa likes my friend prahathi she is my home girl that corrects me",
            "Prahathi is Lingaa's friend",
            "Lingaa studies engineering",
            "Lingaa is studying engineering",
            "Lingaa owns Beastt, a project of hers",
            "Klingesh (presumably a nickname for Lingaa) works on project-beastt",
            "Lingaa owns a laptop with an RTX 3050",
        ])

        MemorySkill(store, USER).run(THE_MESSAGE)
        remaining = texts(store)

        assert not [t for t in remaining if "engineering" in t]
        assert not [t for t in remaining if "of hers" in t]
        assert not [t for t in remaining if "presumably" in t]
        assert not [t for t in remaining if " my " in t]
        assert [t for t in remaining if "MBA" in t]
        assert "Lingaa owns a laptop with an RTX 3050" in remaining


class TestGrammarInTheStore:
    @pytest.mark.parametrize("text, expected", [
        ("i call her prahathi", "Lingaa calls her prahathi"),
        ("i live in chennai", "Lingaa lives in chennai"),
        ("i study MBA", "Lingaa studies MBA"),
        ("i own a laptop", "Lingaa owns a laptop"),
        ("i use GitHub", "Lingaa uses GitHub"),
        ("i want a new phone", "Lingaa wants a new phone"),
    ])
    def test_the_verb_follows_the_subject(self, text, expected):
        assert statements.depersonalise(text, USER) == expected

    def test_a_verb_not_on_the_list_is_left_alone(self):
        """A bounded list, because anything after a name might be a noun."""
        assert statements.depersonalise("i cycle to work", USER) == \
            "Lingaa cycle to work"
