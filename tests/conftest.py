"""Shared fixtures, and the one import trick the suite needs.

Everything tested here is a pure function: the severity ladder, the alert rules,
the command vetting, the wake-word matcher, the relevance filter. None of them
touch the network. But `beastt.trading` imports `requests` at module scope, so
importing it would fail on a machine that has not installed the dependencies --
and refusing to run the tests until someone pip-installs a networking library in
order to check that "sell 0.04 lots of brent" is refused is the kind of friction
that stops a suite being run at all.

So: if `requests` is genuinely absent, a stub is registered that satisfies the
import and raises if anything actually tries to use it. On a normal install the
real library is found and this does nothing. Either way no test is allowed to
make a request -- the stub would raise, and the tests that exercise fetching
monkeypatch `trading.requests` explicitly.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub_requests() -> None:
    try:
        import requests  # noqa: F401
        return
    except ImportError:
        pass

    module = types.ModuleType("requests")

    def _refuse(*_args, **_kwargs):
        raise AssertionError(
            "A test tried to make a real HTTP request. Nothing in this suite "
            "should: monkeypatch the module under test instead."
        )

    module.get = _refuse
    module.post = _refuse
    module.put = _refuse
    module.request = _refuse

    class RequestException(Exception):
        pass

    module.RequestException = RequestException
    module.exceptions = types.SimpleNamespace(RequestException=RequestException)
    sys.modules["requests"] = module


_stub_requests()


# --- configuration ----------------------------------------------------------
@pytest.fixture
def config(make_config):
    """A Config with a bot configured and everything else quiet.

    Built from `make_config`, so it starts at the *declared* defaults rather than
    from a live `Config()`. That distinction is the whole point: field defaults are
    evaluated when the class body runs, so a real .env leaks into anything built
    with `replace(Config(), ...)` for every field the author did not name.

    This fixture was the first casualty, twice: it missed the five provider API
    keys, so on a machine with a Groq key two checks asserting "a cloud provider
    with no key is unconfigured" failed against a provider that was correctly
    configured -- and then it missed `default_model`. The lesson took a third and
    fourth recurrence to land, in `wake_console` and then in `verify_figures`: the
    mistake is not forgetting a particular setting, it is listing them at all.
    """
    return make_config(
        name="JARVIS",
        user_name="Lingesh",
        # Pinned because a real .env sets these, and `default_model` in
        # particular changes which brain the assistant resolves.
        model="llama3.2",
        default_model="",
        ollama_url="http://localhost:11434",
        request_timeout=300,
        fred_key="",
        bot_status_repo="klingesh/tradingbot-status",
        bot_status_token="test-token",
        bot_status_file="status.json",
        bot_stale_minutes=15,
        bot_remind_minutes=60,
        bot_check_minutes=5,
        bot_alerts=False,
        longterm_enabled=False,
        search_enabled=False,
        documents_enabled=False,
        code_enabled=False,
        shell_enabled=False,
        imagegen_enabled=False,
        images_enabled=False,
        data_enabled=False,
        github_token="",
        github_repo="",
    )


@pytest.fixture(autouse=True)
def _no_env_leak(request):
    """Fail loudly if the `config` fixture ever stops isolating the environment.

    The alternative is what happened the first time: a test that passes for
    everyone except the person with an API key in their .env, reported as a bug
    in the code rather than in the test.
    """
    yield
    if "config" not in request.fixturenames:
        return
    try:
        config = request.getfixturevalue("config")
    except Exception:
        return

    from beastt import providers

    leaked = [p.id for p in providers.PROVIDERS
              if p.key_field and getattr(config, p.key_field, "")]
    assert not leaked, (
        f"the config fixture is carrying real API keys for {leaked} out of the "
        "environment; pin them in conftest.config")


@pytest.fixture(scope="session")
def pristine_values():
    """Every Config field at its declared default, ignoring the environment.

    Captured once by reloading the module with the environment scrubbed, then
    reloading it back before any test runs -- so no test ever sees the reloaded
    class, only this dictionary of values.

    This is the root fix for a bug that has now appeared four times. `Config`'s
    defaults are evaluated when the class body runs, so every fixture built with
    `replace(Config(), ...)` silently inherits whatever the developer's .env says
    for any field the author did not think to name. Each time, the missing field
    was different -- provider keys, then `default_model`, then `wake_console`,
    then `verify_figures` and `quotes_enabled` -- because the mistake is not
    forgetting a particular setting, it is listing them at all.
    """
    import importlib
    import os

    import beastt.config as config_module

    try:
        import dotenv
    except ImportError:
        dotenv = None

    saved = {key: value for key, value in os.environ.items()
             if key.startswith(("BEASTT_", "JARVIS_"))}
    real_loader = getattr(dotenv, "load_dotenv", None) if dotenv else None

    try:
        for key in saved:
            del os.environ[key]
        if real_loader is not None:
            dotenv.load_dotenv = lambda *_a, **_k: False
        fresh = importlib.reload(config_module)
        values = {field: getattr(fresh.Config(), field)
                  for field in fresh.Config.__dataclass_fields__}
    finally:
        if real_loader is not None:
            dotenv.load_dotenv = real_loader
        os.environ.update(saved)
        importlib.reload(config_module)

    return values


@pytest.fixture
def make_config(pristine_values):
    """Build a Config from the declared defaults plus explicit overrides.

    Use this instead of `replace(Config(), ...)` anywhere a test's behaviour
    depends on a setting it did not name. The instance is of the ordinary `Config`
    class -- only the starting values are pristine.
    """
    from beastt.config import Config

    def _make(**overrides):
        unknown = set(overrides) - set(pristine_values)
        assert not unknown, f"no such Config field(s): {sorted(unknown)}"
        return Config(**{**pristine_values, **overrides})

    return _make


@pytest.fixture
def pristine_config():
    """The `Config` class as it would be with no .env and no environment set.

    The only honest way to test a *default*. `Config`'s field defaults are
    evaluated when the class body runs, so a live `Config()` reports whatever the
    developer's own .env says -- and a test asserting `Config().wake_console is
    False` passes for everyone who has not set it and fails for everyone who has.
    Which is exactly what happened: it was green here and red on the machine of
    the person who had been told to turn the setting on.

    That is the third time this class of bug has appeared in this project, after
    the provider keys leaking into the `config` fixture and the same fixture
    missing `default_model`. So it is a shared fixture now, and
    `test_meta.py` fails the build if anyone asserts on a bare default again.

    The environment is saved and restored by hand rather than with monkeypatch,
    because the module has to be reloaded *after* the variables come back and
    fixture teardown ordering does not allow that.
    """
    import importlib
    import os

    import beastt.config as config_module

    try:
        import dotenv
    except ImportError:
        dotenv = None

    saved = {key: value for key, value in os.environ.items()
             if key.startswith(("BEASTT_", "JARVIS_"))}
    real_loader = getattr(dotenv, "load_dotenv", None) if dotenv else None

    try:
        for key in saved:
            del os.environ[key]
        if real_loader is not None:
            # config.py loads the project's .env at import, which would put every
            # variable straight back before the defaults were evaluated.
            dotenv.load_dotenv = lambda *_a, **_k: False
        yield importlib.reload(config_module).Config
    finally:
        if real_loader is not None:
            dotenv.load_dotenv = real_loader
        os.environ.update(saved)
        importlib.reload(config_module)


@pytest.fixture
def no_bot(config):
    """The same config with no trading bot, to prove nothing else changed."""
    from dataclasses import replace

    return replace(config, bot_status_repo="")


# --- the published status ----------------------------------------------------
def _stamp(minutes_ago: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture
def status():
    """A healthy published status, shaped like the real one.

    Modelled on what the bot actually publishes, including the Brent short that
    was open when the monitor was first tested. The heartbeat is deliberately
    four minutes old: the bot writes it every 60s but the publisher only pushes
    every 300s, so a *healthy* bot always looks a few minutes stale from here.
    That is the case the threshold has to survive.
    """
    return {
        "heartbeat": _stamp(4),
        "login": 5039291847,
        "currency": "USD",
        "dry_run": False,
        "halted": False,
        "halt_reason": "",
        "day_halted": False,
        "new_entries_blocked": False,
        "equity": 10248.37,
        "balance": 10195.20,
        "peak_equity": 10402.11,
        "drawdown_percent": 1.48,
        "drawdown_limit_percent": 10.0,
        "day_drawdown_percent": 0.42,
        "day_loss_limit_percent": 4.0,
        "restarts": 2,
        "restarts_last_hour": 0,
        "recent_errors": [],
        "open_positions": [
            {"symbol": "BRENT", "side": "sell", "lots": 0.04,
             "open_price": 71.82, "profit": 2.80},
        ],
    }


@pytest.fixture
def stamp():
    """Build an ISO heartbeat a given number of minutes in the past."""
    return _stamp
