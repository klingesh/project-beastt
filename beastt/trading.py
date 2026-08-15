"""Watching a trading bot that runs somewhere else.

The bot lives on a VPS and BEASTT lives on a laptop, so there is no shared disk
between them. The bot publishes a small `status.json` to a private GitHub
repository every few minutes, and this reads it back.

Strictly read-only. Nothing here can place, close or modify a trade, and it never
will: a language model in the order path is a bad idea however well it reports.
What it does is answer "is it alive, is it halted, how far into its drawdown limit
is it", which is the part a person actually cannot see from a phone.

The one genuinely subtle thing is staleness. The bot rewrites its heartbeat every
poll (60s by default) but the publisher only pushes every 300s, so a perfectly
healthy bot can have a heartbeat five minutes old by the time it is read here.
The stale threshold therefore has to be comfortably larger than the publish
interval, not the poll interval -- otherwise BEASTT reports a dead bot every few
minutes and the alert becomes noise to be ignored.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import Config

API = "https://api.github.com"
_UA = {"User-Agent": "beastt-assistant/1.0 (trading bot monitor)"}
TIMEOUT = 20
#: Answers are cached so repeated questions in one conversation don't hammer the
#: API. Well under the publish interval, so it never hides fresh news.
CACHE_TTL = 60

_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
#: (when we asked, when the file was last pushed as epoch seconds or None).
_publish_cache: Dict[str, Tuple[float, Optional[float]]] = {}


class MonitorError(RuntimeError):
    """The status could not be read. Carries something worth showing a human."""


def configured(config: Config) -> bool:
    return bool(str(getattr(config, "bot_status_repo", "") or "").strip())


def token_for(config: Config) -> str:
    """The token to read the status repo with.

    A dedicated one is preferred and should be read-only: BEASTT never writes
    there. Falling back to the general GitHub token is a convenience, but it will
    only work if that token's access includes the status repository -- a
    fine-grained token scoped to one repo cannot see another.
    """
    return (str(getattr(config, "bot_status_token", "") or "").strip()
            or str(getattr(config, "github_token", "") or "").strip())


# --- fetching ---------------------------------------------------------------
def fetch_status(config: Config, use_cache: bool = True) -> Dict[str, Any]:
    """Read status.json out of the private repo. Raises MonitorError on failure."""
    repo = str(getattr(config, "bot_status_repo", "") or "").strip()
    if not repo:
        raise MonitorError(
            "No trading bot configured. Set BEASTT_BOT_STATUS_REPO in your .env "
            "to the private repository the bot publishes to.")

    path = str(getattr(config, "bot_status_file", "status.json") or "status.json")
    key = f"{repo}/{path}"
    hit = _cache.get(key)
    if use_cache and hit and (time.time() - hit[0]) < CACHE_TTL:
        return hit[1]

    token = token_for(config)
    if not token:
        raise MonitorError(
            "No GitHub token available to read the bot status. Set "
            "BEASTT_BOT_STATUS_TOKEN in your .env.")

    headers = dict(_UA)
    headers["Authorization"] = f"Bearer {token}"
    headers["Accept"] = "application/vnd.github+json"
    headers["X-GitHub-Api-Version"] = "2022-11-28"

    try:
        resp = requests.get(f"{API}/repos/{repo}/contents/{path}",
                            headers=headers, timeout=TIMEOUT)
    except Exception as exc:
        raise MonitorError(f"couldn't reach GitHub ({exc.__class__.__name__})")

    if resp.status_code == 404:
        # GitHub returns 404, not 403, for a private repo the token cannot see,
        # so these two causes are genuinely indistinguishable from here. Say both
        # rather than guessing at one.
        raise MonitorError(
            f"can't find {repo}/{path}. Either the name is wrong, or the token "
            "doesn't have access to that repository — a fine-grained token "
            "scoped to one repo can't read another.")
    if resp.status_code in (401, 403):
        raise MonitorError("GitHub refused the token — check it hasn't expired.")
    if resp.status_code >= 400:
        raise MonitorError(f"GitHub returned HTTP {resp.status_code}")

    try:
        body = resp.json()
        raw = base64.b64decode(body["content"])
        status = json.loads(raw)
    except Exception:
        raise MonitorError("the published status wasn't readable JSON")

    if not isinstance(status, dict):
        raise MonitorError("the published status wasn't the expected shape")

    _cache[key] = (time.time(), status)
    return status


def fetch_publish_age(config: Config, use_cache: bool = True) -> Optional[float]:
    """Seconds since the status file was last pushed. None if it can't be known.

    A second request, deliberately. The contents API says what the file holds and
    not when it arrived, and *when it arrived* is the entire discriminator between
    a stopped bot and a stopped publisher -- see `stale_verdict`.

    **Never raises.** This is an enrichment: without it the report falls back to
    the wording it had before, which was vague but true. The monitor is what
    someone reaches for when something is already broken, so it must not acquire a
    new way to fail -- a second network call that could take the whole report down
    with it would be a poor trade for one sharper sentence.

    The commit time is cached, not the age. Caching an age would hand back a
    number that was right a minute ago, which is precisely the class of bug this
    function exists to detect.
    """
    repo = str(getattr(config, "bot_status_repo", "") or "").strip()
    path = str(getattr(config, "bot_status_file", "status.json") or "status.json")
    if not repo:
        return None

    key = f"{repo}/{path}"
    hit = _publish_cache.get(key)
    if use_cache and hit and (time.time() - hit[0]) < CACHE_TTL:
        return None if hit[1] is None else max(0.0, time.time() - hit[1])

    published: Optional[float] = None
    token = token_for(config)
    if token:
        headers = dict(_UA)
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        try:
            resp = requests.get(
                f"{API}/repos/{repo}/commits",
                params={"path": path, "per_page": 1},
                headers=headers, timeout=TIMEOUT)
            if resp.status_code < 400:
                commits = resp.json()
                stamp = (commits[0]["commit"]["committer"]["date"]
                         if commits else "")
                when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                published = when.timestamp()
        except Exception:
            # Including a shape that isn't what the API documents. Any failure at
            # all means "not known", which the caller already handles.
            published = None

    _publish_cache[key] = (time.time(), published)
    return None if published is None else max(0.0, time.time() - published)


# --- interpreting -----------------------------------------------------------
@dataclass
class Health:
    """What the numbers mean, worked out once so the wording stays consistent."""

    state: str    # halted | paused | stale | bot_stopped | not_publishing |
                  # running | unknown
    headline: str
    heartbeat_age: Optional[float]   # seconds, None if unparseable
    concerns: List[str]
    #: Seconds since the status file was last pushed, when that could be found
    #: out. Carried on the Health so the reports can read it without every one of
    #: them growing a parameter.
    publish_age: Optional[float] = None


#: States meaning "the numbers below are not current". Grouped because three
#: separate reports have to suppress the same things for all of them, and the
#: first version of this knew only about "stale" -- so adding a state silently
#: turned the suppression off.
SILENT = ("stale", "bot_stopped", "not_publishing")


def stale_verdict(heartbeat_age: float, publish_age: Optional[float],
                  stale_after: float) -> Tuple[str, str, List[str]]:
    """Which side went quiet: the bot, or the publisher pushing for it.

    Reached when the heartbeat has gone silent. The heartbeat alone cannot tell
    those apart, and this report used to say exactly that -- "either the bot has
    stopped, or the publisher on the VPS has" -- leaving somebody to go and look.

    The discriminator is the age of the last *push* against the age of the
    heartbeat inside it:

    * The publisher pushed long after the bot went quiet: **the bot stopped**, and
      the VPS and its network are demonstrably fine, because a dead publisher
      cannot push.
    * Both went quiet together: **the publishing side stopped**. The bot may well
      still be running and trading, which is what actually happened the day this
      was written -- the trader logged normal cycles for fourteen hours after the
      last push, while the report said "not reporting".

    What it will not claim is that the bot is *fine* in the second case. From a
    laptop, "the publisher crashed", "the VPS rebooted" and "the network went" are
    one indistinguishable event. Naming one would be the same guess this replaces,
    pointed the other way -- and that guess is what sent somebody hunting through
    Event Viewer for a shutdown that had never happened.
    """
    if publish_age is None:
        return ("stale",
                f"NOT REPORTING — last heartbeat {_ago(heartbeat_age)}",
                ["Either the bot has stopped, or the publisher on the VPS has.",
                 "Worth checking both windows are still running."])

    # How old the heartbeat already was when the last push went out. A few
    # minutes is normal and means nothing: the bot writes every 60s and the
    # publisher pushes every 300s, so the newest published heartbeat is routinely
    # several minutes behind. Only a lag past the staleness threshold says the
    # publisher was still working while the bot was not.
    lag = heartbeat_age - publish_age
    if lag > stale_after:
        return ("bot_stopped",
                f"BOT STOPPED — last heartbeat {_ago(heartbeat_age)}, but the "
                f"VPS published {_ago(publish_age)}",
                ["The publisher is still pushing, so the VPS is up and its "
                 "network is fine — it is the bot itself that has stopped.",
                 "Restart the bot on the VPS. The publisher doesn't need "
                 "touching."])

    return ("not_publishing",
            f"NOT REPORTING — nothing published for {_ago(publish_age)}",
            ["The publisher on the VPS stopped, so every number below is that "
             "old. The bot may still be running and trading normally.",
             "Check the bot's own log before restarting it — if it is alive, only "
             "the publisher needs starting."])


def heartbeat_age(status: Dict[str, Any]) -> Optional[float]:
    stamp = str(status.get("heartbeat") or "").strip()
    if not stamp:
        return None
    try:
        # The bot writes an offset-aware ISO timestamp; tolerate a trailing Z.
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - when).total_seconds())


def assess(config: Config, status: Dict[str, Any],
           publish_age: Optional[float] = None) -> Health:
    """Decide how worried to be, in a fixed order of severity.

    `publish_age` is optional and the report degrades to its older, vaguer wording
    without it -- see `stale_verdict`. Passed in rather than fetched here so this
    stays a pure function of its arguments, which is the only reason an hour of
    monitor behaviour can be tested in milliseconds.
    """
    stale_after = float(getattr(config, "bot_stale_minutes", 15) or 15) * 60
    age = heartbeat_age(status)
    concerns: List[str] = []

    if status.get("halted"):
        reason = str(status.get("halt_reason") or "reason not recorded")
        return Health("halted", f"HALTED — kill switch fired ({reason})", age,
                      ["The bot will take no new entries until this is cleared "
                       "by hand on the VPS."], publish_age)

    if age is None:
        concerns.append("its heartbeat timestamp couldn't be read")
    elif age > stale_after:
        state, headline, notes = stale_verdict(age, publish_age, stale_after)
        return Health(state, headline, age, notes, publish_age)

    if status.get("day_halted") or status.get("new_entries_blocked"):
        return Health("paused", "PAUSED — daily loss limit hit, no new entries "
                                "today", age, concerns, publish_age)

    # Things worth mentioning without changing the headline.
    dd = _number(status.get("drawdown_percent"))
    limit = _number(status.get("drawdown_limit_percent"))
    if limit and dd >= limit * 0.75:
        concerns.append(f"drawdown is {dd:.2f}%, over three quarters of the "
                        f"{limit:.2f}% kill-switch limit")
    errors = status.get("recent_errors") or []
    if errors:
        concerns.append(f"{len(errors)} recent error(s) logged")
    looping = crash_looping(status)
    if looping:
        concerns.append(looping)

    return Health("running", "RUNNING", age, concerns, publish_age)


#: Restarts within an hour that suggest the bot cannot stay up.
LOOP_THRESHOLD = 3


def crash_looping(status: Dict[str, Any]) -> str:
    """A warning about repeated restarts, or "" if there is nothing to say.

    Judged on restarts *in the last hour*, not the lifetime total. The first
    version tested the lifetime counter, which only ever rises -- so having been
    restarted nine times by hand during one morning's maintenance, the report said
    "possibly crash-looping" about a bot that had been stable for hours, and would
    have gone on saying it forever. A warning that cannot expire is not a warning.

    Older bots publish only the lifetime figure. Rather than guess a rate from it,
    say nothing: a false alarm every five minutes for the life of the install is
    worse than a missed one, and the number is still shown in the report either way.
    """
    recent = status.get("restarts_last_hour")
    if recent is None:
        return ""
    count = int(_number(recent))
    if count < LOOP_THRESHOLD:
        return ""
    return (f"it has restarted {count} times in the last hour — it may not be "
            "staying up; check logs\\bot.log on the VPS")


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ago(seconds: Optional[float]) -> str:
    if seconds is None:
        return "at an unknown time"
    if seconds < 90:
        return f"{int(seconds)}s ago"
    if seconds < 5400:
        return f"{int(seconds / 60)} min ago"
    return f"{seconds / 3600:.1f} hours ago"


def _money(value: Any, currency: str = "") -> str:
    return f"{_number(value):,.2f}{' ' + currency if currency else ''}"


# --- reporting --------------------------------------------------------------
def summarise(config: Config, status: Dict[str, Any],
              publish_age: Optional[float] = None) -> str:
    """The answer to "how's my bot?"."""
    health = assess(config, status, publish_age)
    currency = str(status.get("currency") or "")
    mode = "dry run (no orders placed)" if status.get("dry_run") else "live orders"

    lines = [f"Bot: {health.headline}"]
    if health.state not in SILENT:
        lines[0] += f"   (heartbeat {_ago(health.heartbeat_age)})"

    lines.append(f"Account {status.get('login', '?')} — {mode}")
    lines.append(
        f"Equity {_money(status.get('equity'), currency)}   "
        f"balance {_money(status.get('balance'))}")

    dd = _number(status.get("drawdown_percent"))
    limit = _number(status.get("drawdown_limit_percent"))
    peak = status.get("peak_equity")
    line = f"Drawdown {dd:.2f}% of {limit:.2f}% limit"
    if peak:
        line += f"   (peak {_money(peak)})"
    lines.append(line)

    day_dd = _number(status.get("day_drawdown_percent"))
    day_limit = _number(status.get("day_loss_limit_percent"))
    lines.append(f"Today {day_dd:.2f}% of {day_limit:.2f}% limit")

    # Stated as fact, not as a warning. Whether it means anything is decided by
    # crash_looping(), which looks at the last hour rather than all history.
    restarts = int(_number(status.get("restarts")))
    if restarts:
        recent = status.get("restarts_last_hour")
        detail = f", {int(_number(recent))} in the last hour" if recent is not None else ""
        lines.append(f"Restarts {restarts} since it was first started{detail}")

    positions = status.get("open_positions") or []
    if positions:
        lines.append(f"Open ({len(positions)}):")
        for p in positions:
            profit = _number(p.get("profit"))
            lines.append(
                f"  {p.get('symbol', '?')} {p.get('side', '?')} "
                f"{_number(p.get('lots')):g} lots @ {_number(p.get('open_price')):g}"
                f"   {profit:+,.2f}")
    else:
        lines.append("Open: nothing")

    if health.concerns:
        lines.append("")
        for note in health.concerns:
            lines.append(f"Note: {note}")

    lines.append("")
    lines.append(f"Read from {getattr(config, 'bot_status_repo', '')} — "
                 "I can only report on the bot, not change what it does.")
    return "\n".join(lines)


def one_line(config: Config, status: Dict[str, Any],
             publish_age: Optional[float] = None) -> str:
    """A single line, for the status screen."""
    health = assess(config, status, publish_age)
    return (f"{health.headline}; equity "
            f"{_money(status.get('equity'), str(status.get('currency') or ''))}, "
            f"drawdown {_number(status.get('drawdown_percent')):.2f}%")


def publish_age_if_needed(config: Config, status: Dict[str, Any]) -> Optional[float]:
    """The publish age, but only when it could change the verdict.

    A bot that is reporting normally has nothing to explain, and this is a network
    round trip on the path of every "how's my bot?". So the extra request is spent
    only once the heartbeat has actually gone quiet -- which is rare, and is
    exactly when somebody is standing there wanting to know which window to go and
    look at.

    Lives here, and not in each of the three callers, because the threshold it
    compares against belongs to `assess` and having three copies of it is how they
    drift apart.
    """
    stale_after = float(getattr(config, "bot_stale_minutes", 15) or 15) * 60
    age = heartbeat_age(status)
    if age is None or age <= stale_after:
        return None
    return fetch_publish_age(config)


def alerts(config: Config, status: Dict[str, Any],
           publish_age: Optional[float] = None) -> List[str]:
    """Things worth interrupting someone for. Empty when all is well.

    Kept separate from summarise() because the threshold is different: a report
    can mention anything, an interruption has to earn it.
    """
    health = assess(config, status, publish_age)
    if health.state == "halted":
        return [f"Trading bot HALTED: {status.get('halt_reason') or 'kill switch'}"]
    if health.state == "bot_stopped":
        # Named as the bot, because that is now known rather than suspected, and
        # because the two cases send you to different windows on the VPS.
        return [f"Trading bot STOPPED — last heartbeat "
                f"{_ago(health.heartbeat_age)}. The VPS is still publishing, so "
                f"it is the bot that has stopped."]
    if health.state == "not_publishing":
        return [f"Trading bot status not published for "
                f"{_ago(health.publish_age)} — the publisher on the VPS has "
                f"stopped. The bot itself may still be trading."]
    if health.state == "stale":
        return [f"Trading bot not reporting — last heartbeat "
                f"{_ago(health.heartbeat_age)}"]
    if health.state == "paused":
        return ["Trading bot paused: daily loss limit reached, no new entries "
                "today"]

    out = []
    dd = _number(status.get("drawdown_percent"))
    limit = _number(status.get("drawdown_limit_percent"))
    if limit and dd >= limit * 0.75:
        out.append(f"Trading bot drawdown {dd:.2f}% — approaching the "
                   f"{limit:.2f}% kill switch")
    looping = crash_looping(status)
    if looping:
        out.append(f"Trading bot {looping}")
    for err in (status.get("recent_errors") or [])[-1:]:
        out.append(f"Trading bot error: {err.get('message', '')[:160]}")
    return out
