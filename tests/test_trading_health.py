"""The trading monitor's judgement: is it alive, is it halted, how worried to be.

Covers HARDENING_LOG entries 1 and 4. The fixtures are shaped like the real
published status, including the Brent short that was open when this was first
tested, because the bugs both entries describe were about *interpreting* real
numbers rather than parsing malformed ones.
"""

from __future__ import annotations

import base64
import json
from dataclasses import replace

import pytest

from beastt import trading


@pytest.fixture(autouse=True)
def _clear_cache():
    """fetch_status memoises for 60s; a leaked entry would cross-talk."""
    trading._cache.clear()
    yield
    trading._cache.clear()


# --- the severity ladder ----------------------------------------------------
class TestSeverityOrder:
    """Assessed in a fixed order so the worst condition is never masked."""

    def test_healthy_bot_is_running(self, config, status):
        health = trading.assess(config, status)
        assert health.state == "running"
        assert health.headline == "RUNNING"
        assert health.concerns == []

    def test_halt_beats_stale(self, config, status, stamp):
        """The pair you get when a bot kill-switches and the VPS then reboots.

        If staleness were checked first, the halt -- the thing that actually
        needs a person -- would be reported as "not reporting" instead.
        """
        status.update(halted=True, halt_reason="max drawdown reached",
                      heartbeat=stamp(90))

        health = trading.assess(config, status)

        assert health.state == "halted"
        assert "max drawdown reached" in health.headline
        assert "NOT REPORTING" not in health.headline

    def test_halt_beats_paused(self, config, status):
        status.update(halted=True, day_halted=True)
        assert trading.assess(config, status).state == "halted"

    def test_stale_beats_paused(self, config, status, stamp):
        status.update(day_halted=True, heartbeat=stamp(45))
        assert trading.assess(config, status).state == "stale"

    def test_halt_without_a_reason_still_reports(self, config, status):
        status.update(halted=True, halt_reason="")
        health = trading.assess(config, status)
        assert health.state == "halted"
        assert "reason not recorded" in health.headline

    def test_halted_says_it_needs_clearing_by_hand(self, config, status):
        status.update(halted=True, halt_reason="x")
        assert any("by hand" in c for c in trading.assess(config, status).concerns)

    @pytest.mark.parametrize("flag", ["day_halted", "new_entries_blocked"])
    def test_either_daily_limit_flag_pauses(self, config, status, flag):
        status[flag] = True
        health = trading.assess(config, status)
        assert health.state == "paused"
        assert "no new entries" in health.headline


# --- staleness, the subtle one ---------------------------------------------
class TestStaleness:
    """The threshold must exceed the *publisher's* interval, not the bot's poll.

    The bot rewrites its heartbeat every 60s but only publishes every 300s, so a
    perfectly healthy bot legitimately looks five minutes old from here. Set the
    threshold below that and the assistant cries wolf every few minutes -- which
    trains its owner to ignore the one alert that matters.
    """

    @pytest.mark.parametrize("minutes", [0, 1, 4, 5, 6, 10, 14])
    def test_a_healthy_bot_is_never_called_stale(self, config, status, stamp,
                                                 minutes):
        status["heartbeat"] = stamp(minutes)
        assert trading.assess(config, status).state == "running"

    @pytest.mark.parametrize("minutes", [16, 30, 120])
    def test_genuinely_old_heartbeats_are_stale(self, config, status, stamp,
                                                minutes):
        status["heartbeat"] = stamp(minutes)
        health = trading.assess(config, status)
        assert health.state == "stale"
        assert "NOT REPORTING" in health.headline

    def test_stale_names_both_possible_culprits(self, config, status, stamp):
        """The bot and the publisher are separate processes; either can die."""
        status["heartbeat"] = stamp(40)
        concerns = " ".join(trading.assess(config, status).concerns)
        assert "publisher" in concerns
        assert "both" in concerns

    def test_threshold_is_configurable(self, config, status, stamp):
        status["heartbeat"] = stamp(20)
        assert trading.assess(config, status).state == "stale"
        assert trading.assess(replace(config, bot_stale_minutes=30),
                              status).state == "running"

    def test_unreadable_heartbeat_is_a_concern_not_a_headline(self, config,
                                                             status):
        """An unparseable stamp is not evidence the bot is dead, so it must not
        be reported as though it were -- but it must not be silent either."""
        status["heartbeat"] = "not-a-date"

        health = trading.assess(config, status)

        assert health.state == "running"
        assert health.heartbeat_age is None
        assert any("couldn't be read" in c for c in health.concerns)

    @pytest.mark.parametrize("value", ["", None, "not-a-date", "2026-13-45"])
    def test_heartbeat_age_returns_none_rather_than_raising(self, value):
        assert trading.heartbeat_age({"heartbeat": value}) is None

    def test_heartbeat_age_accepts_a_trailing_z(self):
        assert trading.heartbeat_age({"heartbeat": "2020-01-01T00:00:00Z"}) > 0

    def test_a_clock_skewed_future_heartbeat_clamps_to_zero(self, stamp):
        """Never report a negative age; the VPS clock can run ahead."""
        assert trading.heartbeat_age({"heartbeat": stamp(-10)}) == 0.0


# --- the crash-loop warning that could never expire ------------------------
class TestCrashLooping:
    """HARDENING_LOG entry 4, written out as the reported false alarm."""

    def test_the_reported_false_alarm(self, status):
        """Nine restarts, all manual, during one morning's maintenance.

        The first version judged the lifetime counter, which only rises -- so it
        warned about a bot that had been stable for hours, and would have gone on
        warning for the life of the install. A warning that cannot expire is not
        a warning.
        """
        status.update(restarts=9, restarts_last_hour=0)
        assert trading.crash_looping(status) == ""

    @pytest.mark.parametrize("recent,expected", [
        (0, False), (1, False), (2, False), (3, True), (4, True), (12, True),
    ])
    def test_it_fires_on_the_hourly_rate(self, status, recent, expected):
        status["restarts_last_hour"] = recent
        assert bool(trading.crash_looping(status)) is expected
        assert trading.LOOP_THRESHOLD == 3

    def test_an_older_bot_publishing_only_a_total_says_nothing(self, status):
        """Rather than infer a rate, stay quiet: a false alarm every five minutes
        forever is worse than a missed one, and the total is still reported."""
        status["restarts"] = 40
        status.pop("restarts_last_hour")
        assert trading.crash_looping(status) == ""

    def test_a_lifetime_total_alone_never_triggers_a_concern(self, config,
                                                            status):
        status.update(restarts=99, restarts_last_hour=0)
        assert trading.assess(config, status).concerns == []

    def test_the_warning_points_at_the_log(self, status):
        status["restarts_last_hour"] = 5
        assert "bot.log" in trading.crash_looping(status)


# --- lesser concerns -------------------------------------------------------
class TestConcerns:
    def test_drawdown_concern_starts_at_three_quarters_of_the_limit(self, config,
                                                                   status):
        status["drawdown_limit_percent"] = 10.0

        status["drawdown_percent"] = 7.4
        assert trading.assess(config, status).concerns == []

        status["drawdown_percent"] = 7.5
        assert any("three quarters" in c
                   for c in trading.assess(config, status).concerns)

    def test_no_limit_published_means_no_drawdown_concern(self, config, status):
        """Without a limit there is nothing to be three quarters of, and
        dividing by it would invent a warning."""
        status.update(drawdown_percent=99.0, drawdown_limit_percent=0)
        assert trading.assess(config, status).concerns == []

    def test_recent_errors_are_counted(self, config, status):
        status["recent_errors"] = [{"message": "a"}, {"message": "b"}]
        assert any("2 recent error" in c
                   for c in trading.assess(config, status).concerns)

    def test_concerns_do_not_change_the_headline(self, config, status):
        status.update(drawdown_percent=9.9, recent_errors=[{"message": "x"}],
                     restarts_last_hour=9)
        health = trading.assess(config, status)
        assert health.state == "running"
        assert health.headline == "RUNNING"
        assert len(health.concerns) == 3


# --- the human-readable report --------------------------------------------
class TestSummarise:
    def test_it_states_the_read_only_boundary(self, config, status):
        assert ("I can only report on the bot, not change what it does"
                in trading.summarise(config, status))

    def test_lifetime_restarts_read_as_fact_not_warning(self, config, status):
        """Entry 4's other half: the history is worth knowing even when it means
        nothing is wrong now, so it is stated plainly with the hourly count
        beside it and no alarming language."""
        status.update(restarts=9, restarts_last_hour=0)

        report = trading.summarise(config, status)

        assert "Restarts 9 since it was first started, 0 in the last hour" in report
        assert "crash" not in report.lower()
        assert "staying up" not in report

    def test_the_open_position_is_reported_with_its_profit(self, config, status):
        report = trading.summarise(config, status)
        assert "BRENT sell 0.04 lots @ 71.82" in report
        assert "+2.80" in report

    def test_it_does_not_advise_on_the_position(self, config, status):
        """Stating that Brent is short and 2.80 up is a fact. What to do about it
        would be investment advice, which is out of scope by design."""
        report = trading.summarise(config, status).lower()
        for word in ("you should", "i'd recommend", "consider closing", "advise"):
            assert word not in report

    def test_no_positions_says_so_explicitly(self, config, status):
        status["open_positions"] = []
        assert "Open: nothing" in trading.summarise(config, status)

    def test_a_stale_report_does_not_repeat_the_heartbeat(self, config, status,
                                                          stamp):
        status["heartbeat"] = stamp(40)
        first_line = trading.summarise(config, status).splitlines()[0]
        assert "NOT REPORTING" in first_line
        assert first_line.count("heartbeat") == 1

    def test_dry_run_is_distinguished_from_live(self, config, status):
        assert "live orders" in trading.summarise(config, status)
        status["dry_run"] = True
        assert "dry run" in trading.summarise(config, status)

    def test_one_line_fits_the_status_screen(self, config, status):
        line = trading.one_line(config, status)
        assert "\n" not in line
        assert "RUNNING" in line


# --- what earns an interruption -------------------------------------------
class TestAlerts:
    """A report can mention anything; an interruption has to earn it."""

    def test_a_healthy_bot_is_never_announced(self, config, status):
        assert trading.alerts(config, status) == []

    @pytest.mark.parametrize("changes,expected", [
        ({"halted": True, "halt_reason": "max dd"}, "HALTED"),
        ({"day_halted": True}, "paused"),
    ])
    def test_bad_states_alert(self, config, status, changes, expected):
        status.update(changes)
        assert expected in trading.alerts(config, status)[0]

    def test_alerts_share_the_crash_loop_rule(self, config, status):
        """This is how it emerged that alerts() had its own copy of the
        threshold and would have kept the old lifetime-counter behaviour."""
        status.update(restarts=9, restarts_last_hour=0)
        assert trading.alerts(config, status) == []

        status["restarts_last_hour"] = 4
        assert any("restarted 4 times" in a
                   for a in trading.alerts(config, status))

    def test_only_the_most_recent_error_is_raised(self, config, status):
        status["recent_errors"] = [{"message": "old"}, {"message": "newest"}]
        joined = " ".join(trading.alerts(config, status))
        assert "newest" in joined
        assert "old" not in joined


# --- wording ---------------------------------------------------------------
class TestAgo:
    @pytest.mark.parametrize("seconds,expected", [
        (10, "10s ago"), (89, "89s ago"), (90, "1 min ago"),
        (600, "10 min ago"), (5399, "89 min ago"), (5400, "1.5 hours ago"),
    ])
    def test_readable_ages(self, seconds, expected):
        assert trading._ago(seconds) == expected

    def test_unknown_age_is_admitted(self):
        assert trading._ago(None) == "at an unknown time"


class TestNumberCoercion:
    @pytest.mark.parametrize("value,expected", [
        (None, 0.0), ("", 0.0), ("abc", 0.0), (5, 5.0), ("7.5", 7.5),
    ])
    def test_a_missing_or_junk_field_reads_as_zero(self, value, expected):
        assert trading._number(value) == expected


# --- configuration and fetching -------------------------------------------
class TestConfiguration:
    @pytest.mark.parametrize("repo,expected", [
        ("klingesh/tradingbot-status", True), ("", False), ("   ", False),
    ])
    def test_configured(self, config, repo, expected):
        assert trading.configured(replace(config, bot_status_repo=repo)) is expected

    def test_a_dedicated_read_only_token_is_preferred(self, config):
        both = replace(config, bot_status_token="dedicated", github_token="general")
        assert trading.token_for(both) == "dedicated"

    def test_it_falls_back_to_the_general_token(self, config):
        assert trading.token_for(
            replace(config, bot_status_token="", github_token="general")
        ) == "general"


class _Response:
    def __init__(self, status_code=200, payload=None, raw=None):
        self.status_code = status_code
        self._payload = payload
        if raw is not None:
            self._payload = {"content": base64.b64encode(raw).decode()}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class TestFetchStatus:
    def _patch(self, monkeypatch, response, counter=None):
        def fake_get(url, headers=None, timeout=None):
            if counter is not None:
                counter.append(url)
            if isinstance(response, Exception):
                raise response
            return response

        monkeypatch.setattr(trading.requests, "get", fake_get, raising=False)

    def test_it_reads_the_published_json(self, config, status, monkeypatch):
        self._patch(monkeypatch,
                    _Response(raw=json.dumps(status).encode()))
        assert trading.fetch_status(config)["login"] == status["login"]

    def test_a_404_names_both_of_its_possible_causes(self, config, monkeypatch):
        """GitHub returns 404 rather than 403 for a private repo a token cannot
        see, so a wrong name and a missing permission are indistinguishable from
        here. Guessing one sends someone debugging the wrong thing.
        """
        self._patch(monkeypatch, _Response(404))

        with pytest.raises(trading.MonitorError) as err:
            trading.fetch_status(config)

        message = str(err.value)
        assert "the name is wrong" in message
        assert "doesn't have access" in message

    @pytest.mark.parametrize("code", [401, 403])
    def test_a_refused_token_says_so(self, config, monkeypatch, code):
        self._patch(monkeypatch, _Response(code))
        with pytest.raises(trading.MonitorError, match="refused the token"):
            trading.fetch_status(config)

    def test_other_http_errors_report_their_status(self, config, monkeypatch):
        self._patch(monkeypatch, _Response(500))
        with pytest.raises(trading.MonitorError, match="HTTP 500"):
            trading.fetch_status(config)

    def test_unreachable_github_is_reported_not_raised_raw(self, config,
                                                           monkeypatch):
        self._patch(monkeypatch, OSError("no route to host"))
        with pytest.raises(trading.MonitorError, match="couldn't reach GitHub"):
            trading.fetch_status(config)

    def test_unparseable_content_is_reported(self, config, monkeypatch):
        self._patch(monkeypatch, _Response(raw=b"{not json"))
        with pytest.raises(trading.MonitorError, match="wasn't readable JSON"):
            trading.fetch_status(config)

    def test_a_json_list_is_the_wrong_shape(self, config, monkeypatch):
        self._patch(monkeypatch, _Response(raw=b"[1, 2, 3]"))
        with pytest.raises(trading.MonitorError, match="wasn't the expected shape"):
            trading.fetch_status(config)

    def test_no_repo_configured_explains_the_setting(self, config):
        with pytest.raises(trading.MonitorError, match="BEASTT_BOT_STATUS_REPO"):
            trading.fetch_status(replace(config, bot_status_repo=""))

    def test_no_token_explains_the_setting(self, config):
        with pytest.raises(trading.MonitorError, match="BEASTT_BOT_STATUS_TOKEN"):
            trading.fetch_status(
                replace(config, bot_status_token="", github_token=""))

    def test_repeated_questions_are_served_from_cache(self, config, status,
                                                      monkeypatch):
        """Cached well under the publish interval, so it never hides fresh news
        but a conversation cannot hammer the API."""
        calls = []
        self._patch(monkeypatch, _Response(raw=json.dumps(status).encode()),
                    counter=calls)

        trading.fetch_status(config)
        trading.fetch_status(config)

        assert len(calls) == 1

    def test_the_watcher_can_bypass_the_cache(self, config, status, monkeypatch):
        calls = []
        self._patch(monkeypatch, _Response(raw=json.dumps(status).encode()),
                    counter=calls)

        trading.fetch_status(config)
        trading.fetch_status(config, use_cache=False)

        assert len(calls) == 2
