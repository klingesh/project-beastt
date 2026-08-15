"""Rules about the tests themselves, for mistakes that have now happened twice.

Every check in here exists because something passed on the machine it was written
on and failed on somebody else's -- which is the worst way for a test to be wrong,
because it is reported as a bug in the code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
#: Every test module, plus conftest.
SOURCES = sorted(TESTS.glob("*.py"))


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def bare_config_defaults(source: str):
    """Line numbers of every `Config().field` read in `source`.

    Parsed rather than grepped. The first version of this was a regex and it
    flagged this module's own docstring for *explaining* the mistake it forbids,
    along with every comment describing it -- which is the same category of error
    as the thing being checked: matching text that looks like code.

    `replace(Config(), field=...)` is excluded for free: there, `Config()` is an
    argument, not the object being read from.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        call = node.value
        if (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "Config"
                and not call.args and not call.keywords):
            found.append(node.lineno)
    return sorted(set(found))


class TestNoTestReadsALiveDefault:
    """`Config`'s field defaults are evaluated when the class body runs, from the
    environment. So `Config().anything` reports the *developer's* setting, not the
    default -- and asserting on it produces a test that passes for everyone who
    has not configured that value and fails for everyone who has.

    This has happened three times: the provider API keys leaking into the `config`
    fixture, the same fixture missing `default_model`, and
    `assert Config().wake_console is False` failing on the machine of the person
    who had just been told to switch it on.

    `replace(Config(), field=...)` is fine -- that is setting a value, not reading
    one. `pristine_config` is the fixture for reading a default.
    """

    @pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
    def test_no_module_asserts_on_a_bare_config_default(self, path):
        offenders = bare_config_defaults(path.read_text(encoding="utf-8"))
        assert not offenders, (
            f"{path.name} reads a default out of a live Config on line(s) "
            + ", ".join(str(n) for n in offenders)
            + ", which reports the developer's own .env rather than the default. "
              "Use the `pristine_config` fixture to test a default, or "
              "`replace(Config(), ...)` to set one."
        )

    def test_the_rule_catches_the_original_mistake(self):
        """A guard that cannot fire is not a guard."""
        assert bare_config_defaults("assert Config().wake_console is False")
        assert bare_config_defaults("x = Config().ui_port")
        assert bare_config_defaults("if Config().quotes_enabled:\n    pass")

    def test_the_rule_allows_setting_a_value(self):
        """`replace(Config(), field=...)` is setting, not reading."""
        assert not bare_config_defaults("config = replace(Config(), ui_port=9100)")
        assert not bare_config_defaults("assert pristine_config().ui_port == 8765")
        assert not bare_config_defaults("cfg = Config()\n")

    def test_the_rule_ignores_prose(self):
        """Parsed, not grepped. A regex flagged this module's own docstring for
        explaining the mistake it forbids -- and every comment describing it."""
        assert not bare_config_defaults('"""Never write Config().ui_port here."""')
        assert not bare_config_defaults("# Config().ui_port is wrong\npass")


class TestThePristineConfigFixtureWorks:
    def test_it_ignores_the_ambient_environment(self, pristine_config,
                                               monkeypatch):
        """The whole point: a value set in the environment must not reach it."""
        import os

        os.environ["BEASTT_UI_PORT"] = "9999"
        try:
            # Already reloaded by the fixture, so the class it handed back was
            # built with a scrubbed environment.
            assert pristine_config().ui_port != 9999
        finally:
            del os.environ["BEASTT_UI_PORT"]

    def test_it_reports_documented_defaults(self, pristine_config):
        fresh = pristine_config()
        assert fresh.name == "JARVIS"
        assert fresh.on_wake == "ask"
        assert fresh.wake_console is False
        assert fresh.ui_port == 8765
        assert fresh.model == "llama3.2"

    def test_it_restores_the_real_config_afterwards(self):
        """Run after the fixture has torn down in an earlier test: the module has
        to be the ordinary one again, or every later test sees scrubbed values."""
        from beastt.config import Config

        assert Config is not None
        assert hasattr(Config(), "ui_port")

    def test_no_provider_key_leaks_into_it(self, pristine_config):
        from beastt import providers

        fresh = pristine_config()
        for provider in providers.PROVIDERS:
            if provider.key_field:
                assert getattr(fresh, provider.key_field) == ""


class TestEveryTestModuleIsReachable:
    def test_they_all_start_with_test(self):
        """pytest.ini collects `test_*.py`; a module named otherwise is dead
        weight nobody notices."""
        for path in SOURCES:
            assert path.name == "conftest.py" or path.name.startswith("test_")

    def test_none_are_empty(self):
        for path in SOURCES:
            assert path.stat().st_size > 200, f"{path.name} looks empty"


class TestMarkersAreDeclared:
    def test_known_gap_is_registered(self):
        """pytest.ini uses --strict-markers, so an undeclared marker is an error
        rather than a warning -- but only if it stays declared."""
        ini = (TESTS.parent / "pytest.ini").read_text(encoding="utf-8")
        assert "known_gap" in ini
        assert "--strict-markers" in ini

    def test_every_known_gap_is_also_an_xfail(self):
        """A gap marked but not xfailed would simply fail the build; one xfailed
        without the marker would not show up in `pytest -m known_gap`."""
        for path in SOURCES:
            lines = _lines(path)
            for number, line in enumerate(lines):
                if "@pytest.mark.known_gap" not in line:
                    continue
                window = "\n".join(lines[number:number + 6])
                assert "xfail" in window, (
                    f"{path.name}:{number + 1} is marked known_gap but is not "
                    "xfailed, so it just breaks the build")
