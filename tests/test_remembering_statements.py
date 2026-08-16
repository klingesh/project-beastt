"""Saying "i live in chennai" has to still be true tomorrow.

Reported, after saying it and restarting:

    > so jarvis where do i live
    You live with Prahadhesvaryaa K S, but I don't recall you mentioning the
    exact location -- would you like to share that with me, Lingaa?

Chennai had been said and was gone. Three faults met to produce that.

**Nothing said in the browser was ever remembered.** `remember_session()` is
called from four places in `cli.py` and nowhere else, so the entire web interface
wrote no durable facts. In the CLI it only runs on a graceful exit, and the
documented restart procedure is `taskkill /F`.

**A plain statement was never captured.** `MemorySkill` needs the literal word
"remember", so "i live in chennai" had no path into the store at all.

**And the previous entry closed half a loop.** It correctly identified a
first-person statement as a fact being offered *to be remembered*, stopped
researching it, and then did not remember it.

The wrong fact it answered from -- a living arrangement -- had itself been
invented by end-of-session reflection out of somebody being a friend. So a
correction has to be possible too.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from beastt import statements
from beastt.longterm import LongTermMemory

USER = "Lingaa"
NAME = "Jarvis"


@pytest.fixture
def memory(tmp_path):
    return LongTermMemory(path=str(tmp_path / "m.json"), user_name=USER)


def fact(text):
    return statements.fact_from(text, USER, NAME)


# --- what counts as a fact -------------------------------------------------
class TestCapturingFacts:
    @pytest.mark.parametrize("said,expected", [
        # The reported message, verbatim.
        ("i live in chennai jarvis fyi", "Lingaa lives in chennai"),
        ("i live in pg not casagrand", "Lingaa lives in pg"),
        ("i'm living in coimbatore", "Lingaa lives in coimbatore"),
        ("i moved to bangalore", "Lingaa lives in bangalore"),
        ("i live with my parents", "Lingaa lives with my parents"),
        ("i work at infosys", "Lingaa works at infosys"),
        ("i study engineering at psg", "Lingaa studies engineering at psg"),
        ("my name is Lingesh", "Lingaa's name is Lingesh"),
        ("i am from tamil nadu", "Lingaa is from tamil nadu"),
        ("my birthday is in june", "Lingaa's birthday is june"),
        ("i am 21 years old", "Lingaa is 21 years old"),
        ("i have an rtx 3050 laptop", "Lingaa has rtx 3050 laptop"),
        ("i use windows 11", "Lingaa uses windows 11"),
        ("i drive a splendor", "Lingaa drives splendor"),
        ("i like filter coffee", "Lingaa likes filter coffee"),
        ("i hate instant coffee", "Lingaa dislikes instant coffee"),
        ("i am allergic to peanuts", "Lingaa is allergic to peanuts"),
        ("i run a trading bot on a vps",
         "Lingaa is working on trading bot on a vps"),
    ])
    def test_a_statement_becomes_a_third_person_fact(self, said, expected):
        captured = fact(said)
        assert captured is not None, f"{said!r} offered a fact and it was dropped"
        assert captured.text == expected

    def test_the_assistants_name_is_not_part_of_the_fact(self):
        assert "jarvis" not in fact("i live in chennai jarvis fyi").text.lower()

    def test_trailing_noise_is_cut(self):
        """"i live in pg not casagrand" records a PG, not "pg not casagrand"."""
        assert fact("i live in pg not casagrand").text == "Lingaa lives in pg"

    def test_a_fact_plus_a_question_still_captures_the_fact(self):
        """"i live in chennai, whats the weather here" should do both -- record
        the city and answer the question."""
        assert fact("i live in chennai, whats the weather here").text == (
            "Lingaa lives in chennai")


class TestWhatIsNotAFact:
    @pytest.mark.parametrize("said", [
        # Transient: about right now, not about a life.
        "i am at college attending my classes",
        "i am going to college today",
        "i feel tired right now",
        "i am about to leave",
        "i am currently in a meeting",
        "i will be home later",
    ])
    def test_passing_states_are_not_stored(self, said):
        assert fact(said) is None

    @pytest.mark.parametrize("said", [
        # These *do* match a pattern, so only the transient guard rejects them.
        # The list above does not exercise it at all -- every one of those is
        # rejected for not matching anything, so the guard could be deleted and
        # they would all still pass.
        "i am staying in a hotel today",
        "i am working at a client site today",
        "i have a meeting today",
        "i am living with my cousin for now",
        "i use the library right now",
        "i moved to a hotel tonight",
    ])
    def test_a_matching_pattern_is_still_rejected_when_it_is_transient(self, said):
        assert fact(said) is None, (
            f"{said!r} is about today, not about a life, and would be filed "
            "permanently")

    @pytest.mark.parametrize("said", [
        "where do i live",
        "i live in chennai?",
        "do i live in chennai",
        "what do you remember about me",
    ])
    def test_questions_are_not_statements(self, said):
        assert fact(said) is None

    @pytest.mark.parametrize("said", [
        # These match a pattern and carry nothing. A store full of them is worse
        # than an empty one, because recall spends its budget on them.
        "i have to go",
        "i have a question",
        "i have no idea",
        "i like it",
        "i use it",
        "i have some",
        "i have a minute",
    ])
    def test_matches_that_say_nothing_are_dropped(self, said):
        assert fact(said) is None

    def test_prose_about_other_people_is_not_about_the_user(self):
        """Anchored at the start, so a sentence mentioning where people live is
        not read as the user's address -- the same mistake as researching a
        statement."""
        assert fact("the article says people who live in chennai are happier") is None

    @pytest.mark.parametrize("said", ["", None, "   ", "hey jarvis",
                                      "nothing much jarvis", "ok"])
    def test_nothing_offered(self, said):
        assert fact(said) is None

    def test_a_whole_paragraph_is_not_a_fact(self):
        assert fact("i live in " + "a very long description " * 10) is None


# --- superseding -----------------------------------------------------------
class TestCorrectingAFact:
    def test_a_new_city_replaces_the_old_one(self):
        """Nobody lives in two cities."""
        store_a = fact("i live in chennai")
        store_b = fact("i moved to coimbatore")
        assert store_a.key == store_b.key == "lives-in"

    def test_the_store_replaces_rather_than_accumulates(self, memory):
        memory.remember_statement(fact("i live in chennai"))
        replaced = memory.remember_statement(fact("i moved to coimbatore"))

        assert replaced == ["Lingaa lives in chennai"]
        assert memory.all_texts() == ["Lingaa lives in coimbatore"]

    def test_the_key_is_stored_on_the_fact(self, memory):
        """Written as well as inferred. `infer_key` covers legacy facts, which
        means dropping the stored key changes no behaviour at all -- so without
        this the field could be deleted and every other test would pass. It is
        worth keeping: reading a key off a sentence is a guess, and the one
        recorded at capture time is not.
        """
        memory.remember_statement(fact("i live in chennai"))

        stored = [f for f in memory.facts if f["text"] == "Lingaa lives in chennai"]
        # Subject included since superseding moved into add(): a bare predicate
        # made two people's studies erase one another.
        assert stored and stored[0].get("key") == "lingaa:lives-in"

    def test_a_keyless_fact_records_an_empty_key(self, memory):
        memory.remember_statement(fact("i have a laptop"))
        assert memory.facts[0].get("key") == ""

    def test_the_key_survives_a_reload(self, memory, tmp_path):
        memory.remember_statement(fact("i live in chennai"))

        reloaded = LongTermMemory(path=memory.path, user_name=USER)

        assert reloaded.facts[0].get("key") == "lingaa:lives-in"

    def test_facts_without_a_key_accumulate(self, memory):
        """Someone can own two laptops."""
        memory.remember_statement(fact("i have a laptop"))
        memory.remember_statement(fact("i have a bike"))

        assert len(memory) == 2

    def test_a_place_and_a_household_are_both_true(self, memory):
        """"i live with my parents" must not erase the city, or vice versa."""
        memory.remember_statement(fact("i live in chennai"))
        memory.remember_statement(fact("i live with my parents"))

        assert len(memory) == 2

    def test_legacy_facts_are_superseded_too(self, memory):
        """The fix that makes this work at all on a real machine.

        Keys are new, so every fact already on disk has none. Without reading the
        key back off the sentence, superseding would pass every test here and
        never once fire on an install that had been running for months.
        """
        memory.add("Lingaa lives in Coimbatore")          # written by reflection

        replaced = memory.remember_statement(fact("i live in chennai"))

        assert replaced == ["Lingaa lives in Coimbatore"]
        assert memory.all_texts() == ["Lingaa lives in chennai"]

    @pytest.mark.parametrize("stored,key", [
        ("Lingaa lives in Coimbatore", "lives-in"),
        ("Lingaa lives with Prahadhesvaryaa K S", "lives-with"),
        ("Lingaa works at Infosys", "works-at"),
        ("Lingaa studies engineering", "studies"),
        ("Lingaa's name is Lingesh", "name"),
        ("Lingaa is from Tamil Nadu", "from"),
        ("Lingaa is 21 years old", "age"),
        ("Lingaa has an RTX 3050 laptop", ""),
        ("Lingaa likes filter coffee", ""),
    ])
    def test_a_key_is_read_back_off_a_stored_fact(self, stored, key):
        assert statements.infer_key(stored) == key


class TestDenials:
    @pytest.mark.parametrize("said,expected", [
        ("i don't live with prahadhesvaryaa", "prahadhesvaryaa"),
        ("i no longer work at infosys", "infosys"),
        ("i dont have a car", "car"),
        ("i am not from kerala", "kerala"),
    ])
    def test_a_correction_becomes_a_forget_query(self, said, expected):
        assert statements.denial_from(said, NAME) == expected

    def test_the_invented_fact_can_be_removed(self, memory):
        """The one the session actually needed: a living arrangement that
        end-of-session reflection had invented from somebody being a friend."""
        memory.add("Lingaa lives with Prahadhesvaryaa K S")
        memory.remember_statement(fact("i live in chennai"))

        removed = memory.forget(
            statements.denial_from("i don't live with prahadhesvaryaa", NAME))

        assert "Lingaa lives with Prahadhesvaryaa K S" in removed
        assert memory.all_texts() == ["Lingaa lives in chennai"]

    @pytest.mark.parametrize("said", [
        "i live in chennai", "i have a laptop", "where do i live",
    ])
    def test_an_ordinary_statement_is_not_a_denial(self, said):
        assert statements.denial_from(said, NAME) == ""


# --- through the real Assistant -------------------------------------------
class ScriptedBrain:
    def __init__(self, *replies):
        self.replies = list(replies) or ["ok"]
        self.calls = []

    def is_available(self):
        return True

    def reply(self, messages, **_kwargs):
        self.calls.append("\n".join(m.content for m in messages))
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]

    def stream(self, messages, **kwargs):
        yield self.reply(messages, **kwargs)


@pytest.fixture
def build(tmp_path, make_config):
    from beastt.config import Config

    store = str(tmp_path / "memory.json")

    def _build(brain):
        from beastt.assistant import Assistant

        config = make_config(
            name=NAME, user_name=USER, longterm_enabled=True,
            memory_path=store, documents_enabled=False, code_enabled=False,
            imagegen_enabled=False, bot_status_repo="", data_enabled=False,
            search_enabled=False, deliberate=False, quotes_enabled=False,
        )
        return Assistant(config=config, brain=brain, verbose=False)

    return _build


class TestTheReportedSession:
    def test_the_statement_is_stored_as_it_is_said(self, build):
        assistant = build(ScriptedBrain("Got it."))

        assistant.respond("i live in chennai jarvis fyi")

        assert "Lingaa lives in chennai" in assistant.longterm.all_texts()

    def test_the_model_is_told_so_it_can_acknowledge(self, build):
        """The user restarted and re-asked, which is what someone does when they
        are not sure it landed. Silence is the reason they had to."""
        brain = ScriptedBrain("Got it -- Chennai it is.")
        assistant = build(brain)

        assistant.respond("i live in chennai")

        assert "You have just noted this about Lingaa" in brain.calls[0]
        assert "Lingaa lives in chennai" in brain.calls[0]
        assert "do not say you have saved a note" in brain.calls[0]

    def test_it_survives_a_restart(self, build):
        """The whole complaint: said, restarted, gone."""
        build(ScriptedBrain()).respond("i live in chennai jarvis fyi")

        fresh = build(ScriptedBrain())
        fresh.respond("so jarvis where do i live")

        assert "Lingaa lives in chennai" in fresh.longterm.all_texts()
        assert any("chennai" in f.lower()
                   for f in fresh.longterm.relevant("where do i live"))

    def test_the_recalled_fact_reaches_the_model(self, build):
        build(ScriptedBrain()).respond("i live in chennai")

        brain = ScriptedBrain("You live in Chennai.")
        build(brain).respond("where do i live")

        assert "Lingaa lives in chennai" in brain.calls[0]

    def test_the_stale_wrong_fact_can_be_corrected(self, build):
        assistant = build(ScriptedBrain())
        assistant.longterm.add("Lingaa lives with Prahadhesvaryaa K S")
        assistant.respond("i live in chennai")

        assistant.respond("i don't live with prahadhesvaryaa")

        assert assistant.longterm.all_texts() == ["Lingaa lives in chennai"]

    def test_a_denial_is_acknowledged(self, build):
        assistant = build(ScriptedBrain())
        assistant.longterm.add("Lingaa lives with Prahadhesvaryaa K S")

        brain = ScriptedBrain("Noted, my mistake.")
        assistant.brain = brain
        assistant.respond("i don't live with prahadhesvaryaa")

        assert "just removed this from your notes" in brain.calls[0]

    def test_moving_replaces_rather_than_confusing(self, build):
        assistant = build(ScriptedBrain())
        assistant.respond("i live in chennai")
        assistant.respond("i moved to coimbatore")

        assert assistant.longterm.all_texts() == ["Lingaa lives in coimbatore"]

    def test_ordinary_chat_stores_nothing(self, build):
        assistant = build(ScriptedBrain())

        for said in ["hey jarvis", "nothing much jarvis", "i am at college today",
                     "ohh how fucked up is this"]:
            assistant.respond(said)

        assert assistant.longterm.all_texts() == []

    def test_a_failure_to_remember_never_breaks_the_turn(self, build,
                                                         monkeypatch):
        """Remembering is a convenience."""
        assistant = build(ScriptedBrain("still fine"))

        def explode(*_args, **_kwargs):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(assistant.longterm, "remember_statement", explode)

        assert assistant.respond("i live in chennai") == "still fine"

    def test_it_works_in_the_streaming_path_too(self, build):
        """Which is the one the browser uses -- and the browser was the interface
        that remembered nothing."""
        assistant = build(ScriptedBrain("Got it."))

        events = list(assistant.respond_stream("i live in chennai"))

        assert any(e.get("text") == "Noted that"
                   for e in events if e["type"] == "status")
        assert "Lingaa lives in chennai" in assistant.longterm.all_texts()

    def test_nothing_is_stored_when_long_term_memory_is_off(self, tmp_path,
                                                           make_config):
        from beastt.assistant import Assistant
        from beastt.config import Config

        config = make_config(name=NAME, user_name=USER,
                         longterm_enabled=False, documents_enabled=False,
                         code_enabled=False, imagegen_enabled=False,
                         bot_status_repo="", data_enabled=False,
                         search_enabled=False, deliberate=False,
                         quotes_enabled=False)
        assistant = Assistant(config=config, brain=ScriptedBrain(), verbose=False)

        assistant.respond("i live in chennai")

        assert assistant.longterm is None


class TestTheWebInterfaceReflects:
    """It called remember_session() exactly never, so every fact a browser
    conversation produced was lost."""

    @pytest.fixture
    def state(self, tmp_path, make_config):
        from beastt.webui.server import _State

        return _State(make_config(longterm_enabled=True,
                                  memory_path=str(tmp_path / "m.json")))

    class _Assistant:
        def __init__(self):
            self.longterm = object()
            self.reflected = 0
            self._reflecting = False

        def remember_session(self):
            self.reflected += 1
            return 1

    def _chat(self, assistant_turns):
        return {"id": "abc", "messages": [{"role": "assistant", "content": "x"}]
                * assistant_turns}

    def test_it_reflects_once_the_interval_is_reached(self, state):
        import time

        assistant = self._Assistant()

        state.reflect_later(self._chat(state.REFLECT_EVERY), assistant)
        time.sleep(0.3)

        assert assistant.reflected == 1

    @pytest.mark.parametrize("turns", [0, 1, 2, 3, 5, 7])
    def test_it_does_not_reflect_on_every_turn(self, state, turns):
        """It costs a model call."""
        import time

        assistant = self._Assistant()

        state.reflect_later(self._chat(turns), assistant)
        time.sleep(0.1)

        assert assistant.reflected == 0

    def test_two_reflections_never_overlap(self, state):
        """They would only fight over the same file."""
        assistant = self._Assistant()
        assistant._reflecting = True

        state.reflect_later(self._chat(state.REFLECT_EVERY), assistant)

        assert assistant.reflected == 0

    def test_nothing_happens_without_long_term_memory(self, state):
        import time

        assistant = self._Assistant()
        assistant.longterm = None

        state.reflect_later(self._chat(state.REFLECT_EVERY), assistant)
        time.sleep(0.1)

        assert assistant.reflected == 0

    def test_the_streaming_handler_actually_calls_it(self):
        """Pins the call site, not the function.

        Every other test here calls `reflect_later` directly, so removing the one
        line that invokes it from the streaming handler broke nothing -- and that
        line *is* the fix. This is the third unpinned call site in this project to
        be found by mutation, after `gather`'s name argument and a stub signature
        that had fallen behind its caller, so it is worth asserting crudely rather
        than not at all.
        """
        import inspect

        from beastt.webui.server import Handler

        source = inspect.getsource(Handler._stream)

        assert "reflect_later" in source, (
            "the streaming handler no longer reflects, so a browser conversation "
            "writes no durable facts -- the original fault")

    def test_a_failing_reflection_is_swallowed(self, state):
        import time


        class _Broken(self._Assistant):
            def remember_session(self):
                raise RuntimeError("model down")

        assistant = _Broken()
        state.reflect_later(self._chat(state.REFLECT_EVERY), assistant)
        time.sleep(0.3)

        assert assistant._reflecting is False
