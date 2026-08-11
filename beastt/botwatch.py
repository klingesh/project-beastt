"""Telling someone their trading bot is in trouble, without becoming noise.

Asking "how's my bot?" only works if you think to ask. The failure that prompted
this was quieter than that: the publisher on the VPS was stopped and never
restarted, so for sixteen minutes the assistant reported a stale snapshot -- right
down to a position label that had since been fixed -- with no indication anything
was wrong. Nobody had reason to ask, because nothing looked broken.

So this checks on its own and speaks up. The hard part is not detecting trouble, it
is not crying wolf: an assistant that notifies every five minutes trains its owner
to dismiss notifications, and then the one that mattered gets dismissed too.

Three rules keep it honest:

* **Alert on change, not on level.** A halt is announced when it starts, not every
  time it is observed.
* **Remind slowly while it persists.** A kill switch that fires at three in the
  morning should still be visible at breakfast, so it repeats -- hourly, not
  constantly.
* **Say when it recovers.** Knowing a problem has cleared is worth as much as
  knowing it began, and without it the last thing you were told is still bad news.

The decision logic is a pure function of (previous state, current health, now) so
it can be tested without waiting an hour for a reminder. That is deliberate: two
bugs in this feature's first day were hidden by code that could not be run.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .paths import resolve

STATE_FILE = "beastt_memory/botwatch.json"

#: States that warrant interrupting someone.
BAD = ("halted", "stale", "paused")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class WatchState:
    """What we last told the user, so we don't tell them again immediately."""

    state: str = "unknown"
    #: Signature of the alertable conditions, so a new error is noticed even when
    #: the headline state has not changed.
    signature: str = ""
    last_alert: str = ""
    since: str = ""

    @classmethod
    def load(cls, path: str = STATE_FILE) -> "WatchState":
        try:
            with open(resolve(path), encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            # A missing or unreadable file means "no history"; never a reason to
            # stop watching.
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: str = STATE_FILE) -> None:
        try:
            target = resolve(path)
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
        except Exception:
            pass          # remembering is a convenience, not a requirement


def signature_of(health, status: Dict[str, Any]) -> str:
    """The conditions worth alerting on, collapsed to a comparable string.

    Includes the concern count so a fresh error is noticed while the bot is still
    nominally 'running'; excludes equity and the heartbeat, which move constantly
    and would make every check look like news.
    """
    errors = len(status.get("recent_errors") or [])
    near = 0
    try:
        dd = float(status.get("drawdown_percent") or 0)
        limit = float(status.get("drawdown_limit_percent") or 0)
        near = 1 if limit and dd >= limit * 0.75 else 0
    except (TypeError, ValueError):
        pass
    # Restarts in the last hour, not the lifetime total: the total only rises, so
    # including it would make the signature change once and then never again --
    # and before that, it would have alerted forever on a stale count.
    from .trading import crash_looping

    looping = 1 if crash_looping(status) else 0
    return f"{health.state}|near={near}|errors={errors}|looping={looping}"


def decide(previous: WatchState, health, status: Dict[str, Any],
           now: Optional[datetime] = None,
           remind_minutes: float = 60.0) -> Tuple[List[str], WatchState]:
    """What to say, and what to remember. Pure, so the rules can be tested.

    Returns ([] , state) when there is nothing worth saying.
    """
    now = now or _now()
    stamp = now.isoformat()
    signature = signature_of(health, status)
    messages: List[str] = []

    recovered = previous.state in BAD and health.state == "running"
    worsened = health.state in BAD and health.state != previous.state
    changed = signature != previous.signature

    if recovered:
        messages.append("Trading bot is reporting normally again.")
    elif worsened or (changed and health.state in BAD):
        messages.extend(_describe(health, status))
    elif changed and health.state == "running":
        # Still running, but something new to mention -- a fresh error, or
        # drawdown crossing three quarters of the limit.
        messages.extend(_running_concerns(status))
    elif health.state in BAD and previous.last_alert:
        try:
            since_last = (now - datetime.fromisoformat(previous.last_alert))
            if since_last.total_seconds() >= remind_minutes * 60:
                messages.extend(m + " (still)" for m in _describe(health, status))
        except ValueError:
            pass

    if not messages:
        # Keep the signature current even when silent, so a condition that
        # appears and disappears between checks is not reported later as new.
        return [], WatchState(state=health.state, signature=signature,
                              last_alert=previous.last_alert,
                              since=previous.since or stamp)

    return messages, WatchState(
        state=health.state, signature=signature, last_alert=stamp,
        since=stamp if health.state != previous.state else
        (previous.since or stamp))


def _describe(health, status: Dict[str, Any]) -> List[str]:
    """Wording for a bad state. Kept here so alerts read as interruptions."""
    if health.state == "halted":
        reason = str(status.get("halt_reason") or "kill switch fired")
        return [f"Trading bot HALTED — {reason}. It will take no new entries "
                "until you clear it on the VPS."]
    if health.state == "stale":
        return ["Trading bot is not reporting. Either it or the status publisher "
                "has stopped — check both windows on the VPS."]
    if health.state == "paused":
        return ["Trading bot paused — daily loss limit reached. No new entries "
                "today."]
    return _running_concerns(status)


def _running_concerns(status: Dict[str, Any]) -> List[str]:
    """Things worth saying about a bot that is otherwise fine."""
    out: List[str] = []
    try:
        dd = float(status.get("drawdown_percent") or 0)
        limit = float(status.get("drawdown_limit_percent") or 0)
        if limit and dd >= limit * 0.75:
            out.append(f"Trading bot drawdown {dd:.2f}% — three quarters of the "
                       f"way to the {limit:.2f}% kill switch.")
    except (TypeError, ValueError):
        pass
    from .trading import crash_looping

    looping = crash_looping(status)
    if looping:
        out.append(f"Trading bot {looping}")
    for err in (status.get("recent_errors") or [])[-1:]:
        out.append(f"Trading bot error: {str(err.get('message', ''))[:160]}")
    return out


# --- running it -------------------------------------------------------------
def check_once(config: Config, remind_minutes: Optional[float] = None) -> List[str]:
    """Fetch, compare with what was last said, notify, and remember.

    Any failure returns [] rather than raising: a watcher that can take down the
    assistant is worse than no watcher.
    """
    from . import trading

    if not trading.configured(config):
        return []

    previous = WatchState.load()
    try:
        status = trading.fetch_status(config, use_cache=False)
    except trading.MonitorError as exc:
        # Being unable to read the status is itself worth one alert, treated as
        # the same condition as a stale heartbeat -- from the user's point of
        # view, they are the same problem.
        status = {"recent_errors": []}
        health = trading.Health("stale", str(exc), None, [])
    except Exception:
        return []
    else:
        health = trading.assess(config, status)

    remind = (remind_minutes if remind_minutes is not None
              else float(getattr(config, "bot_remind_minutes", 60) or 60))
    messages, updated = decide(previous, health, status, remind_minutes=remind)
    updated.save()

    for message in messages:
        _announce(config, message)
    return messages


def safe_for_toast(message: str) -> str:
    """Strip what would break the notification.

    notify.toast() builds a PowerShell command by string interpolation, so a
    single quote in the text ends the string literal and the notification either
    fails or runs mangled. Halt reasons come from the bot, so this text is not
    fully under our control.
    """
    cleaned = str(message or "").replace("'", "").replace('"', "")
    cleaned = " ".join(cleaned.split())
    return cleaned[:220]


def _announce(config: Config, message: str) -> None:
    from .notify import toast

    print(f"[botwatch] {message}")
    try:
        toast(str(getattr(config, "name", "BEASTT")), safe_for_toast(message))
    except Exception:
        pass


def start(config: Config) -> Optional[threading.Thread]:
    """Begin watching in the background. Returns the thread, or None if disabled."""
    from . import trading

    if not getattr(config, "bot_alerts", False) or not trading.configured(config):
        return None

    every = max(60.0, float(getattr(config, "bot_check_minutes", 5) or 5) * 60)

    def loop() -> None:
        # Wait before the first check so the assistant finishes starting up and
        # a transient network failure at boot isn't reported as a dead bot.
        time.sleep(30)
        while True:
            try:
                check_once(config)
            except Exception as exc:
                print(f"[botwatch] check failed: {exc.__class__.__name__}: {exc}")
            time.sleep(every)

    thread = threading.Thread(target=loop, name="botwatch", daemon=True)
    thread.start()
    print(f"[botwatch] Watching {config.bot_status_repo} every "
          f"{int(every / 60)} min.")
    return thread
