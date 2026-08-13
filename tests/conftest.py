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
def config():
    """A Config with a bot configured and everything else quiet.

    `Config`'s field defaults are read from the environment at import time, so a
    developer's own .env would otherwise leak into the tests. Every field the
    tests care about is set explicitly here.
    """
    from dataclasses import replace

    from beastt.config import Config

    return replace(
        Config(),
        name="JARVIS",
        user_name="Lingesh",
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
    )


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
