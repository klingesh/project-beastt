"""Seeing what the background service is doing, without becoming a second one.

Asked for as "if i say jarvis it should automatically open cmd prompt and open ui
page", after observing that it worked "while i run python" -- which is the crux:
running it by hand is the one arrangement where it is visible, so it was the only
arrangement anyone could confirm was working.

What this deliberately does *not* do is open another assistant. A second `--wake`
puts two processes on one microphone; a `--text` window starts a whole second
brain. What is wanted is to *see*, and seeing needs no second listener.

Which also names the other fault covered here. Opening a terminal to find out why
the service seems deaf is exactly what puts two listeners on one microphone -- so
the investigation creates the symptom it is investigating, and nothing said so.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from beastt import logview


@pytest.fixture
def log(tmp_path):
    path = tmp_path / "beastt.log"
    path.write_text("\n".join(f"line {i}" for i in range(60)) + "\n",
                    encoding="utf-8")
    return path


class TestTail:
    def test_it_returns_the_last_lines(self, log):
        assert logview.tail(log, 3) == ["line 57", "line 58", "line 59"]

    def test_a_short_file_returns_everything(self, tmp_path):
        path = tmp_path / "short.log"
        path.write_text("one\ntwo\n", encoding="utf-8")
        assert logview.tail(path, 40) == ["one", "two"]

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        """The log does not exist until the service has run once."""
        assert logview.tail(tmp_path / "nope.log") == []

    def test_an_empty_file(self, tmp_path):
        path = tmp_path / "empty.log"
        path.write_text("", encoding="utf-8")
        assert logview.tail(path) == []

    def test_undecodable_bytes_do_not_stop_it(self, tmp_path):
        """The log is a tee of stdout, and anything can end up in it."""
        path = tmp_path / "odd.log"
        path.write_bytes(b"before\n\xff\xfe bad bytes\nafter\n")
        assert "after" in logview.tail(path)

    def test_the_default_backlog_is_bounded(self, log):
        assert len(logview.tail(log)) == logview.BACKLOG_LINES


class TestFollow:
    def _collect(self, path, seconds=0.5):
        seen, stop = [], [False]

        def watch():
            for line in logview.follow(path, poll=0.05, stop=lambda: stop[0]):
                seen.append(line)

        thread = threading.Thread(target=watch, daemon=True)
        thread.start()
        time.sleep(0.15)
        return seen, stop, thread

    def _finish(self, stop, thread, seconds=0.35):
        time.sleep(seconds)
        stop[0] = True
        thread.join(timeout=1.5)

    def test_new_lines_appear(self, log):
        seen, stop, thread = self._collect(log)
        with open(log, "a", encoding="utf-8") as handle:
            handle.write("[wake] Heard you: 'Jarvis.'\n")
        self._finish(stop, thread)

        assert "[wake] Heard you: 'Jarvis.'" in seen

    def test_history_is_not_replayed(self, log):
        """It starts at the end. The caller prints the backlog itself, and
        printing it twice would look like the log was repeating."""
        seen, stop, thread = self._collect(log)
        self._finish(stop, thread, 0.2)

        assert not any(line.startswith("line ") for line in seen)

    def test_rotation_is_noticed(self, log):
        """The service renames the log at a megabyte and starts a new one. A
        follower that kept seeking to its old offset would sit silent for ever
        afterwards -- which looks exactly like a dead service."""
        seen, stop, thread = self._collect(log)
        log.write_text("fresh after rotation\n", encoding="utf-8")
        self._finish(stop, thread)

        assert "--- log rotated ---" in seen
        assert "fresh after rotation" in seen

    def test_a_file_that_does_not_exist_yet_is_waited_for(self, tmp_path):
        missing = tmp_path / "later.log"
        seen, stop, thread = self._collect(missing)
        missing.write_text("the service just started\n", encoding="utf-8")
        self._finish(stop, thread)

        assert "the service just started" in seen

    def test_the_stop_callback_ends_it(self, log):
        seen, stop, thread = self._collect(log)
        stop[0] = True
        thread.join(timeout=1.5)

        assert not thread.is_alive()


class TestRun:
    def test_it_prints_the_backlog_and_says_what_it_is(self, log, capsys):
        logview.run(path=log, once=True)

        out = capsys.readouterr().out
        assert "line 59" in out
        assert "This is a viewer" in out
        assert "does not stop the assistant" in out

    def test_it_uses_the_configured_name(self, log, capsys):
        from dataclasses import replace

        from beastt.config import Config

        logview.run(config=replace(Config(), name="Jarvis"), path=log, once=True)

        assert "Jarvis" in capsys.readouterr().out

    def test_a_missing_log_explains_how_to_get_one(self, tmp_path, capsys):
        logview.run(path=tmp_path / "nope.log", once=True)

        out = capsys.readouterr().out
        assert "no log yet" in out
        assert "--wake --service" in out


class TestOpenWindow:
    def test_it_opens_a_visible_console(self, monkeypatch):
        """python.exe, not pythonw.exe: the window is the entire point."""
        seen = {}

        def fake_popen(command, **kwargs):
            seen["command"] = command
            seen["flags"] = kwargs.get("creationflags")
            return object()

        monkeypatch.setattr(logview.subprocess, "Popen", fake_popen)

        assert logview.open_window() is True
        assert "--watch-log" in seen["command"]
        assert not any("pythonw" in part for part in seen["command"])

    def test_a_failure_to_open_is_reported_not_raised(self, monkeypatch):
        def explode(*_args, **_kwargs):
            raise OSError("no console available")

        monkeypatch.setattr(logview.subprocess, "Popen", explode)

        assert logview.open_window() is False

    def test_it_picks_the_windowed_interpreter_deliberately(self, tmp_path,
                                                            monkeypatch):
        """Asserted against a directory where both exist.

        On Linux neither `python.exe` nor `pythonw.exe` is present, so the
        fallback fires and swapping the two names changes nothing observable --
        the choice would be untested on the only platform it matters on. These two
        launchers want opposite interpreters, which is an easy thing to get
        backwards.
        """
        for name in ("python.exe", "pythonw.exe"):
            (tmp_path / name).write_text("", encoding="utf-8")
        monkeypatch.setattr(logview.sys, "executable",
                            str(tmp_path / "pythonw.exe"))

        chosen = logview._console_python()

        assert chosen.name == "python.exe", (
            "the log window must have a console -- it is the whole point")

    def test_the_ui_launcher_wants_the_opposite(self, tmp_path, monkeypatch):
        """No console should flash up when the browser is opened."""
        from beastt import uilaunch

        for name in ("python.exe", "pythonw.exe"):
            (tmp_path / name).write_text("", encoding="utf-8")
        monkeypatch.setattr(uilaunch.sys, "executable",
                            str(tmp_path / "python.exe"))

        assert uilaunch._console_free_python().name == "pythonw.exe"

    def test_both_fall_back_when_neither_exists(self, tmp_path, monkeypatch):
        """As on this machine, and any POSIX one."""
        from beastt import uilaunch

        fake = tmp_path / "python3"
        fake.write_text("", encoding="utf-8")
        monkeypatch.setattr(logview.sys, "executable", str(fake))
        monkeypatch.setattr(uilaunch.sys, "executable", str(fake))

        assert logview._console_python() == fake
        assert uilaunch._console_free_python() == fake


class TestTheCliWiring:
    def test_the_flag_exists(self):
        from beastt.cli import _parse_args

        assert _parse_args(["--watch-log"]).watch_log is True
        assert _parse_args([]).watch_log is False

    def test_it_is_handled_before_the_banner(self):
        """A banner above a log just pushes the interesting part off the top."""
        import inspect

        from beastt import cli

        source = inspect.getsource(cli.run)
        assert source.index("args.watch_log") < source.index("print(_banner(")

    def test_waking_can_open_the_window(self):
        import inspect

        from beastt import cli

        source = inspect.getsource(cli._run_standby)
        assert "logview.open_window(config)" in source
        assert "console_opened" in source

    def test_only_one_window_per_standby_run(self):
        """There is no way to tell whether a window the user closed is still
        open, so opening one on every wake would bury the screen in consoles."""
        import inspect

        from beastt import cli

        source = inspect.getsource(cli._run_standby)
        assert "not console_opened" in source
        assert "console_opened = True" in source

    def test_the_setting_is_off_by_default(self):
        """Most people want the browser, not a log."""
        from beastt.config import Config

        assert Config().wake_console is False


class TestOneListenerAtATime:
    """Opening a terminal to find out why the service seems deaf is exactly what
    puts two listeners on one microphone."""

    def test_it_warns_when_a_service_is_already_running(self, monkeypatch,
                                                        capsys):
        import beastt.status as status

        monkeypatch.setattr(status, "_running", lambda: ["4156"])

        from beastt.cli import _warn_if_already_listening

        pids = _warn_if_already_listening()
        out = capsys.readouterr().out

        assert pids == ["4156"]
        assert "4156" in out
        assert "microphone" in out
        assert "--watch-log" in out, "it should offer the non-conflicting option"
        assert "taskkill" in out

    def test_it_says_nothing_when_nothing_is_running(self, monkeypatch, capsys):
        import beastt.status as status

        monkeypatch.setattr(status, "_running", lambda: [])

        from beastt.cli import _warn_if_already_listening

        assert _warn_if_already_listening() == []
        assert capsys.readouterr().out == ""

    def test_it_warns_rather_than_refusing(self, monkeypatch):
        """Running a second copy on purpose, to watch it, is legitimate. It just
        needs saying out loud."""
        import beastt.status as status

        monkeypatch.setattr(status, "_running", lambda: ["1"])

        from beastt.cli import _warn_if_already_listening

        _warn_if_already_listening()      # returns; does not raise or exit

    def test_a_broken_process_check_is_not_fatal(self, monkeypatch):
        """Detecting other processes is a nicety; failing at it must not stop the
        assistant starting."""
        import beastt.status as status

        def explode():
            raise RuntimeError("powershell missing")

        monkeypatch.setattr(status, "_running", explode)

        from beastt.cli import _warn_if_already_listening

        assert _warn_if_already_listening() == []

    def test_only_the_foreground_warns(self):
        """The service reaches _run_standby directly, and would otherwise warn
        about itself on every restart."""
        import inspect

        from beastt import cli, service

        assert "_warn_if_already_listening" in inspect.getsource(cli.run)
        assert "_warn_if_already_listening" not in inspect.getsource(
            service.run_service)
