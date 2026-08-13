"""The small pure functions the rest of the system trusts.

Model-id parsing, path confinement, the directive heuristic, contrast safety and
chat-id sanitisation. None of them are more than twenty lines, and all of them are
load-bearing: three are the only thing standing between a model's suggestion and
the filesystem.
"""

from __future__ import annotations

import pytest

from beastt import code, providers, theme
from beastt.skills import intent
from beastt.webui import chats


# --- provider:model ids ----------------------------------------------------
class TestSplitModelId:
    @pytest.mark.parametrize("model_id,expected", [
        ("groq:llama-3.3", ("groq", "llama-3.3")),
        ("openai:gpt-4o", ("openai", "gpt-4o")),
        ("cerebras:llama3.1-8b", ("cerebras", "llama3.1-8b")),
        ("mistral:mistral-small", ("mistral", "mistral-small")),
        ("openrouter:qwen/qwen3:free", ("openrouter", "qwen/qwen3:free")),
    ])
    def test_a_known_provider_is_split_off(self, model_id, expected):
        assert providers.split_model_id(model_id) == expected

    def test_only_the_first_colon_splits(self):
        """So an Ollama tag survives the round trip."""
        assert providers.split_model_id("ollama:llama3.1:8b") == (
            "ollama", "llama3.1:8b")

    def test_a_bare_name_is_a_local_model(self):
        """Which keeps older BEASTT_MODEL values working."""
        assert providers.split_model_id("llama3.2") == ("ollama", "llama3.2")

    def test_a_bare_name_with_a_tag_is_a_local_model(self):
        assert providers.split_model_id("llama3.1:8b") == ("ollama", "llama3.1:8b")

    def test_an_unknown_prefix_belongs_to_the_model_name(self):
        """A colon in a name is far more likely than a provider nobody has heard
        of, and guessing the other way would silently drop half the name."""
        assert providers.split_model_id("nonsense:thing") == (
            "ollama", "nonsense:thing")

    @pytest.mark.parametrize("model_id", ["", "   ", None])
    def test_nothing_in_nothing_out(self, model_id):
        assert providers.split_model_id(model_id) == ("", "")

    def test_surrounding_whitespace_is_ignored(self):
        assert providers.split_model_id("  groq:llama-3.3  ") == (
            "groq", "llama-3.3")


class TestProviderRegistry:
    def test_every_provider_has_what_the_picker_needs(self):
        for provider in providers.PROVIDERS:
            assert provider.id
            assert provider.label
            if provider.id != "ollama":
                assert provider.env_var, f"{provider.id} needs an env var to name"
                assert provider.signup, f"{provider.id} needs a signup URL"

    def test_ollama_is_the_local_one(self):
        assert providers.get("ollama").is_local is True

    def test_cloud_providers_are_not_local(self):
        for provider in providers.PROVIDERS:
            if provider.id != "ollama":
                assert provider.is_local is False

    def test_a_cloud_provider_is_unconfigured_without_a_key(self, config):
        assert providers.is_configured(config, providers.get("groq")) is False

    def test_a_key_configures_it(self, config):
        from dataclasses import replace
        assert providers.is_configured(
            replace(config, groq_key="sk-test"), providers.get("groq")) is True

    def test_the_local_provider_needs_no_key(self, config):
        """Local is always usable."""
        assert providers.is_configured(config, providers.get("ollama")) is True

    def test_an_unknown_provider_is_none(self):
        assert providers.get("github-models-which-was-removed") is None

    def test_build_refuses_an_empty_id(self, config):
        """It returns None rather than raising, so the caller chooses between
        falling back and reporting."""
        assert providers.build(config, "") is None

    def test_build_refuses_a_provider_with_no_model_named(self, config):
        assert providers.build(config, "groq:") is None

    def test_an_unknown_prefix_is_built_as_a_local_model(self, config):
        """Consistent with split_model_id: the colon belonged to the name, so
        this is a local model called "nonsense:thing", not a failure."""
        brain = providers.build(config, "nonsense:thing")
        assert brain is not None
        assert brain.model == "nonsense:thing"

    def test_build_refuses_a_cloud_model_with_no_key(self, config):
        assert providers.build(config, "groq:llama-3.3") is None

    def test_default_model_id_prefers_the_explicit_setting(self, config):
        from dataclasses import replace
        assert providers.default_model_id(
            replace(config, default_model="groq:llama-3.3")) == "groq:llama-3.3"

    def test_default_model_id_falls_back_to_the_local_model(self, config):
        from dataclasses import replace
        resolved = providers.default_model_id(
            replace(config, default_model="", model="llama3.2"))
        assert providers.split_model_id(resolved) == ("ollama", "llama3.2")


# --- keeping generated files inside the workspace -------------------------
class TestSafeRelpath:
    """A model suggests the filename, so it must not be able to choose the
    location."""

    @pytest.mark.parametrize("name,expected", [
        ("notes.py", "notes.py"),
        ("./ok/name.py", "ok/name.py"),
        ("sub/dir/file.py", "sub/dir/file.py"),
    ])
    def test_ordinary_names_pass_through(self, name, expected):
        assert code._safe_relpath(name, "default.py") == expected

    @pytest.mark.parametrize("name", [
        "../../etc/passwd",
        "../secrets.py",
        "a/../../b.py",
        "./../../../root/.ssh/id_rsa",
    ])
    def test_parent_segments_are_dropped(self, name):
        result = code._safe_relpath(name, "default.py")
        assert ".." not in result.split("/")

    def test_an_absolute_posix_path_becomes_relative(self):
        assert code._safe_relpath("/etc/passwd", "default.py") == "etc/passwd"

    def test_a_windows_path_is_defanged(self):
        assert code._safe_relpath("C:\\Windows\\evil.py", "default.py") == (
            "C_/Windows/evil.py")

    def test_a_unc_path_is_defanged(self):
        result = code._safe_relpath("\\\\server\\share\\x.py", "default.py")
        assert not result.startswith("/")
        assert not result.startswith("\\")

    @pytest.mark.parametrize("name", ["", "   ", "..", ".", "/", "../..", None])
    def test_nothing_usable_falls_back_to_the_default(self, name):
        assert code._safe_relpath(name, "default.py") == "default.py"

    def test_shell_characters_are_replaced(self):
        assert code._safe_relpath("a;rm -rf b.py", "d.py") == "a_rm_-rf_b.py"

    def test_the_result_is_always_relative(self):
        for name in ["/a", "//a", "C:/a", "\\a", "../a"]:
            assert not code._safe_relpath(name, "d.py").startswith("/")

    def test_the_workspace_is_inside_the_project(self):
        from beastt.paths import project_root
        assert project_root() in code.workspace().parents or (
            code.workspace().parent == project_root())


# --- an instruction versus a quotation ------------------------------------
class TestDirective:
    """The pasted-DaVinci-Resolve-spec bug: a four-thousand-character document
    containing "I can generate a package" scaffolded a Python package called
    "i-can-generate", because the words were there."""

    import re as _re
    PATTERN = _re.compile(r"generate a (\w+)")

    def test_a_short_request_is_taken_at_face_value(self):
        assert intent.directive(self.PATTERN, "generate a package") is not None

    def test_a_request_at_the_front_of_a_long_message_still_counts(self):
        text = "generate a package for me, here are the details: " + "x" * 900
        assert intent.directive(self.PATTERN, text) is not None

    def test_a_match_buried_in_a_long_document_does_not(self):
        text = "y" * 300 + " generate a package " + "z" * 300
        assert len(text) > intent.LONG_MESSAGE
        assert intent.directive(self.PATTERN, text) is None

    def test_the_boundary(self):
        """Short messages are always trusted; only once a message is long enough
        to be a document does position start to matter."""
        filler = "y" * (intent.LONG_MESSAGE - len("generate a package"))
        just_short = filler + "generate a package"
        assert len(just_short) == intent.LONG_MESSAGE
        assert intent.directive(self.PATTERN, just_short) is not None

        assert intent.directive(self.PATTERN, just_short + "!") is None

    def test_room_is_allowed_for_a_greeting(self):
        text = "hey Jarvis, please generate a package for me. " + "x" * 900
        assert self.PATTERN.search(text).start() <= intent.HEAD
        assert intent.directive(self.PATTERN, text) is not None

    def test_no_match_is_not_a_directive(self):
        assert intent.directive(self.PATTERN, "hello there") is None

    @pytest.mark.parametrize("text", ["", None])
    def test_empty_input(self, text):
        assert intent.directive(self.PATTERN, text) is None

    def test_is_directive_rejects_a_missing_match(self):
        assert intent.is_directive("anything", None) is False


class TestPlausibleName:
    @pytest.mark.parametrize("words", [
        ["notes", "api"], ["my-project"], ["invoice", "tracker"],
    ])
    def test_real_names_are_accepted(self, words):
        assert intent.plausible_name(words) is True

    @pytest.mark.parametrize("words", [
        ["i", "can", "generate"],
        ["the", "following"],
        ["please", "make"],
        ["a"],
        [],
        None,
    ])
    def test_prose_is_rejected(self, words):
        assert intent.plausible_name(words) is False

    def test_opening_filler_is_disqualifying(self):
        assert intent.plausible_name(["the", "invoice", "tracker"]) is False

    def test_mostly_filler_is_prose_however_it_starts(self):
        assert intent.plausible_name(["tracker", "for", "the", "of"]) is False


# --- contrast safety ------------------------------------------------------
class TestTheme:
    def test_white_gets_dark_text(self):
        assert theme.DEFAULT.on("FFFFFF") == theme.DEFAULT.text_dark

    def test_deep_navy_gets_white_text(self):
        assert theme.DEFAULT.on("0B2545") == theme.DEFAULT.white

    def test_a_pale_model_chosen_primary_stays_readable(self):
        """The reason this exists: the model may pick a pale primary colour,
        where white text would be unreadable."""
        pale = theme.Theme(primary="FFF9C4")
        assert pale.on_primary() == pale.text_dark

    @pytest.mark.parametrize("hex_colour,expected", [
        ("FFFFFF", 1.0), ("000000", 0.0),
    ])
    def test_luminance_endpoints(self, hex_colour, expected):
        assert theme.Theme._luminance(hex_colour) == pytest.approx(expected,
                                                                  abs=1e-6)

    @pytest.mark.parametrize("bad", ["", "xyz", "not-a-colour", None])
    def test_malformed_hex_does_not_raise(self, bad):
        """A model-chosen colour reaches this, so it must degrade rather than
        take the whole render down."""
        assert theme.Theme._luminance(bad) == 0.0

    def test_every_palette_is_legible_on_its_own_primary(self):
        for name, palette in theme.PALETTES.items():
            assert palette.on_primary() in (palette.text_dark, palette.white), name

    def test_an_unknown_palette_falls_back_to_the_default(self):
        assert theme.get("chartreuse") is theme.DEFAULT
        assert theme.get("") is theme.DEFAULT
        assert theme.get(None) is theme.DEFAULT

    def test_palette_names_are_case_insensitive(self):
        assert theme.get("  PLUM  ") is theme.PALETTES["plum"]


class TestFromDesign:
    def test_a_named_palette_is_honoured(self):
        assert theme.from_design({"palette": "ember"}).primary == (
            theme.PALETTES["ember"].primary)

    def test_valid_hex_is_accepted_with_or_without_a_hash(self):
        assert theme.from_design({"accent": "#123ABC"}).accent == "123ABC"
        assert theme.from_design({"primary": "123abc"}).primary == "123ABC"

    def test_invalid_hex_falls_back_per_field(self):
        """So one bad colour does not discard a design that was otherwise fine."""
        built = theme.from_design({"palette": "ember", "primary": "not-a-hex",
                                   "accent": "#123ABC"})

        assert built.primary == theme.PALETTES["ember"].primary
        assert built.accent == "123ABC"

    def test_only_safe_fonts_are_accepted(self):
        """An exotic font name renders as a fallback on someone else's machine."""
        assert theme.from_design({"heading_font": "Comic Sans MS"}).heading_font == (
            theme.DEFAULT.heading_font)

    def test_the_rationale_is_bounded(self):
        assert len(theme.from_design({"rationale": "x" * 900}).rationale) == 200

    @pytest.mark.parametrize("design", [None, {}, "not a dict", []])
    def test_junk_designs_fall_back(self, design):
        assert theme.from_design(design).primary == theme.DEFAULT.primary

    def test_describe_names_the_colours(self):
        described = theme.describe(theme.PALETTES["navy"])
        assert "#0B2545" in described


class TestProseFonts:
    def test_a_report_is_pinned_to_a_serif(self):
        assert theme.prose_fonts(True, "SF Pro Text") == ("Times New Roman",
                                                          "Times New Roman")

    def test_a_non_report_may_use_the_sans(self):
        assert theme.prose_fonts(False, "sf pro") == ("SF Pro Text", "SF Pro Text")

    def test_the_default_is_the_serif(self):
        assert theme.prose_fonts(False, "") == ("Times New Roman",
                                                 "Times New Roman")


# --- chat ids -------------------------------------------------------------
class TestSafeChatId:
    """Ids are generated locally, but one arriving from a request is never
    trusted -- otherwise `_path()` would traverse."""

    def test_a_normal_id_is_unchanged(self):
        assert chats._safe_id("a1b2c3d4e5f6") == "a1b2c3d4e5f6"

    def test_hyphens_and_underscores_survive(self):
        assert chats._safe_id("my-chat_2") == "my-chat_2"

    @pytest.mark.parametrize("hostile", [
        "../../etc/passwd", "..\\..\\windows", "/absolute/path",
        "a/b/c", "a;rm -rf b", "chat.json", "..",
    ])
    def test_path_separators_and_dots_are_removed(self, hostile):
        cleaned = chats._safe_id(hostile)
        for char in "/\\.;: ":
            assert char not in cleaned

    def test_length_is_bounded(self):
        assert len(chats._safe_id("a" * 500)) == 40

    def test_a_sanitised_id_cannot_escape_the_chats_folder(self):
        target = chats._path("../../etc/passwd")
        assert target.parent == chats.chats_dir()
