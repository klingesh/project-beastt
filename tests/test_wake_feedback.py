"""Being called by name has to produce something you can notice.

Reported: "jarvis is running in background and records voices but it didn't
respond -- if i say jarvis it stays silent. i want to know whether jarvis heard me
or not."

The service was doing something on wake, just nothing that reached the user. It
printed to a log file nobody was watching, played a single beep, and then asked
whether to talk by voice or by text. So calling out and getting silence was
indistinguishable from three different situations:

  * the wake word was never matched (Whisper's small standby model mishearing),
  * speaker verification rejected the voice,
  * or it did wake, and the beep was missed.

A beep confirms that *something* happened. Being greeted **by name** confirms that
the name was heard and that it was recognised as this particular person, which is
the question actually being asked.

The second half is where the greeting leads: straight into the interface, which is
where the work happens anyway. A window appearing is feedback that cannot be
missed even with the speakers muted.

Nothing here opens a socket, starts a process or speaks: the launcher's decisions
are exercised with its side effects stubbed.
"""

from __future__ import annotations

import socket
import threading

import pytest

from beastt import personality, uilaunch


class TestTheSpokenAcknowledgement:
    def test_it_uses_the_users_name(self):
        """The point of it. "I'm listening" says something was heard; "I'm
        listening, Lingaa" says *who* was heard."""
        for _ in range(20):
            assert "Lingaa" in personality.wake_greeting("Lingaa")

    def test_it_is_short(self):
        """An acknowledgement, not the start of a conversation. Anything long
        gets talked over."""
        for _ in range(20):
            greeting = personality.wake_greeting("Lingaa")
            assert len(greeting) < 60
            assert greeting.count(".") + greeting.count("?") <= 2

    def test_it_varies(self):
        """The same six words every time stops being information."""
        seen = {personality.wake_greeting("Lingaa") for _ in range(60)}
        assert len(seen) > 1

    def test_it_copes_with_no_name_configured(self):
        greeting = personality.wake_greeting("")
        assert greeting
        assert "{user}" not in greeting

    def test_it_is_not_the_startup_greeting(self):
        """`welcome_message` opens a session and asks what you want; this only
        confirms it heard you. Reusing one for the other is how the first spoken
        words on wake came to be a question about voice or text."""
        wake = {personality.wake_greeting("Lingaa") for _ in range(60)}
        welcome = {personality.welcome_message("Lingaa") for _ in range(60)}
        assert not (wake & welcome)


class TestIsRunning:
    """A socket connect, not a PID file: the file outlives the process, and a
    live process says nothing about whether its socket is accepting yet."""

    def test_it_finds_a_listening_port(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        threading.Thread(target=lambda: None, daemon=True).start()
        try:
            assert uilaunch.is_running(port) is True
        finally:
            server.close()

    def test_it_reports_a_closed_port(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        server.close()

        assert uilaunch.is_running(port, timeout=0.2) is False

    def test_a_nonsense_port_does_not_raise(self):
        assert uilaunch.is_running(0, timeout=0.2) in (True, False)


class TestUrl:
    def test_it_is_loopback_only(self):
        """The interface has no authentication, so the address it is advertised
        at must never be a routable one."""
        assert uilaunch.url_for(8765) == "http://127.0.0.1:8765"
        assert "0.0.0.0" not in uilaunch.url_for(8765)

    def test_the_port_is_honoured(self):
        assert ":9001" in uilaunch.url_for(9001)


class TestEnsure:
    @pytest.fixture
    def stub(self, monkeypatch):
        """Replace every side effect and record what was attempted."""
        calls = {"started": 0, "opened": 0, "running": False}

        def fake_is_running(port=uilaunch.DEFAULT_PORT, timeout=0.0):
            return calls["running"]

        def fake_start(port=uilaunch.DEFAULT_PORT, timeout=0.0):
            calls["started"] += 1
            calls["running"] = calls.get("start_succeeds", True)
            return calls["running"]

        def fake_open(port=uilaunch.DEFAULT_PORT):
            calls["opened"] += 1
            return calls.get("browser_opens", True)

        monkeypatch.setattr(uilaunch, "is_running", fake_is_running)
        monkeypatch.setattr(uilaunch, "start", fake_start)
        monkeypatch.setattr(uilaunch, "open_in_browser", fake_open)
        return calls

    def test_an_already_running_interface_is_just_brought_forward(self, stub):
        stub["running"] = True

        ok, spoken = uilaunch.ensure(8765)

        assert ok is True
        assert stub["started"] == 0, "starting a second copy would fail to bind"
        assert stub["opened"] == 1
        assert "already open" in spoken

    def test_a_stopped_interface_is_started_then_opened(self, stub):
        ok, spoken = uilaunch.ensure(8765)

        assert ok is True
        assert stub["started"] == 1
        assert stub["opened"] == 1
        assert "opened the chat" in spoken

    def test_a_failure_to_start_is_admitted_with_what_to_try(self, stub):
        stub["start_succeeds"] = False

        ok, spoken = uilaunch.ensure(8765)

        assert ok is False
        assert stub["opened"] == 0
        assert "couldn't" in spoken.lower()
        assert "main.py --ui" in spoken

    def test_a_browser_that_will_not_open_still_gives_the_address(self, stub):
        """Spoken aloud, so it says the port rather than showing a URL."""
        stub["browser_opens"] = False

        ok, spoken = uilaunch.ensure(9000)

        assert ok is True
        assert "9000" in spoken

    def test_progress_is_reported(self, stub):
        steps = []

        uilaunch.ensure(8765, on_step=steps.append)

        assert steps
        assert any("8765" in s for s in steps)

    def test_a_broken_progress_callback_is_not_fatal(self, stub):
        def explode(_message):
            raise RuntimeError("nope")

        ok, _spoken = uilaunch.ensure(8765, on_step=explode)

        assert ok is True

    @pytest.mark.parametrize("port", [None, 0, ""])
    def test_a_missing_port_falls_back_to_the_default(self, stub, port):
        uilaunch.ensure(port)
        assert True     # it must not raise; the default is used


class TestConfiguration:
    def test_ui_is_an_accepted_wake_mode(self):
        from beastt.cli import _parse_args

        assert _parse_args(["--wake", "--on-wake", "ui"]).on_wake == "ui"

    @pytest.mark.parametrize("mode", ["ask", "voice", "text", "ui"])
    def test_every_mode_parses(self, mode):
        from beastt.cli import _parse_args

        assert _parse_args(["--on-wake", mode]).on_wake == mode

    def test_the_port_comes_from_the_setting_not_a_hardcoded_default(self):
        """`--port` used to default to 8765, which silently beat BEASTT_UI_PORT
        for every user who set it."""
        from dataclasses import replace

        from beastt.cli import _build_config, _parse_args
        from beastt.config import Config

        assert _parse_args(["--ui"]).port is None
        assert replace(Config(), ui_port=9100).ui_port == 9100

    def test_an_explicit_port_still_wins(self):
        from beastt.cli import _build_config, _parse_args

        config = _build_config(_parse_args(["--ui", "--port", "9200"]))
        assert config.ui_port == 9200

    def test_the_default_port_matches_the_launcher(self):
        from beastt.config import Config

        assert Config().ui_port == uilaunch.DEFAULT_PORT


class TestTheAutostartLauncherKeepsTheMode:
    """It only ever baked in "voice", so choosing ui or text was dropped and the
    installed launcher quietly went back to asking."""

    @pytest.mark.parametrize("mode", ["voice", "text", "ui"])
    def test_the_chosen_mode_is_written_into_the_command(self, mode):
        import inspect

        from beastt import cli

        source = inspect.getsource(cli.run)
        assert 'f"--on-wake {config.on_wake}"' in source

    def test_ask_is_not_written_in(self):
        """It is the default, so naming it adds nothing."""
        import inspect

        from beastt import cli

        assert '("voice", "text", "ui")' in inspect.getsource(cli.run)


class TestTheGreetingIsNotDoubled:
    def test_the_chat_session_can_skip_its_own_welcome(self):
        """Standby now greets by name on wake, and greeting twice in three
        seconds sounds like a stutter."""
        import inspect

        from beastt import cli

        signature = inspect.signature(cli._chat_session)
        assert "greeted" in signature.parameters
        assert signature.parameters["greeted"].default is False

        source = inspect.getsource(cli._run_standby)
        assert "greeted=True" in source

    def test_standby_speaks_the_wake_greeting(self):
        """Pins the call site: the acknowledgement is the entire feature, and a
        call site with no test is how three of these have gone missing."""
        import inspect

        from beastt import cli

        source = inspect.getsource(cli._run_standby)
        assert "wake_greeting(config.user_name)" in source
        assert "_toast_wake(config, greeting)" in source

    def test_the_ui_mode_is_handled_in_standby(self):
        import inspect

        from beastt import cli

        source = inspect.getsource(cli._run_standby)
        assert 'mode == "ui"' in source
        assert "uilaunch.ensure" in source


class TestTheToastNeverBreaksTheWake:
    def test_a_failing_notification_is_swallowed(self, monkeypatch):
        """Waking up must not depend on the notification system working."""
        from beastt import cli, notify
        from beastt.config import Config

        def explode(*_args, **_kwargs):
            raise RuntimeError("no shell")

        monkeypatch.setattr(notify, "toast", explode)

        cli._toast_wake(Config(), "Hey Lingaa")      # must not raise

    def test_the_greeting_is_scrubbed_before_it_reaches_powershell(self,
                                                                   monkeypatch):
        """notify.toast builds a command by string interpolation, and this text
        carries the user's name -- which they chose, and could contain a quote.

        Asserted on what `toast` actually receives. An earlier version of this
        test looked for the string "safe_for_toast" in the source, which the
        import line satisfies all by itself -- so removing the call and leaving
        the import broke nothing.
        """
        from dataclasses import replace

        from beastt import cli, notify
        from beastt.config import Config

        received = {}
        monkeypatch.setattr(notify, "toast",
                            lambda title, message: received.update(
                                title=title, message=message))

        cli._toast_wake(replace(Config(), name="Jarvis"),
                        "Hey O'Brien, I'm \"listening\"")

        assert "'" not in received["message"]
        assert '"' not in received["message"]
        assert "Brien" in received["message"]
