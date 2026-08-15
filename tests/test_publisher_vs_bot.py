"""Which side went quiet: the bot, or the publisher pushing for it.

From a real morning. The report said:

    Bot: NOT REPORTING — last heartbeat 25.6 hours ago
    Note: Either the bot has stopped, or the publisher on the VPS has.

The bot was fine. It had been evaluating all eight instruments on schedule for
fourteen hours after the last push, and its `status.json` on the VPS was being
rewritten every cycle. Only the publisher had died -- at 10:05:26, one second
after its last successful push.

Worse, the evidence was misread on the way to that conclusion. The argument used
was that the final push (10:05:25Z) carried a heartbeat from 10:05:13Z, twelve
seconds earlier, and that two processes stopping twelve seconds apart meant one
event took both down. That reasoning is worthless: **the last push is always a
successful push, so the heartbeat inside it is always fresh.** A publisher dying
alone leaves exactly the same fingerprint. The inference sent somebody hunting
Event Viewer for a shutdown that never happened.

The discriminator that does work is the age of the last push measured against the
age of the heartbeat inside it -- and it only ever proves one direction:

* pushes continued long after the heartbeat froze -> the bot stopped. Certain,
  because a dead publisher cannot push.
* both stopped together -> the publishing side stopped. Which of publisher, VPS or
  network is not knowable from here, and is not guessed at.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from beastt import botwatch, trading

MINUTE = 60.0
HOUR = 3600.0
#: The default threshold: fifteen minutes, comfortably over the 300s publish
#: interval. Named so the boundary cases read as intent rather than arithmetic.
STALE_AFTER = 15 * MINUTE

#: The real incident, to the second.
INCIDENT_HEARTBEAT = datetime(2026, 8, 14, 10, 5, 13, 268482, tzinfo=timezone.utc)
INCIDENT_PUSH = datetime(2026, 8, 14, 10, 5, 25, tzinfo=timezone.utc)
INCIDENT_NOW = datetime(2026, 8, 15, 11, 41, 13, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_caches():
    trading._cache.clear()
    trading._publish_cache.clear()
    yield
    trading._cache.clear()
    trading._publish_cache.clear()


def verdict(heartbeat_age, publish_age, stale_after=STALE_AFTER):
    return trading.stale_verdict(heartbeat_age, publish_age, stale_after)


class TestWhenThePublisherIsStillWorking:
    """Pushes arriving after the heartbeat froze. The one case that is certain."""

    def test_the_bot_is_named_as_the_one_that_stopped(self):
        state, headline, _notes = verdict(3 * HOUR, 2 * MINUTE)

        assert state == "bot_stopped"
        assert "BOT STOPPED" in headline

    def test_it_says_both_ages_so_the_reasoning_is_visible(self):
        """Someone has to be able to check this conclusion, not just accept it."""
        _state, headline, _notes = verdict(3 * HOUR, 2 * MINUTE)

        assert "3.0 hours ago" in headline
        assert "2 min ago" in headline

    def test_it_stops_hedging(self):
        """The whole point. "Either... or" was true and useless."""
        _state, headline, notes = verdict(3 * HOUR, 2 * MINUTE)

        assert "Either" not in " ".join(notes)
        assert "either" not in headline.lower()

    def test_it_clears_the_vps_and_the_network(self):
        """Deducible and worth saying: a dead publisher cannot push."""
        _state, _headline, notes = verdict(3 * HOUR, 2 * MINUTE)
        text = " ".join(notes)

        assert "VPS is up" in text
        assert "network is fine" in text

    def test_it_names_the_window_to_restart_and_the_one_to_leave(self):
        _state, _headline, notes = verdict(3 * HOUR, 2 * MINUTE)
        text = " ".join(notes)

        assert "Restart the bot" in text
        assert "publisher doesn't need touching" in text

    def test_a_publisher_running_late_still_counts_as_alive(self):
        """Pushes every 300s, and a slow one is not a dead one. What matters is
        that it pushed *after* the bot went quiet, not that it pushed promptly."""
        state, _headline, _notes = verdict(25 * HOUR, 12 * MINUTE)

        assert state == "bot_stopped"


class TestWhenBothWentQuietTogether:
    """The real incident, and the case the old wording got closest to."""

    def test_the_publishing_side_is_named(self):
        state, headline, _notes = verdict(25.8 * HOUR, 25.8 * HOUR - 12)

        assert state == "not_publishing"
        assert "nothing published for" in headline

    def test_the_headline_ages_the_report_not_the_heartbeat(self):
        """"Nothing published for 25.8 hours" is the fact that matters -- every
        number under it is that old, including the equity."""
        _state, headline, _notes = verdict(25.8 * HOUR, 25.8 * HOUR)

        assert "25.8 hours" in headline

    def test_it_does_not_claim_the_bot_stopped(self):
        """The mistake this exists to prevent. The bot was trading normally."""
        state, headline, notes = verdict(25.8 * HOUR, 25.8 * HOUR - 12)
        text = headline + " " + " ".join(notes)

        assert state != "bot_stopped"
        assert "may still be running and trading" in text

    def test_it_does_not_guess_which_of_the_three(self):
        """Publisher crashed, VPS rebooted, network went -- indistinguishable from
        a laptop. Naming one is the same guess as before, pointed the other way."""
        _state, headline, notes = verdict(25.8 * HOUR, 25.8 * HOUR)
        text = (headline + " " + " ".join(notes)).lower()

        assert "reboot" not in text
        assert "event viewer" not in text

    def test_it_says_to_check_the_bot_before_restarting_it(self):
        """A healthy bot four days up should not be restarted to fix a publisher.
        This one had 10 restarts already and was holding nothing."""
        _state, _headline, notes = verdict(25.8 * HOUR, 25.8 * HOUR)
        text = " ".join(notes)

        assert "before restarting it" in text
        assert "only the publisher needs starting" in text

    def test_a_normal_publish_lag_is_not_evidence_of_anything(self):
        """The documented subtlety: the newest published heartbeat is routinely
        minutes old because the bot writes every 60s and the publisher pushes
        every 300s. A five-minute lag must not read as a stopped bot."""
        state, _headline, _notes = verdict(25 * HOUR, 25 * HOUR - 5 * MINUTE)

        assert state == "not_publishing"


class TestTheBoundary:
    def test_a_lag_at_the_threshold_is_not_yet_a_stopped_bot(self):
        state, _h, _n = verdict(HOUR, HOUR - STALE_AFTER)

        assert state == "not_publishing"

    def test_a_lag_past_the_threshold_is(self):
        state, _h, _n = verdict(HOUR, HOUR - STALE_AFTER - 1)

        assert state == "bot_stopped"

    def test_the_threshold_is_the_configured_one(self):
        """Reused rather than invented, so a bot on a slower publish interval
        needs one setting changed and not two."""
        lag_ok = verdict(HOUR, HOUR - 20 * MINUTE, stale_after=30 * MINUTE)
        lag_over = verdict(HOUR, HOUR - 40 * MINUTE, stale_after=30 * MINUTE)

        assert lag_ok[0] == "not_publishing"
        assert lag_over[0] == "bot_stopped"

    def test_a_push_older_than_the_heartbeat_it_carries_does_not_crash(self):
        """Impossible unless the VPS clock and GitHub's disagree. Negative lag
        must land somewhere sensible rather than raising in a status request."""
        state, headline, notes = verdict(HOUR, 2 * HOUR)

        assert state == "not_publishing"
        assert headline and notes


class TestWhenThePublishTimeIsUnknown:
    """No token, no network, an API shape that changed. The report must survive."""

    def test_it_falls_back_to_the_old_honest_wording(self):
        state, headline, notes = verdict(3 * HOUR, None)

        assert state == "stale"
        assert headline == "NOT REPORTING — last heartbeat 3.0 hours ago"
        assert "Either the bot has stopped" in notes[0]

    def test_the_fallback_still_tells_someone_what_to_do(self):
        _state, _headline, notes = verdict(3 * HOUR, None)

        assert "both windows" in " ".join(notes)


class TestTheRealIncident:
    """Driven from the actual timestamps, as a regression on the wrong call."""

    def _ages(self):
        return ((INCIDENT_NOW - INCIDENT_HEARTBEAT).total_seconds(),
                (INCIDENT_NOW - INCIDENT_PUSH).total_seconds())

    def test_the_twelve_second_gap_is_not_read_as_a_dead_bot(self):
        heartbeat_age, publish_age = self._ages()

        state, _headline, _notes = verdict(heartbeat_age, publish_age)

        assert state == "not_publishing"

    def test_it_reproduces_the_age_the_user_was_shown(self):
        heartbeat_age, _publish = self._ages()

        assert trading._ago(heartbeat_age) == "25.6 hours ago"

    def test_the_gap_between_the_two_is_twelve_seconds(self):
        """Pinning the input, so the case cannot quietly stop being this case."""
        heartbeat_age, publish_age = self._ages()

        assert round(heartbeat_age - publish_age, 1) == 11.7


class TestAssessUsesIt:
    def test_the_state_reaches_the_health_object(self, config, status, stamp):
        status["heartbeat"] = stamp(3 * 60)

        health = trading.assess(config, status, publish_age=2 * MINUTE)

        assert health.state == "bot_stopped"

    def test_the_publish_age_is_carried_for_the_reports(self, config, status,
                                                       stamp):
        status["heartbeat"] = stamp(3 * 60)

        health = trading.assess(config, status, publish_age=2 * MINUTE)

        assert health.publish_age == 2 * MINUTE

    def test_without_it_nothing_changes(self, config, status, stamp):
        """Every existing caller passes nothing, and must keep working."""
        status["heartbeat"] = stamp(3 * 60)

        health = trading.assess(config, status)

        assert health.state == "stale"
        assert health.publish_age is None

    def test_a_healthy_heartbeat_is_untouched_by_it(self, config, status):
        health = trading.assess(config, status, publish_age=9 * HOUR)

        assert health.state == "running"

    def test_halted_still_outranks_everything(self, config, status, stamp):
        """The severity ladder is the oldest rule here (log entry 5). A kill
        switch that fired matters more than which process is quiet."""
        status.update(halted=True, halt_reason="max drawdown reached")
        status["heartbeat"] = stamp(3 * 60)

        health = trading.assess(config, status, publish_age=2 * MINUTE)

        assert health.state == "halted"
        assert health.publish_age == 2 * MINUTE


class TestTheReport:
    def test_a_stopped_bot_reads_as_one(self, config, status, stamp):
        status["heartbeat"] = stamp(3 * 60)

        report = trading.summarise(config, status, publish_age=2 * MINUTE)

        assert "BOT STOPPED" in report
        assert "it is the bot itself that has stopped" in report

    @pytest.mark.parametrize("publish_age, expected", [
        (2 * MINUTE, "BOT STOPPED"),
        (3 * HOUR, "nothing published for"),
        (None, "last heartbeat"),
    ])
    def test_no_stale_report_repeats_the_heartbeat_twice(self, config, status,
                                                        stamp, publish_age,
                                                        expected):
        """The headline already carries the age for all three, so the
        "(heartbeat ...)" suffix must be suppressed for all three. It used to be
        keyed on the single state `stale`, so adding states would have quietly
        turned it back on -- hence the SILENT tuple."""
        status["heartbeat"] = stamp(3 * 60)

        report = trading.summarise(config, status, publish_age)

        assert expected in report
        assert "(heartbeat" not in report

    def test_a_healthy_bot_still_shows_its_heartbeat(self, config, status):
        """The other half of that: suppression must not leak into the good case."""
        assert "(heartbeat" in trading.summarise(config, status)

    def test_the_numbers_are_still_reported_while_quiet(self, config, status,
                                                       stamp):
        """A stale report is still the last known state, and that is worth
        reading -- the equity and open positions are what somebody wants."""
        status["heartbeat"] = stamp(3 * 60)

        report = trading.summarise(config, status, publish_age=3 * HOUR)

        assert "Equity" in report and "BRENT" in report

    def test_the_status_line_takes_it_too(self, config, status, stamp):
        status["heartbeat"] = stamp(3 * 60)

        line = trading.one_line(config, status, publish_age=2 * MINUTE)

        assert line.startswith("BOT STOPPED")


class TestTheInterruption:
    """Alerts are interruptions, so each state has to earn distinct wording."""

    def test_a_stopped_bot_is_announced_as_stopped(self, config, status, stamp):
        status["heartbeat"] = stamp(3 * 60)

        messages = trading.alerts(config, status, publish_age=2 * MINUTE)

        assert len(messages) == 1
        assert "STOPPED" in messages[0]
        assert "still publishing" in messages[0]

    def test_a_dead_publisher_is_announced_as_the_publisher(self, config, status,
                                                           stamp):
        status["heartbeat"] = stamp(25 * 60)

        messages = trading.alerts(config, status, publish_age=24.9 * HOUR)

        assert len(messages) == 1
        assert "publisher on the VPS has stopped" in messages[0]
        assert "may still be trading" in messages[0]

    def test_the_publisher_alert_ages_the_report(self, config, status, stamp):
        status["heartbeat"] = stamp(25 * 60)

        message, = trading.alerts(config, status, publish_age=24.9 * HOUR)

        assert "24.9 hours ago" in message

    def test_the_unknown_case_keeps_its_old_alert(self, config, status, stamp):
        status["heartbeat"] = stamp(3 * 60)

        messages = trading.alerts(config, status)

        assert messages == ["Trading bot not reporting — last heartbeat "
                            "3.0 hours ago"]


class TestTheWatcher:
    def test_both_new_states_are_worth_interrupting_for(self):
        """Covered by an invariant in test_botwatch too; asserted here because
        this is the change that introduced them."""
        assert "bot_stopped" in botwatch.BAD
        assert "not_publishing" in botwatch.BAD

    @pytest.mark.parametrize("state, expected", [
        ("bot_stopped", "restart it on the VPS"),
        ("not_publishing", "publisher"),
    ])
    def test_each_gets_its_own_wording(self, status, state, expected):
        health = trading.Health(state, "headline", 3 * HOUR, [], 2 * MINUTE)

        messages = botwatch._describe(health, status)

        assert len(messages) == 1
        assert expected in messages[0]

    def test_the_two_messages_are_not_the_same(self, status):
        """If they read alike, the distinction was pointless."""
        stopped = botwatch._describe(
            trading.Health("bot_stopped", "h", HOUR, [], MINUTE), status)
        quiet = botwatch._describe(
            trading.Health("not_publishing", "h", HOUR, [], HOUR), status)

        assert stopped != quiet

    @pytest.mark.parametrize("state", ["bot_stopped", "not_publishing"])
    def test_the_wording_survives_becoming_a_toast(self, status, state):
        """notify.toast() interpolates into PowerShell, so an apostrophe in the
        text ends the string literal -- log lesson 10. These strings are ours, but
        the check is on the text that ships, not on the author's intentions."""
        message, = botwatch._describe(
            trading.Health(state, "h", HOUR, [], MINUTE), status)

        assert botwatch.safe_for_toast(message) == message

    def test_a_state_change_between_the_two_is_news(self, config, status, stamp):
        """"The publisher died" becoming "the bot died" is a different problem
        with a different fix, so it has to break through the reminder throttle."""
        status["heartbeat"] = stamp(3 * 60)
        quiet = trading.assess(config, status, publish_age=3 * HOUR)
        stopped = trading.assess(config, status, publish_age=2 * MINUTE)

        _first, state = botwatch.decide(botwatch.WatchState(), quiet, status)
        messages, _next = botwatch.decide(state, stopped, status)

        assert messages and "restart it on the VPS" in messages[0]


class TestAskingGitHubOnlyWhenItMatters:
    """One extra request, on the path of every "how's my bot?"."""

    @pytest.fixture
    def counted(self, monkeypatch):
        calls = []
        monkeypatch.setattr(trading, "fetch_publish_age",
                            lambda config, use_cache=True: calls.append(1) or 4.0)
        return calls

    def test_a_healthy_bot_costs_nothing(self, config, status, counted):
        assert trading.publish_age_if_needed(config, status) is None
        assert counted == []

    def test_a_quiet_bot_is_investigated(self, config, status, stamp, counted):
        status["heartbeat"] = stamp(3 * 60)

        assert trading.publish_age_if_needed(config, status) == 4.0
        assert counted == [1]

    def test_an_unreadable_heartbeat_is_not(self, config, status, counted):
        """Nothing to compare a push against, so the request would buy nothing."""
        status["heartbeat"] = "not a timestamp"

        assert trading.publish_age_if_needed(config, status) is None
        assert counted == []

    def test_the_threshold_is_the_same_one_assess_uses(self, config, status,
                                                       stamp, counted):
        """Otherwise a heartbeat could be stale enough to report on and not stale
        enough to explain, which is the worst of both."""
        tight = replace(config, bot_stale_minutes=1)
        status["heartbeat"] = stamp(5)

        assert trading.publish_age_if_needed(config, status) is None
        assert trading.publish_age_if_needed(tight, status) == 4.0

    @pytest.mark.parametrize("age", [0.0, 60.0, 899.0, 900.0, 900.5, 901.0, 3600.0])
    def test_it_asks_exactly_when_the_report_would_need_it(self, config, status,
                                                          monkeypatch, counted,
                                                          age):
        """The pairing, asserted rather than described. `assess` calls a heartbeat
        stale at `> stale_after` and this asked at `<= stale_after`; one of those
        drifting to `<` leaves a state that is reported as stale and never
        explained, and no single-value test notices.

        Driven through `heartbeat_age` because the boundary is a precise second
        and a timestamp built from the clock cannot land on it.
        """
        monkeypatch.setattr(trading, "heartbeat_age", lambda _status: age)

        asked = trading.publish_age_if_needed(config, status) is not None
        needs_explaining = trading.assess(config, status).state in trading.SILENT

        assert asked is needs_explaining
        assert bool(counted) is needs_explaining


class _Commits:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def commit_list(when: datetime):
    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    return [{"sha": "abc123", "commit": {"committer": {"date": stamp}}}]


class TestFetchingThePublishTime:
    @pytest.fixture
    def github(self, monkeypatch):
        """Stand in for the commits API, recording how it was called."""
        seen = []

        def fake_get(url, params=None, headers=None, timeout=None):
            seen.append({"url": url, "params": params, "headers": headers})
            if isinstance(fake_get.reply, Exception):
                raise fake_get.reply
            return fake_get.reply

        fake_get.reply = _Commits(payload=commit_list(
            datetime.now(timezone.utc) - timedelta(minutes=4)))
        fake_get.seen = seen
        monkeypatch.setattr(trading.requests, "get", fake_get, raising=False)
        return fake_get

    def test_it_reports_how_long_ago_the_push_was(self, config, github):
        age = trading.fetch_publish_age(config)

        assert 230 < age < 260          # four minutes, allowing for the clock

    def test_it_asks_about_the_configured_file(self, config, github):
        """Asking about the wrong path would return the repository's newest commit
        instead, which is a different question with a plausible-looking answer."""
        trading.fetch_publish_age(config)

        call, = github.seen
        assert call["url"].endswith("/repos/klingesh/tradingbot-status/commits")
        assert call["params"]["path"] == "status.json"
        assert call["params"]["per_page"] == 1

    def test_it_sends_the_token(self, config, github):
        trading.fetch_publish_age(config)

        call, = github.seen
        assert call["headers"]["Authorization"] == "Bearer test-token"

    def test_what_is_cached_is_an_absolute_time(self, config, github):
        """The bug this whole feature exists to catch, in miniature: a cached
        *age* is a number that was true a minute ago."""
        import time as clock

        age = trading.fetch_publish_age(config)
        _asked_at, published = trading._publish_cache[
            "klingesh/tradingbot-status/status.json"]

        assert published > 1_600_000_000, "an epoch timestamp, not a duration"
        assert abs((clock.time() - published) - age) < 2

    def test_a_cached_answer_still_ages(self, config, github, monkeypatch):
        """Served from cache, and thirty seconds later it must read as thirty
        seconds older. Advanced by less than the 60s cache lifetime, so this
        tests the age arithmetic rather than the expiry."""
        first = trading.fetch_publish_age(config)
        real_time = trading.time.time
        monkeypatch.setattr(trading.time, "time", lambda: real_time() + 30)

        second = trading.fetch_publish_age(config)

        assert len(github.seen) == 1, "the second call was not cached"
        assert 25 < second - first < 35

    def test_the_watcher_can_bypass_the_cache(self, config, github):
        trading.fetch_publish_age(config)
        trading.fetch_publish_age(config, use_cache=False)

        assert len(github.seen) == 2

    def test_a_clock_ahead_of_ours_is_clamped(self, config, github):
        """GitHub's committer date can sit slightly in our future. A negative age
        would read as a push that has not happened yet."""
        github.reply = _Commits(payload=commit_list(
            datetime.now(timezone.utc) + timedelta(minutes=5)))

        assert trading.fetch_publish_age(config) == 0.0

    def test_an_error_reply_is_not_read_even_when_its_body_looks_right(
            self, config, github):
        """The status code is checked before the body, and that is load-bearing
        rather than belt-and-braces: a 4xx whose payload happens to parse would
        yield a confident publish time from a request that failed. Proxies and
        error pages return all sorts of things, and a wrong answer here is worse
        than no answer -- it would name the wrong process to go and restart.
        """
        github.reply = _Commits(403, payload=commit_list(
            datetime.now(timezone.utc)))

        assert trading.fetch_publish_age(config) is None

    @pytest.mark.parametrize("reply, label", [
        (_Commits(403), "refused"),
        (_Commits(404), "missing"),
        (_Commits(500), "server error"),
        (_Commits(payload=[]), "no commits at all"),
        (_Commits(payload=[{"commit": {}}]), "a shape that changed"),
        (_Commits(payload={"message": "Not Found"}), "an error object"),
        (_Commits(200), "unparseable body"),
        (OSError("no route to host"), "no network"),
        (TimeoutError("slow"), "a timeout"),
    ])
    def test_every_failure_is_simply_unknown(self, config, github, reply, label):
        """It must never raise. This runs inside the report someone reaches for
        when things are already broken; a second network call that could take the
        whole thing down would be a poor trade for a sharper sentence.
        """
        github.reply = reply

        assert trading.fetch_publish_age(config) is None, label

    def test_an_unknown_answer_still_produces_a_report(self, config, github,
                                                      status, stamp):
        """The end-to-end consequence of the row above."""
        github.reply = OSError("no route to host")
        status["heartbeat"] = stamp(3 * 60)

        report = trading.summarise(config, status,
                                  trading.publish_age_if_needed(config, status))

        assert "NOT REPORTING" in report
        assert "Either the bot has stopped" in report

    def test_no_token_means_no_request(self, config, github):
        """A read-only token is the documented setup; without one this is not
        worth guessing at, and an unauthenticated call to a private repo would
        404 anyway."""
        naked = replace(config, bot_status_token="", github_token="")

        assert trading.fetch_publish_age(naked) is None
        assert github.seen == []

    def test_no_repo_means_no_request(self, config, github):
        assert trading.fetch_publish_age(
            replace(config, bot_status_repo="")) is None
        assert github.seen == []

    def test_the_cache_is_per_repository(self, config, github):
        """Two installs, or a repo renamed in .env: the answer must not be shared."""
        trading.fetch_publish_age(config)
        trading.fetch_publish_age(replace(config, bot_status_repo="other/repo"))

        assert len(github.seen) == 2


class TestEveryCallerActuallyAsks:
    """Three call sites, driven end to end.

    A caller that quietly stops passing the answer is invisible unless something
    runs it -- and this project has now found three such unpinned call sites
    (`gather`'s name argument, a stub whose signature had fallen behind its
    caller, and the one line in the streaming handler that triggered reflection).
    In each case that line *was* the entire feature. Mutating each of these three
    to pass `None` left the suite green until these tests existed.
    """

    @pytest.fixture
    def quiet_bot(self, monkeypatch, status, stamp):
        """A bot whose heartbeat stopped three hours ago, still being published."""
        status["heartbeat"] = stamp(3 * 60)
        monkeypatch.setattr(trading, "fetch_status",
                            lambda config, use_cache=True: status)
        monkeypatch.setattr(trading, "fetch_publish_age",
                            lambda config, use_cache=True: 2 * MINUTE)
        return status

    def test_the_skill_asks(self, config, quiet_bot):
        """"how's my bot" in a conversation."""
        from beastt.skills.trading_skill import TradingBotSkill

        reply = TradingBotSkill(config).run("how's my bot")

        assert "BOT STOPPED" in reply
        assert "it is the bot itself that has stopped" in reply

    def test_the_watcher_asks(self, config, quiet_bot, monkeypatch):
        """The background check, which is what would wake someone at 2am."""
        monkeypatch.setattr(
            botwatch.WatchState, "load",
            classmethod(lambda cls, *a, **k: botwatch.WatchState()))
        monkeypatch.setattr(botwatch.WatchState, "save", lambda self, *a, **k: None)
        said = []
        monkeypatch.setattr(botwatch, "_announce",
                            lambda cfg, message: said.append(message))

        messages = botwatch.check_once(config)

        assert messages == said
        assert said and "restart it on the VPS" in said[0]

    def test_the_status_screen_asks(self, config, quiet_bot, monkeypatch, capsys):
        """`python main.py --status`, the other place this figure is shown."""
        from beastt import status as status_screen

        monkeypatch.setattr(status_screen.Config, "load",
                            classmethod(lambda cls: config))

        status_screen.report()

        assert "BOT STOPPED" in capsys.readouterr().out


def flat(text: str) -> str:
    """A docstring with its wrapping collapsed.

    So that reflowing a paragraph does not fail a test about its content -- the
    first version of these checks broke on "cannot tell / those apart".
    """
    return " ".join((text or "").split())


class TestTheReasoningIsRecorded:
    def test_the_module_says_why_the_gap_proves_nothing(self):
        """The inference that went wrong is subtle and looked convincing. If the
        refutation is not written down it will be made again."""
        text = flat(trading.stale_verdict.__doc__)

        assert "cannot tell those apart" in text
        assert "a dead publisher cannot push" in text

    def test_it_records_what_it_refuses_to_guess(self):
        text = flat(trading.stale_verdict.__doc__)

        assert "one indistinguishable event" in text
        assert "Event Viewer" in text

    def test_the_fetch_says_why_it_swallows_everything(self):
        text = flat(trading.fetch_publish_age.__doc__)

        assert "Never raises" in text
        assert "enrichment" in text
