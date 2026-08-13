"""Wake word detection: tolerant enough to be useful, strict enough to be quiet.

Speech-to-text output is fuzzy, so matching is deliberately loose. That makes the
false-positive boundary the interesting part -- a wake word that fires on ordinary
conversation means the assistant starts listening in the middle of someone's day,
which is both annoying and a privacy problem.

The module's own docstring states the boundary it intends to hold:

    Kept high so everyday lookalikes (e.g. "best" vs "beast") don't trigger a
    false wake.

That claim does not currently hold, and the test for it is marked as a known gap
at the bottom of this file.
"""

from __future__ import annotations

import pytest

from beastt import wake


@pytest.fixture
def jarvis():
    return wake.variants_for("jarvis")


@pytest.fixture
def beastt():
    return wake.variants_for("beastt")


# --- waking ----------------------------------------------------------------
class TestDetect:
    @pytest.mark.parametrize("heard", [
        "jarvis", "Jarvis", "JARVIS", "jervis", "javis", "jarvist", "jarviss",
    ])
    def test_known_mishearings_wake_it(self, jarvis, heard):
        woken, _remainder = wake.detect(heard, jarvis)
        assert woken is True

    def test_the_question_after_the_name_is_preserved(self, jarvis):
        """"Jarvis, what's the weather?" both wakes it and asks in one breath."""
        woken, remainder = wake.detect("Jarvis, what's the weather?", jarvis)

        assert woken is True
        assert remainder == "what s the weather"

    def test_calling_the_name_alone_leaves_no_remainder(self, jarvis):
        assert wake.detect("Jarvis", jarvis) == (True, "")

    @pytest.mark.parametrize("filler", ["hey", "hi", "hello", "ok", "okay",
                                        "yo", "hai"])
    def test_leading_filler_is_stripped(self, jarvis, filler):
        woken, remainder = wake.detect(f"{filler} jarvis push it to github",
                                       jarvis)
        assert woken is True
        assert remainder == "push it to github"

    def test_the_name_need_not_come_first(self, jarvis):
        woken, remainder = wake.detect("so anyway jarvis what time is it",
                                       jarvis)
        assert woken is True
        assert remainder == "what time is it"

    def test_a_split_transcription_is_caught(self, jarvis):
        """Whisper sometimes hears the name as two words."""
        woken, remainder = wake.detect("jar vis what time is it", jarvis)

        assert woken is True
        assert remainder == "what time is it"

    def test_punctuation_and_case_are_normalised(self, jarvis):
        assert wake.detect("JARVIS!!!", jarvis)[0] is True

    @pytest.mark.parametrize("heard", ["", "   ", None])
    def test_silence_does_not_wake_it(self, jarvis, heard):
        assert wake.detect(heard, jarvis) == (False, "")


class TestFalsePositives:
    """Everyday speech that must not start a session."""

    @pytest.mark.parametrize("heard", [
        "java is running on port 8080",   # joins to "javais" -- matched strictly
        "the jars are full",
        "harvest festival",
        "let me have a look",
        "what's the weather like today",
        "I need to park the car",
        "service is down",
    ])
    def test_ordinary_speech_is_ignored(self, jarvis, heard):
        assert wake.detect(heard, jarvis) == (False, "")

    def test_joined_pairs_are_matched_strictly(self, jarvis):
        """Fuzzy matching on joined pairs would fire on "java is" -> "javais",
        so that path accepts only an exact match or a known stem."""
        assert wake.detect("java is slow", jarvis)[0] is False

    def test_a_short_token_cannot_wake_it(self, jarvis):
        assert wake.detect("jar", jarvis)[0] is False


class TestVariants:
    def test_a_shipped_name_gets_its_known_mishearings(self):
        assert "jervis" in wake.variants_for("jarvis")
        assert "beasty" in wake.variants_for("beastt")

    def test_an_unknown_name_is_used_as_is(self):
        assert wake.variants_for("orion") == ("orion",)

    def test_extra_spellings_can_be_added(self):
        variants = wake.variants_for("orion", extra=["oryon", "  ORION2 "])
        assert "oryon" in variants
        assert "orion2" in variants

    def test_blank_extras_are_dropped(self):
        assert "" not in wake.variants_for("orion", extra=["", "   "])

    def test_renaming_changes_what_it_answers_to(self):
        """The wake words derive from the configured name, so a rename takes
        effect without a second setting."""
        assert wake.detect("orion hello", wake.variants_for("orion"))[0] is True
        assert wake.detect("jarvis hello", wake.variants_for("orion"))[0] is False

    def test_the_default_is_jarvis(self):
        assert "jarvis" in wake.DEFAULT_WAKE_WORDS


# --- choosing a mode after waking -----------------------------------------
class TestParseModeChoice:
    @pytest.mark.parametrize("heard", [
        "text", "typing", "keyboard", "chat", "let's type", "written", "t", "txt",
    ])
    def test_text_answers(self, heard):
        assert wake.parse_mode_choice(heard) == "text"

    @pytest.mark.parametrize("heard", [
        "voice", "speak", "let's talk", "audio", "say it", "v",
    ])
    def test_voice_answers(self, heard):
        assert wake.parse_mode_choice(heard) == "voice"

    def test_the_question_echoed_back_is_not_an_answer(self):
        """"voice or text?" contains both, so it cannot be resolved -- and
        guessing would pick the wrong one half the time."""
        assert wake.parse_mode_choice("voice or text") is None

    @pytest.mark.parametrize("heard", ["", "   ", None, "yes", "hmm", "whatever"])
    def test_unclear_answers_return_none(self, heard):
        assert wake.parse_mode_choice(heard) is None


# --- known gap ------------------------------------------------------------
class TestKnownGaps:
    @pytest.mark.known_gap
    @pytest.mark.xfail(strict=True, reason=(
        "The module docstring claims _SIMILARITY = 0.85 is 'kept high so "
        "everyday lookalikes (e.g. \"best\" vs \"beast\") don't trigger a false "
        "wake'. It does not: SequenceMatcher('best', 'beast').ratio() is 0.889, "
        "so an assistant named BEASTT wakes on the word 'best' -- and on "
        "'breast'. Raising the ratio to 0.90 fixes both while still matching "
        "every shipped mishearing. Only affects installs that renamed the "
        "assistant to BEASTT; the shipped default of JARVIS is unaffected."))
    @pytest.mark.parametrize("heard", [
        "best", "breast",
    ])
    def test_lookalikes_should_not_wake_a_beastt_assistant(self, beastt, heard):
        assert wake.detect(heard, beastt)[0] is False

    @pytest.mark.known_gap
    @pytest.mark.xfail(strict=True, reason=(
        "_prefixes() accepts any token beginning with the first four characters "
        "of a wake word, with no similarity check at all -- so 'jarvis' also "
        "wakes on 'jarvanicalxyz'. The stem is a deliberate tolerance for "
        "mishearings, but it is unbounded in length."))
    def test_an_arbitrary_word_sharing_a_stem_should_not_wake_it(self, jarvis):
        assert wake.detect("jarvanicalxyz", jarvis)[0] is False
