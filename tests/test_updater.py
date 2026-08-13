"""What the updaters will and will not overwrite.

HARDENING_LOG lesson 7: "The updater is the lifeline. It must keep working when
everything it updates is broken." Entry 5 records it breaking outright the moment
the repository went private.

There are two of them and they must agree, because they update the same install
by different routes: `update.py` is run by hand from the project folder, and
`beastt/selfupdate.py` is what "update yourself" uses. A file type one syncs and
the other does not is a difference nobody would notice until something was
missing at runtime -- which is how the web assets were left out once, and the
pytest configuration after that.

`_wanted()` is a pure string predicate in both, so this costs nothing to check.
"""

from __future__ import annotations

import pytest

import update
from beastt import selfupdate

#: Both implementations, so every rule is asserted against each.
WANTED = [
    pytest.param(update._wanted, id="update.py"),
    pytest.param(selfupdate._wanted, id="selfupdate.py"),
]


class TestCodeIsSynced:
    @pytest.mark.parametrize("wanted", WANTED)
    @pytest.mark.parametrize("path", [
        "main.py",
        "update.py",
        "beastt/assistant.py",
        "beastt/skills/trading_skill.py",
        "beastt/voice/stt.py",
        "README.md",
        "requirements.txt",
        ".env.example",
        ".gitignore",
    ])
    def test_the_application_itself(self, wanted, path):
        assert wanted(path) is True

    @pytest.mark.parametrize("wanted", WANTED)
    @pytest.mark.parametrize("path", [
        "beastt/webui/static/index.html",
        "beastt/webui/static/app.js",
        "beastt/webui/static/style.css",
    ])
    def test_the_web_assets(self, wanted, path):
        """Omitting these once left the chat interface with no page to serve."""
        assert wanted(path) is True

    @pytest.mark.parametrize("wanted", WANTED)
    @pytest.mark.parametrize("path", [
        "pytest.ini",
        ".github/workflows/tests.yml",
    ])
    def test_the_test_configuration(self, wanted, path):
        """The same omission, one layer along: without pytest.ini the suite
        arrives with nothing telling pytest where to look or which markers
        exist, so `pytest` from the project root tries to collect generated
        projects out of beastt_workspace/ instead."""
        assert wanted(path) is True

    @pytest.mark.parametrize("wanted", WANTED)
    @pytest.mark.parametrize("path", [
        "tests/conftest.py",
        "tests/test_trading_health.py",
        "tests/test_botwatch.py",
    ])
    def test_the_suite_itself(self, wanted, path):
        assert wanted(path) is True


class TestPersonalFilesAreNeverTouched:
    @pytest.mark.parametrize("wanted", WANTED)
    def test_the_env_file(self, wanted):
        """Every key the user owns lives here."""
        assert wanted(".env") is False

    @pytest.mark.parametrize("wanted", WANTED)
    @pytest.mark.parametrize("path", [
        "beastt_memory/memory.json",
        "beastt_memory/chats/a1b2c3.json",
        "beastt_memory/errors.log",
        "beastt_memory/botwatch.json",
        "beastt_memory/version.json",
        "beastt_memory/voiceprint.npz",
    ])
    def test_memory_voiceprint_and_logs(self, wanted, path):
        assert wanted(path) is False

    @pytest.mark.parametrize("wanted", WANTED)
    @pytest.mark.parametrize("path", [
        "beastt_output/My_Deck_20260813_1200.pptx",
        "beastt_workspace/my-project/main.py",
        "beastt_workspace/cloned-repo/setup.py",
        "beastt_workspace/scaffolded/tests/test_core.py",
    ])
    def test_generated_work_and_clones(self, wanted, path):
        """Generated documents, generated code and cloned repositories are the
        user's, not ours. A scaffolded project contains .py files at plausible
        paths, so without this an update could overwrite someone's work."""
        assert wanted(path) is False

    @pytest.mark.parametrize("wanted", WANTED)
    def test_git_internals(self, wanted):
        assert wanted(".git/config") is False


class TestUnwantedTypes:
    @pytest.mark.parametrize("wanted", WANTED)
    @pytest.mark.parametrize("path", [
        "docs/JARVIS_Build_Report.docx",
        "screenshot.png",
        "notes.pdf",
        "archive.zip",
        "binary.exe",
        "model.bin",
    ])
    def test_binaries_and_documents_are_skipped(self, wanted, path):
        """The updater carries source, not payload."""
        assert wanted(path) is False

    @pytest.mark.parametrize("wanted", WANTED)
    def test_a_file_with_no_suffix_is_skipped(self, wanted):
        assert wanted("LICENSE") is False


class TestTheTwoUpdatersAgree:
    """A file type one syncs and the other does not is invisible until something
    is missing at runtime."""

    PATHS = [
        "main.py", "update.py", "beastt/assistant.py", "README.md",
        "requirements.txt", ".env.example", ".gitignore", "pytest.ini",
        ".github/workflows/tests.yml", "tests/conftest.py",
        "beastt/webui/static/app.js", "beastt/webui/static/style.css",
        ".env", "beastt_memory/memory.json", "beastt_output/deck.pptx",
        "beastt_workspace/proj/main.py", "beastt_workspace/proj/tests/test_x.py",
        ".git/config", "jarvis/deck.pptx", "docs/report.docx", "LICENSE",
        "notes.pdf",
    ]

    @pytest.mark.parametrize("path", PATHS)
    def test_the_same_verdict(self, path):
        assert update._wanted(path) is selfupdate._wanted(path), (
            f"the two updaters disagree about {path!r}")

    def test_the_suffix_lists_match(self):
        assert set(update.WANTED_SUFFIXES) == set(selfupdate._KEEP_SUFFIX)

    def test_the_skip_lists_match(self):
        assert set(update.SKIP_PREFIX) == set(selfupdate._SKIP_PREFIX)
        assert update.SKIP_EXACT == selfupdate._SKIP_EXACT


class TestTargetBranch:
    def test_both_track_the_same_repository(self):
        assert (update.OWNER, update.REPO) == (selfupdate.OWNER, selfupdate.REPO)

    def test_both_default_to_the_same_branch(self):
        """A mismatch here would mean "update yourself" and `python update.py`
        quietly install different code."""
        assert update.DEFAULT_BRANCH == selfupdate.BRANCH
