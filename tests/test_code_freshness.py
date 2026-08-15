"""Telling someone their server is running code that is no longer on disk.

From a real session, moments after an update that installed without a hitch:

    I couldn't attach pasted-2026-08-15-10-50-20-09.png: I can't read '.png'.
    I handle PDF, Word, Excel, PowerPoint, CSV, JSON, text and source files.

Both halves of that were current at once. The filename was invented by the new
pasting code; the refusal came from the old uploading code, which had never heard
of images. The interface re-reads its Javascript, HTML and CSS from disk on every
request, so the front end was already new; the server's Python was read into
memory at startup, so the back end was still old. The result is an error message
describing a version of the program that exists nowhere -- unsearchable, and
indistinguishable from a bug in the update itself.

That asymmetry is the thing being tested here, so the check that .py counts and
app.js does not is not a detail: it is the whole diagnosis.
"""

from __future__ import annotations

import os
import socket
import sys

import pytest

import update
from beastt import freshness


@pytest.fixture(autouse=True)
def watched(monkeypatch, tmp_path):
    """Point the watcher at a temporary package and reset it between tests.

    Both globals are set through monkeypatch so the real process's baseline is
    restored afterwards -- otherwise the first test to call `remember` would
    leave every later one comparing against a directory that no longer exists.
    """
    monkeypatch.setattr(freshness, "PACKAGE_ROOT", tmp_path)
    monkeypatch.setattr(freshness, "_baseline", None)
    return tmp_path


def write(root, name: str, text: str = "x = 1\n"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestWhatCountsAsCode:
    def test_python_anywhere_in_the_package_is_watched(self, watched):
        write(watched, "assistant.py")
        write(watched, "skills/github_skill.py")
        write(watched, "webui/server.py")

        assert set(freshness.snapshot()) == {
            "assistant.py", "skills/github_skill.py", "webui/server.py"}

    def test_the_static_assets_are_not(self, watched):
        """The load-bearing distinction. These are read from disk per request, so
        they are never stale -- and a restart notice that fired every time the
        Javascript changed would be firing on the half that already works."""
        write(watched, "server.py")
        write(watched, "webui/static/app.js", "console.log(1)")
        write(watched, "webui/static/index.html", "<p>")
        write(watched, "webui/static/style.css", "p{}")

        assert set(freshness.snapshot()) == {"server.py"}

    def test_editing_an_asset_after_startup_asks_for_nothing(self, watched):
        write(watched, "server.py")
        write(watched, "webui/static/app.js", "console.log(1)")
        freshness.remember()

        write(watched, "webui/static/app.js", "console.log('brand new')")

        assert freshness.changed() == []
        assert freshness.report() == {"stale": False}

    def test_keys_are_relative_and_slash_separated(self, watched):
        """They are shown to a person and compared across runs, so they must not
        carry the install path or the platform's separator."""
        write(watched, "skills/trading_skill.py")

        key, = freshness.snapshot()
        assert key == "skills/trading_skill.py"

    def test_a_package_folder_that_is_not_there(self, monkeypatch, tmp_path):
        """Never expected, but this runs inside a status request: it may not raise."""
        monkeypatch.setattr(freshness, "PACKAGE_ROOT", tmp_path / "not-here")

        assert freshness.code_files() == []
        assert freshness.snapshot() == {}
        assert freshness.report() == {"stale": False}


class TestNoticingAChange:
    def test_an_untouched_install_is_quiet(self, watched):
        write(watched, "a.py")
        write(watched, "b.py")
        freshness.remember()

        assert freshness.changed() == []
        assert freshness.report() == {"stale": False}

    def test_an_edited_file(self, watched):
        write(watched, "a.py")
        write(watched, "b.py")
        freshness.remember()

        write(watched, "b.py", "x = 2  # a real change\n")

        assert freshness.changed() == ["b.py"]

    def test_a_file_that_arrived(self, watched):
        """How the image support landed: `imageread.py` was new."""
        write(watched, "a.py")
        freshness.remember()

        write(watched, "imageread.py")

        assert freshness.changed() == ["imageread.py"]

    def test_a_file_that_went_away(self, watched):
        write(watched, "a.py")
        write(watched, "gone.py")
        freshness.remember()

        (watched / "gone.py").unlink()

        assert freshness.changed() == ["gone.py"]

    def test_rewriting_a_file_with_the_same_bytes_is_not_a_change(self, watched):
        """Why contents are hashed instead of timestamps compared.

        Every updater rewrites files, git checkouts rewrite files, and an editor
        saving an unmodified buffer rewrites files. A notice that appeared after
        each of those would be ignored inside a week, and then ignored on the one
        occasion it mattered.
        """
        path = write(watched, "a.py", "x = 1\n")
        freshness.remember()

        path.write_text("x = 1\n", encoding="utf-8")
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))

        assert freshness.changed() == []

    def test_only_the_files_that_moved_are_named(self, watched):
        for name in ("a.py", "b.py", "c.py"):
            write(watched, name)
        freshness.remember()

        write(watched, "b.py", "x = 99\n")

        assert freshness.changed() == ["b.py"]

    def test_asking_twice_gives_the_same_answer(self, watched):
        """It is polled, so it has to be a question and not an event."""
        write(watched, "a.py")
        freshness.remember()
        write(watched, "a.py", "x = 2\n")

        assert freshness.changed() == ["a.py"]
        assert freshness.changed() == ["a.py"]

    def test_nothing_is_claimed_before_a_baseline_exists(self, watched):
        """With nothing to compare against, the honest answer is "no evidence of
        a change" -- not "everything changed", which would put a restart notice
        in front of anyone whose server forgot to record its startup state."""
        write(watched, "a.py")
        write(watched, "b.py", "x = 2\n")

        assert freshness._baseline is None
        assert freshness.changed() == []
        assert freshness.report() == {"stale": False}

    def test_remembering_again_moves_the_line(self, watched):
        """What a restart does, from this module's point of view."""
        write(watched, "a.py")
        freshness.remember()
        write(watched, "a.py", "x = 2\n")
        assert freshness.changed() == ["a.py"]

        freshness.remember()

        assert freshness.changed() == []


class TestTimestampsAreNotTrusted:
    """There was a stat cache in here -- size and mtime as a key, to avoid
    re-hashing while nothing moved. These are the two cases that removed it."""

    def test_an_edit_of_the_same_length_is_still_seen(self, watched):
        """The case that killed the cache, and it was these tests that found it.

        "x = 1" to "x = 2" is the same number of bytes, and two writes close
        together share a modification time on some filesystems -- including the
        one this is developed on, where the two stats came back identical to the
        nanosecond. Keyed on that stat, a real edit was invisible.
        """
        path = write(watched, "a.py", "x = 1\n")
        freshness.remember()

        path.write_text("x = 2\n", encoding="utf-8")

        assert freshness.changed() == ["a.py"]

    def test_a_touch_with_no_edit_is_not_a_change(self, watched):
        """The other half, and the reason contents are hashed at all: updaters,
        git checkouts and editors all rewrite files that did not change."""
        path = write(watched, "a.py", "x = 1\n")
        freshness.remember()

        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))

        assert freshness.changed() == []


class TestWhatTheInterfaceIsTold:
    def test_a_clean_report_carries_nothing_else(self, watched):
        write(watched, "a.py")
        freshness.remember()

        assert freshness.report("Jarvis") == {"stale": False}

    def test_it_says_which_program_and_how_many_files(self, watched):
        write(watched, "a.py")
        write(watched, "b.py")
        freshness.remember()
        write(watched, "a.py", "x = 2\n")
        write(watched, "b.py", "x = 2\n")

        report = freshness.report("Jarvis")

        assert report["stale"] is True
        assert report["count"] == 2
        assert report["detail"].startswith("Jarvis was updated on disk")
        assert "2 files changed" in report["detail"]

    def test_one_file_is_singular(self, watched):
        """It will usually be one, and "1 files changed" reads like a bug."""
        write(watched, "a.py")
        freshness.remember()
        write(watched, "a.py", "x = 2\n")

        assert "1 file changed" in freshness.report()["detail"]

    def test_it_explains_the_symptom_not_the_event(self, watched):
        """Anyone reading this is already looking at something inexplicable. The
        message has to connect itself to that, or it is just a version number."""
        write(watched, "a.py")
        freshness.remember()
        write(watched, "a.py", "x = 2\n")

        detail = freshness.report()["detail"]

        assert "still running the code from before" in detail
        assert "errors" in detail

    def test_it_always_carries_a_fix(self, watched):
        write(watched, "a.py")
        freshness.remember()
        write(watched, "a.py", "x = 2\n")

        assert freshness.report()["fix"] == freshness.restart_hint()

    def test_a_large_update_does_not_list_everything(self, watched):
        """Thirty filenames in a banner is a wall of text, and the count already
        says how big it was."""
        for number in range(30):
            write(watched, f"m{number:02d}.py")
        freshness.remember()
        for number in range(30):
            write(watched, f"m{number:02d}.py", "x = 2\n")

        report = freshness.report()

        assert report["count"] == 30
        assert len(report["changed"]) == freshness.MAX_NAMED

    def test_the_names_it_does_give_are_real(self, watched):
        write(watched, "a.py")
        write(watched, "webui/server.py")
        freshness.remember()
        write(watched, "webui/server.py", "x = 2\n")

        assert freshness.report()["changed"] == ["webui/server.py"]


class TestHowToRestartIt:
    """Two cases with no overlap, which is why this is not one sentence."""

    def test_a_windowless_server_is_not_told_to_press_ctrl_c(self, monkeypatch):
        """Started by the wake word, so it runs under pythonw with no console.
        "Press Ctrl+C in its window" names a window that does not exist, which is
        how someone concludes the update failed and stops trying."""
        monkeypatch.setattr(freshness.sys, "platform", "win32")
        monkeypatch.setattr(freshness.sys, "executable", r"C:\Py\pythonw.exe")

        hint = freshness.restart_hint()

        assert "taskkill /F /IM pythonw.exe" in hint
        # Ctrl+C is named only to rule it out, which is worth saying: someone
        # who was told that last week will otherwise go looking for the window.
        assert "nothing to Ctrl+C" in hint
        assert "press Ctrl+C" not in hint

    def test_a_server_in_a_terminal_is_told_to_press_ctrl_c(self, monkeypatch):
        monkeypatch.setattr(freshness.sys, "platform", "win32")
        monkeypatch.setattr(freshness.sys, "executable", r"C:\Py\python.exe")

        hint = freshness.restart_hint()

        assert "Ctrl+C" in hint
        assert "main.py --ui" in hint
        assert "taskkill" not in hint

    def test_anywhere_else_gets_plain_words(self, monkeypatch):
        monkeypatch.setattr(freshness.sys, "platform", "linux")
        monkeypatch.setattr(freshness.sys, "executable", "/usr/bin/python3")

        hint = freshness.restart_hint()

        assert "taskkill" not in hint and "Ctrl+C" not in hint
        assert "start it again" in hint

    def test_the_windows_check_is_case_insensitive(self, monkeypatch):
        """Windows will hand back either spelling."""
        monkeypatch.setattr(freshness.sys, "platform", "win32")
        monkeypatch.setattr(freshness.sys, "executable", r"C:\Py\PYTHONW.EXE")

        assert "taskkill" in freshness.restart_hint()


class FakeState:
    """Enough of the server's state for the status route, with no model probe."""

    def __init__(self, config):
        self.config = config

    def brain_status(self):
        return {"ready": True, "detail": "llama3.2 · local", "label": "llama3.2"}


class TestTheStatusRoute:
    @pytest.fixture
    def ask_status(self, config):
        from beastt.webui import server

        def _ask():
            handler = object.__new__(server.Handler)
            handler.path = "/api/status"
            handler.state = FakeState(config)
            replies = []
            handler._json = lambda payload, code=200: replies.append(payload)
            handler.do_GET()
            return replies[-1]

        return _ask

    def test_it_reports_fresh_code(self, ask_status, watched):
        write(watched, "a.py")
        freshness.remember()

        assert ask_status()["code"] == {"stale": False}

    def test_it_reports_stale_code(self, ask_status, watched):
        write(watched, "a.py")
        freshness.remember()
        write(watched, "a.py", "x = 2\n")

        code = ask_status()["code"]

        assert code["stale"] is True
        assert code["fix"]

    def test_it_names_the_assistant_the_user_configured(self, ask_status, watched):
        """`config` here is named JARVIS; the message must not hardcode anything."""
        write(watched, "a.py")
        freshness.remember()
        write(watched, "a.py", "x = 2\n")

        assert ask_status()["code"]["detail"].startswith("JARVIS was updated")

    def test_the_rest_of_the_status_still_works(self, ask_status, watched):
        payload = ask_status()

        assert payload["name"] and payload["brain"]["ready"] is True


class TestTheServerRecordsItsOwnStartupState:
    def test_serving_takes_a_baseline(self, monkeypatch, config, watched):
        """Without this call the comparison has nothing to compare to and the
        whole feature silently reports "fine" forever."""
        from beastt.webui import server

        write(watched, "a.py")

        class FakeServer:
            def __init__(self, address, handler):
                self.address = address

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        monkeypatch.setattr(server, "ThreadingHTTPServer", FakeServer)
        monkeypatch.setattr(server.Handler, "state", None, raising=False)

        server.serve(config=config, port=8765, open_browser=False)

        assert freshness._baseline == freshness.snapshot()
        assert list(freshness._baseline) == ["a.py"]


class TestTheFrontEndAsks:
    """It is the page that has to show this, and the page is not importable."""

    @pytest.fixture
    def app_js(self):
        from pathlib import Path

        from beastt.webui import server

        return Path(server.STATIC / "app.js").read_text(encoding="utf-8")

    def test_the_startup_check_uses_the_status_reply(self, app_js):
        """Asserted as a live statement, not as text anywhere in the file: a
        mutation that commented the call out passed the first version of this."""
        calls = [line.strip() for line in app_js.splitlines()
                 if "noteStaleCode(status.code)" in line]

        assert calls, "the startup call is gone"
        assert not any(line.startswith("//") for line in calls)

    def test_it_keeps_asking_after_the_page_has_loaded(self, app_js):
        """The way anyone learns about an update is by running update.py, with
        this window already open. A check only at load would miss every time."""
        assert "watchForUpdates" in app_js
        assert "setInterval" in app_js

    def test_polling_starts_even_if_the_first_status_failed(self, app_js):
        """It was inside the try block first, so one hiccup at load disabled the
        feature for the life of the page."""
        head, _, tail = app_js.partition("watchForUpdates();\n    await refreshList")
        assert tail, "the startup call moved; check it is still outside the try"
        assert "catch (_) { /* defaults are fine */ }" in head

    def test_the_notice_is_shown_once(self, app_js):
        """Dismissed means told. Re-raising a banner every twenty seconds is how
        a true warning becomes something people close without reading.

        Both halves are named, because "staleNoticed appears somewhere in the
        file" was satisfied by a mutant that renamed the declaration and left the
        uses pointing at nothing.
        """
        assert "let staleNoticed = false;" in app_js
        assert "|| staleNoticed) return;" in app_js
        assert "staleNoticed = true;" in app_js


class TestTheUpdaterSaysWhatToRestart:
    """The other end of the same problem: after `update.py` replaces the files,
    the process holding the old ones is still running and nothing said so."""

    def test_nothing_running_means_no_advice(self):
        """Silence when there is nothing to restart -- the common case is a
        laptop where BEASTT is not up at all."""
        assert update.restart_notice([]) == []

    def test_it_names_what_it_found(self):
        lines = update.restart_notice(["the chat interface, on http://127.0.0.1:8765"])
        text = "\n".join(lines)

        assert "http://127.0.0.1:8765" in text
        assert "Restart" in text

    def test_it_says_why_in_terms_of_memory(self):
        text = "\n".join(update.restart_notice(["a background BEASTT"]))

        assert "on disk but not in memory" in text

    def test_on_windows_it_gives_the_command(self, monkeypatch):
        monkeypatch.setattr(update.sys, "platform", "win32")

        text = "\n".join(update.restart_notice(["the chat interface"]))

        assert "taskkill /F /IM pythonw.exe" in text
        assert "Startup shortcut" in text
        assert "Ctrl+C" in text          # for the one started by hand

    def test_elsewhere_it_does_not(self, monkeypatch):
        monkeypatch.setattr(update.sys, "platform", "linux")

        text = "\n".join(update.restart_notice(["the chat interface"]))

        assert "taskkill" not in text

    def test_the_parts_are_collected_from_both_checks(self, monkeypatch):
        monkeypatch.setattr(update, "_ui_answering", lambda port: True)
        monkeypatch.setattr(update, "_windowless_beastt", lambda: True)

        parts = update.running_parts(8765)

        assert len(parts) == 2
        assert "8765" in parts[0]

    @pytest.mark.parametrize("ui, windowless, expected", [
        (True, False, 1),
        (False, True, 1),
        (False, False, 0),
    ])
    def test_only_what_is_actually_up(self, monkeypatch, ui, windowless, expected):
        monkeypatch.setattr(update, "_ui_answering", lambda port: ui)
        monkeypatch.setattr(update, "_windowless_beastt", lambda: windowless)

        assert len(update.running_parts(8765)) == expected


class TestFindingTheRunningInterface:
    def test_a_listening_port_is_found(self):
        """A socket connect rather than a PID file: the file outlives the process,
        and a live process says nothing about whether its socket is up."""
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)

            assert update._ui_answering(listener.getsockname()[1]) is True

    def test_a_closed_port_is_not(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        assert update._ui_answering(port) is False

    def test_a_nonsense_port_does_not_raise(self):
        assert update._ui_answering("not a port") is False

    @pytest.fixture
    def tasklist(self, monkeypatch):
        """Stand in for `tasklist`, and record whether it was run at all."""
        import subprocess
        import types

        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return types.SimpleNamespace(stdout=fake_run.output, returncode=0)

        fake_run.output = ""
        fake_run.calls = calls
        monkeypatch.setattr(subprocess, "run", fake_run)
        return fake_run

    def test_the_process_check_is_windows_only(self, monkeypatch, tasklist):
        """`tasklist` does not exist elsewhere, so this must not shell out on a
        machine where the answer is already known.

        The call is what gets asserted, not the return value: the function
        swallows exceptions, so a mutant that dropped the platform guard still
        returned False here -- it just started a process to do it.
        """
        monkeypatch.setattr(update.sys, "platform", "linux")

        assert update._windowless_beastt() is False
        assert tasklist.calls == []

    def test_a_listed_pythonw_counts(self, monkeypatch, tasklist):
        monkeypatch.setattr(update.sys, "platform", "win32")
        tasklist.output = "pythonw.exe                  18244 Console   1   61,204 K\n"

        assert update._windowless_beastt() is True
        assert tasklist.calls and "tasklist" in tasklist.calls[0][0]

    def test_an_empty_list_does_not(self, monkeypatch, tasklist):
        """What tasklist prints when the filter matches nothing."""
        monkeypatch.setattr(update.sys, "platform", "win32")
        tasklist.output = ("INFO: No tasks are running which match the "
                           "specified criteria.\n")

        assert update._windowless_beastt() is False

    def test_a_tasklist_that_fails_is_not_an_answer(self, monkeypatch):
        """This runs at the end of a successful update. It may not be the thing
        that makes the update look like it broke."""
        import subprocess

        monkeypatch.setattr(update.sys, "platform", "win32")
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("no")))

        assert update._windowless_beastt() is False


class TestReadingTheSettings:
    @pytest.fixture
    def env_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(update, "ROOT", tmp_path)
        return lambda text: (tmp_path / ".env").write_text(text, encoding="utf-8")

    def test_a_value_comes_from_the_env_file(self, env_file):
        env_file("BEASTT_UI_PORT=9100\n")

        assert update._setting("BEASTT_UI_PORT") == "9100"

    def test_comments_and_quotes_are_stripped(self, env_file):
        """Unchanged behaviour, re-asserted because the token reader was
        generalised to reach it and that is exactly when such things break."""
        env_file('BEASTT_GITHUB_TOKEN="ghp_secret"   # mine\n')

        assert update._token() == "ghp_secret"

    def test_the_environment_is_the_fallback(self, env_file, monkeypatch):
        env_file("# nothing here\n")
        monkeypatch.setenv("BEASTT_UI_PORT", "9200")

        assert update._setting("BEASTT_UI_PORT") == "9200"

    def test_an_unset_value_is_empty(self, env_file, monkeypatch):
        env_file("")
        monkeypatch.delenv("BEASTT_UI_PORT", raising=False)

        assert update._setting("BEASTT_UI_PORT") == ""

    @pytest.mark.parametrize("raw, expected", [
        ("9100", 9100),
        ("", update.DEFAULT_UI_PORT),
        ("eight thousand", update.DEFAULT_UI_PORT),
    ])
    def test_the_port_survives_a_bad_setting(self, monkeypatch, raw, expected):
        monkeypatch.setattr(update, "_setting", lambda key: raw)

        assert update.ui_port() == expected

    def test_the_default_port_matches_the_application(self, pristine_config):
        """Duplicated on purpose -- the updater must not import the code it is
        updating -- so something has to notice if the two drift apart."""
        assert update.DEFAULT_UI_PORT == pristine_config().ui_port


class TestTheNoticeAppearsWhenItShould:
    """Exercising `main` itself, because "only when something changed" is a
    decision and not a formatting detail."""

    @pytest.fixture
    def fake_github(self, monkeypatch, tmp_path):
        monkeypatch.setattr(update, "ROOT", tmp_path)
        monkeypatch.setattr(update, "list_files", lambda branch: ["beastt/a.py"])
        monkeypatch.setattr(update, "running_parts",
                            lambda port: ["the chat interface"])

        def serve(content: bytes):
            monkeypatch.setattr(update, "_get",
                                lambda url, as_json=False, accept="*/*": content)

        return tmp_path, serve

    def test_a_changed_file_asks_for_a_restart(self, fake_github, capsys):
        root, serve = fake_github
        serve(b"x = 2\n")
        (root / "beastt").mkdir()
        (root / "beastt" / "a.py").write_bytes(b"x = 1\n")

        assert update.main([]) == 0
        assert "not in memory" in capsys.readouterr().out

    def test_a_new_file_asks_too(self, fake_github, capsys):
        root, serve = fake_github
        serve(b"x = 1\n")

        assert update.main([]) == 0
        assert "not in memory" in capsys.readouterr().out

    def test_an_update_that_changed_nothing_stays_quiet(self, fake_github, capsys):
        """Run twice in a row, as people do. Nothing moved, so nothing is stale
        and there is nothing to restart for."""
        root, serve = fake_github
        serve(b"x = 1\n")
        (root / "beastt").mkdir()
        (root / "beastt" / "a.py").write_bytes(b"x = 1\n")

        assert update.main([]) == 0
        assert "not in memory" not in capsys.readouterr().out

    def test_a_dry_run_stays_quiet(self, fake_github, capsys):
        """Nothing was written, so nothing in memory is out of date."""
        root, serve = fake_github
        serve(b"x = 2\n")

        assert update.main(["--dry-run"]) == 0
        assert "not in memory" not in capsys.readouterr().out


class TestTheDiagnosisIsWrittenDown:
    def test_the_module_explains_the_asymmetry(self):
        """This module exists because of one confusing evening. If the reason is
        not in the file, the next person deletes it as belt-and-braces."""
        import inspect

        text = inspect.getdoc(freshness) or ""

        assert "every single request" in text
        assert "pasted-2026-08-15" in text

    def test_it_says_why_it_is_not_a_reload(self):
        import inspect

        assert "reload" in (inspect.getdoc(freshness) or "").lower()

    def test_the_over_reporting_is_a_recorded_choice(self):
        """Comparing every .py rather than only the imported ones costs an
        occasional needless restart. The reasoning has to survive the reviewer
        who spots it and tightens it into the silent-stale trap."""
        import inspect

        assert "sys.modules" in (inspect.getdoc(freshness) or "")


def test_the_watcher_watches_the_real_package(monkeypatch):
    """Every test above points it at a temporary folder. One has to check that
    the default is the actual installed package -- a typo in `PACKAGE_ROOT`
    would leave all of them passing against nothing.

    The autouse fixture has already redirected it, so this puts it back the way
    the module computes it rather than trusting the fixture's saved value.
    """
    from pathlib import Path

    monkeypatch.setattr(freshness, "PACKAGE_ROOT",
                        Path(freshness.__file__).resolve().parent)

    names = set(freshness.snapshot())

    assert "webui/server.py" in names
    assert "freshness.py" in names
    assert not any(name.endswith((".js", ".css", ".html")) for name in names)
    assert freshness.PACKAGE_ROOT.name == "beastt"
    assert sys.modules["beastt"].__file__.startswith(str(freshness.PACKAGE_ROOT))
