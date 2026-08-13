"""The proactive watcher's alert discipline.

HARDENING_LOG entry 2. `decide()` is a pure function of (previous state, current
health, the clock) precisely so an hour of behaviour can be tested in
milliseconds -- the log records that both bugs in this feature's first day were
hidden by code that could not be run. This is that test.

Nothing here writes the state file: `decide()` takes the previous state as an
argument and returns the next one, so the whole rule set is exercised in memory.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from beastt import botwatch, trading

NOW = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def health(config):
    """Assess a status dict with the test config."""
    def _assess(status):
        return trading.assess(config, status)

    return _assess


@pytest.fixture
def settled(config, status, health):
    """The state after one quiet check of a healthy bot."""
    _messages, state = botwatch.decide(botwatch.WatchState(),
                                       health(status), status, now=NOW)
    return state


def halted(status, reason="max drawdown reached"):
    status.update(halted=True, halt_reason=reason)
    return status


# --- the signature ----------------------------------------------------------
class TestSignature:
    """What counts as "news", collapsed to a comparable string."""

    def test_equity_and_heartbeat_are_excluded(self, status, health):
        """They move on every single check; including them would make every
        check look like news and the watcher would never stop talking."""
        before = botwatch.signature_of(health(status), status)

        status.update(equity=99999.0, balance=98000.0, peak_equity=100000.0,
                      heartbeat="2026-08-13T08:59:30+00:00")

        assert botwatch.signature_of(health(status), status) == before

    def test_a_new_error_changes_it(self, status, health):
        before = botwatch.signature_of(health(status), status)
        status["recent_errors"] = [{"message": "broker connection reset"}]
        assert botwatch.signature_of(health(status), status) != before

    def test_the_state_changes_it(self, status, health):
        before = botwatch.signature_of(health(status), status)
        assert botwatch.signature_of(health(halted(status)), status) != before

    def test_a_lifetime_restart_count_does_not_change_it(self, status, health):
        """The total only rises, so including it would make the signature change
        once and then never again -- and before that, alert forever."""
        before = botwatch.signature_of(health(status), status)
        status["restarts"] = 99
        assert botwatch.signature_of(health(status), status) == before

    def test_crossing_three_quarters_of_the_drawdown_limit_changes_it(
            self, status, health):
        before = botwatch.signature_of(health(status), status)
        status["drawdown_percent"] = 8.0
        assert botwatch.signature_of(health(status), status) != before


# --- alert on change, not on level -----------------------------------------
class TestAlertOnChange:
    def test_a_healthy_first_check_says_nothing(self, status, health):
        messages, state = botwatch.decide(botwatch.WatchState(),
                                          health(status), status, now=NOW)
        assert messages == []
        assert state.state == "running"
        assert state.last_alert == ""

    def test_a_halt_is_announced_when_it_starts(self, settled, status, health):
        messages, state = botwatch.decide(settled, health(halted(status)),
                                          status, now=NOW)

        assert len(messages) == 1
        assert "HALTED" in messages[0]
        assert "max drawdown reached" in messages[0]
        assert state.last_alert == NOW.isoformat()

    def test_it_is_not_announced_again_five_minutes_later(self, settled, status,
                                                         health):
        """The whole point: a notification every five minutes trains its owner to
        dismiss notifications, and then the one that mattered is dismissed too."""
        halted(status)
        _first, state = botwatch.decide(settled, health(status), status, now=NOW)

        messages, later = botwatch.decide(state, health(status), status,
                                          now=NOW + timedelta(minutes=5))

        assert messages == []
        assert later.last_alert == state.last_alert, (
            "the reminder clock must not reset on a silent check")

    def test_a_silent_check_still_records_the_signature(self, settled, status,
                                                        health):
        """So a condition that appears and disappears between checks is not
        reported later as new."""
        status["equity"] = 11111.0
        messages, state = botwatch.decide(settled, health(status), status, now=NOW)
        assert messages == []
        assert state.signature == settled.signature

    def test_only_moving_numbers_is_not_news(self, settled, status, health):
        status.update(equity=12345.0, balance=12000.0)
        assert botwatch.decide(settled, health(status), status, now=NOW)[0] == []

    def test_a_worsening_state_is_announced_even_while_already_bad(
            self, settled, status, health):
        halted(status)
        _msgs, halted_state = botwatch.decide(settled, health(status), status,
                                              now=NOW)

        status.update(halted=False, heartbeat="2026-08-13T07:00:00+00:00")
        messages, _ = botwatch.decide(halted_state, health(status), status,
                                      now=NOW + timedelta(minutes=10))

        assert any("not reporting" in m.lower() for m in messages)


# --- remind slowly while it persists ---------------------------------------
class TestReminder:
    """A kill switch that fires at three in the morning should still be visible
    at breakfast -- hourly, not constantly."""

    def _halted_state(self, settled, status, health):
        halted(status)
        _msgs, state = botwatch.decide(settled, health(status), status, now=NOW)
        return state

    @pytest.mark.parametrize("minutes", [5, 30, 59])
    def test_no_reminder_before_the_interval(self, settled, status, health,
                                            minutes):
        state = self._halted_state(settled, status, health)
        messages, _ = botwatch.decide(state, health(status), status,
                                      now=NOW + timedelta(minutes=minutes))
        assert messages == []

    @pytest.mark.parametrize("minutes", [60, 61, 240])
    def test_a_reminder_once_the_interval_has_passed(self, settled, status,
                                                     health, minutes):
        state = self._halted_state(settled, status, health)

        messages, _ = botwatch.decide(state, health(status), status,
                                      now=NOW + timedelta(minutes=minutes))

        assert len(messages) == 1
        assert messages[0].endswith("(still)")
        assert "HALTED" in messages[0]

    def test_the_reminder_interval_is_configurable(self, settled, status, health):
        state = self._halted_state(settled, status, health)
        soon = NOW + timedelta(minutes=10)

        assert botwatch.decide(state, health(status), status, now=soon,
                               remind_minutes=60)[0] == []
        assert botwatch.decide(state, health(status), status, now=soon,
                               remind_minutes=5)[0] != []

    def test_the_reminder_resets_the_clock(self, settled, status, health):
        state = self._halted_state(settled, status, health)
        later = NOW + timedelta(minutes=61)

        _msgs, after = botwatch.decide(state, health(status), status, now=later)

        assert after.last_alert == later.isoformat()
        # ...so the next reminder is an hour after the reminder, not the original.
        assert botwatch.decide(after, health(status), status,
                               now=later + timedelta(minutes=30))[0] == []

    def test_a_corrupt_timestamp_does_not_raise(self, status, health):
        """The state file is on disk and hand-editable; a bad value must not take
        the watcher down."""
        halted(status)
        state = botwatch.WatchState(
            state="halted",
            signature=botwatch.signature_of(health(status), status),
            last_alert="not-a-timestamp")

        assert botwatch.decide(state, health(status), status, now=NOW)[0] == []

    def test_no_reminder_without_a_previous_alert(self, status, health):
        """Nothing to remind about if nothing was ever said."""
        halted(status)
        state = botwatch.WatchState(
            state="halted",
            signature=botwatch.signature_of(health(status), status),
            last_alert="")

        assert botwatch.decide(state, health(status), status,
                               now=NOW + timedelta(hours=5))[0] == []


# --- say so when it recovers ----------------------------------------------
class TestRecovery:
    """Knowing a problem has cleared is worth as much as knowing it began --
    without it, the last thing you were told is still bad news."""

    @pytest.mark.parametrize("bad", [
        {"halted": True, "halt_reason": "max dd"},
        {"day_halted": True},
    ])
    def test_returning_to_running_is_announced(self, settled, status, health,
                                               bad):
        status.update(bad)
        _msgs, bad_state = botwatch.decide(settled, health(status), status,
                                           now=NOW)

        status.update(halted=False, day_halted=False, new_entries_blocked=False)
        messages, state = botwatch.decide(bad_state, health(status), status,
                                          now=NOW + timedelta(minutes=20))

        assert messages == ["Trading bot is reporting normally again."]
        assert state.state == "running"

    def test_recovery_from_stale_is_announced(self, settled, status, health,
                                              stamp):
        status["heartbeat"] = stamp(60)
        _msgs, stale_state = botwatch.decide(settled, health(status), status,
                                             now=NOW)
        assert stale_state.state == "stale"

        status["heartbeat"] = stamp(2)
        messages, _ = botwatch.decide(stale_state, health(status), status,
                                      now=NOW + timedelta(minutes=10))

        assert "reporting normally again" in messages[0]

    def test_recovery_is_announced_once(self, settled, status, health):
        halted(status)
        _msgs, bad_state = botwatch.decide(settled, health(status), status,
                                           now=NOW)
        status["halted"] = False
        _msgs, recovered = botwatch.decide(bad_state, health(status), status,
                                           now=NOW + timedelta(minutes=10))

        messages, _ = botwatch.decide(recovered, health(status), status,
                                      now=NOW + timedelta(minutes=20))

        assert messages == []

    def test_BAD_states_are_the_ones_worth_interrupting_for(self):
        assert botwatch.BAD == ("halted", "stale", "paused")


# --- lesser concerns, reported once ---------------------------------------
class TestRunningConcerns:
    def test_a_new_error_on_a_running_bot_is_mentioned(self, settled, status,
                                                       health):
        status["recent_errors"] = [{"message": "broker connection reset"}]

        messages, _ = botwatch.decide(settled, health(status), status, now=NOW)

        assert len(messages) == 1
        assert "broker connection reset" in messages[0]

    def test_the_same_error_is_not_mentioned_twice(self, settled, status, health):
        status["recent_errors"] = [{"message": "broker connection reset"}]
        _msgs, state = botwatch.decide(settled, health(status), status, now=NOW)

        messages, _ = botwatch.decide(state, health(status), status,
                                      now=NOW + timedelta(hours=3))

        assert messages == [], "a running bot must not get hourly reminders"

    def test_approaching_the_kill_switch_is_mentioned(self, settled, status,
                                                      health):
        status["drawdown_percent"] = 8.0

        messages, _ = botwatch.decide(settled, health(status), status, now=NOW)

        assert any("three quarters" in m for m in messages)

    def test_a_long_error_message_is_bounded(self, settled, status, health):
        status["recent_errors"] = [{"message": "x" * 500}]
        messages, _ = botwatch.decide(settled, health(status), status, now=NOW)
        assert len(messages[0]) < 250

    def test_a_missing_message_field_does_not_raise(self, settled, status,
                                                    health):
        status["recent_errors"] = [{}]
        botwatch.decide(settled, health(status), status, now=NOW)


# --- the quoting bug ------------------------------------------------------
class TestSafeForToast:
    """notify.toast() builds a PowerShell command by string interpolation, and
    halt reasons come from the bot -- so that text is not under our control.
    A single quote would end the string literal and mangle the notification."""

    def test_quotes_are_stripped(self):
        assert botwatch.safe_for_toast("it's \"broken\"") == "its broken"

    def test_newlines_are_collapsed(self):
        assert botwatch.safe_for_toast("line one\nline two\r\nthree") == (
            "line one line two three")

    def test_length_is_bounded(self):
        assert len(botwatch.safe_for_toast("x" * 5000)) == 220

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_input_is_safe(self, value):
        assert botwatch.safe_for_toast(value) == ""

    def test_a_hostile_halt_reason_cannot_close_the_powershell_string(self):
        hostile = "'; Remove-Item C:\\ -Recurse; '"
        cleaned = botwatch.safe_for_toast(hostile)
        assert "'" not in cleaned
        assert '"' not in cleaned


# --- watcher startup ------------------------------------------------------
class TestStart:
    def test_it_stays_off_unless_alerts_are_enabled(self, config):
        assert botwatch.start(config) is None

    def test_it_stays_off_without_a_bot(self, no_bot):
        from dataclasses import replace
        assert botwatch.start(replace(no_bot, bot_alerts=True)) is None


class TestCheckOnce:
    def test_it_returns_nothing_when_no_bot_is_configured(self, no_bot):
        assert botwatch.check_once(no_bot) == []

    @pytest.fixture
    def isolated(self, monkeypatch):
        """Keep check_once away from the real state file.

        `WatchState.load(path=STATE_FILE)` binds its default at definition time,
        so patching `botwatch.STATE_FILE` does not redirect it -- the call would
        read, and then *write*, the developer's own beastt_memory folder. Patching
        the two methods is what actually isolates it.
        """
        monkeypatch.setattr(botwatch.WatchState, "load",
                            classmethod(lambda cls, *_a, **_k: cls()))
        monkeypatch.setattr(botwatch.WatchState, "save",
                            lambda self, *_a, **_k: None)
        monkeypatch.setattr(botwatch, "_announce", lambda *_a: None)

    def test_an_unreadable_status_is_treated_as_a_stale_heartbeat(
            self, config, monkeypatch, isolated):
        """From the user's point of view, unreadable and dead are the same
        problem -- so it earns one alert rather than being swallowed."""
        def refuse(*_args, **_kwargs):
            raise trading.MonitorError("can't find the repo")

        monkeypatch.setattr(trading, "fetch_status", refuse)

        messages = botwatch.check_once(config)

        assert len(messages) == 1
        assert "not reporting" in messages[0].lower()

    def test_an_unexpected_exception_is_swallowed(self, config, monkeypatch,
                                                  isolated):
        """A watcher that can take down the assistant is worse than no watcher."""
        def explode(*_args, **_kwargs):
            raise RuntimeError("something nobody predicted")

        monkeypatch.setattr(trading, "fetch_status", explode)

        assert botwatch.check_once(config) == []

    def test_a_healthy_bot_produces_no_alerts(self, config, monkeypatch,
                                              status, isolated):
        monkeypatch.setattr(trading, "fetch_status",
                            lambda *_a, **_k: status)
        assert botwatch.check_once(config) == []


class TestWatchState:
    def test_a_missing_file_means_no_history(self, monkeypatch, tmp_path):
        monkeypatch.setattr(botwatch, "STATE_FILE", str(tmp_path / "nope.json"))
        state = botwatch.WatchState.load(str(tmp_path / "nope.json"))
        assert state.state == "unknown"
        assert state.last_alert == ""

    def test_a_corrupt_file_means_no_history(self, tmp_path):
        path = tmp_path / "watch.json"
        path.write_text("{not json at all", encoding="utf-8")
        assert botwatch.WatchState.load(str(path)).state == "unknown"

    def test_unknown_fields_are_ignored(self, tmp_path):
        """So a state file written by a newer version does not crash an older
        one."""
        path = tmp_path / "watch.json"
        path.write_text('{"state": "halted", "invented_field": 1}',
                        encoding="utf-8")
        assert botwatch.WatchState.load(str(path)).state == "halted"

    def test_it_round_trips(self, tmp_path):
        path = str(tmp_path / "nested" / "watch.json")
        original = botwatch.WatchState(state="paused", signature="sig",
                                       last_alert=NOW.isoformat(), since="x")
        original.save(path)
        assert botwatch.WatchState.load(path) == original

    def test_saving_never_raises(self, tmp_path):
        """Remembering is a convenience, not a requirement."""
        botwatch.WatchState().save(str(tmp_path / "\0bad" / "x.json"))
