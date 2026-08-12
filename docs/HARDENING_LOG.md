# Hardening log: the trading monitor, and the fixes it provoked

The README describes what BEASTT does. This records what went **wrong** while
building the trading-bot monitor, and what each fault changed.

Every entry is a real failure from a live session, not a hypothetical. Several were
reported by the user within minutes of a feature shipping, which is the useful kind
of feedback and the reason this file is worth keeping.

> Companion file: the bot side of this work is logged in the Tradingbot repository
> at `docs/HARDENING_LOG.md`. The two were built together.

---

## The setup

Two machines, deliberately separated:

| Where | What runs | Path |
| --- | --- | --- |
| Windows VPS | The trading bot + status publisher | `C:\Tradingbot` |
| Laptop | BEASTT / JARVIS | `C:\project-beastt-feat-beastt-ai-companion` |

They never talk directly. The bot publishes `status.json` to a **private** GitHub
repository (`klingesh/tradingbot-status`) every 300 seconds, and BEASTT reads it
back. No open ports on the VPS, no SSH into a Windows box reached by RDP, and the
same report is readable from a phone.

---

## 1. The monitor itself, and the line it will not cross

`beastt/trading.py` + `beastt/skills/trading_skill.py` answer "how's my bot?" with
heartbeat age, equity, drawdown against both limits, and open positions.

**Read-only, and deliberately so.** Requests to act — close a position, open a buy,
stop the bot — are matched and **refused explicitly** rather than left to fall
through to the model, which would answer as though it might comply. A language model
in the order path is a bad idea however good the reporting around it gets.

It also declines to **opine** on the positions it reports. That Brent is short and
2.80 up is a fact; what to do about it would be investment advice.

**Staleness was the subtle part.** The threshold must exceed the *publisher's*
interval, not the bot's poll interval. The heartbeat is written every 60 seconds but
only published every 300, so a perfectly healthy bot legitimately looks five minutes
old from here. Set the threshold too low and the assistant cries wolf every few
minutes — which trains its owner to ignore the one alert that matters.

**Severity is assessed in a fixed order** — halted, stale, paused, running — so a halt
is never masked by a stale heartbeat. That is exactly the pair you get when a bot
kill-switches and the VPS then reboots.

A `404` names **both** of its possible causes. GitHub returns 404 rather than 403 for a
private repository a token cannot see, so a wrong repo name and a missing permission
are indistinguishable from here — guessing one would send someone debugging the wrong
thing.

**Two refusal gaps found while testing.** `"open a buy on gold"` had no trading noun
after the verb, and `"sell 0.04 lots of brent"` broke because the gap pattern was
`[^.\n]`, which cannot span a decimal point. In this skill **a false refusal is
harmless and a missed one is not**, so both were widened.

73 checks, using the real published status as the fixture, including the live Brent
short. Commit `17c9dfa`.

## 2. Asking only works if you think to ask

**Symptom.** Twenty minutes after the monitor shipped: the bot was restarted, the
publisher was not, and for **sixteen minutes** BEASTT answered "how's my bot?" with a
stale snapshot — right down to a position label that had since been fixed. Nothing
indicated anything was wrong.

**Fix.** `beastt/botwatch.py`, a proactive watcher. Detecting trouble is the easy
part; not becoming noise is the design problem.

- **Alert on change, not on level.** A notification every five minutes trains its
  owner to dismiss notifications, and then the one that mattered is dismissed too.
- **Remind hourly while a problem persists.** A kill switch at 3am should still be
  visible at breakfast.
- **Say so when it recovers.** Without that, the last thing you were told is still
  bad news.
- **Lesser concerns are reported once**, via a signature that excludes equity and the
  heartbeat — including those would make every check look like news.

**Being unable to read the status at all** is treated as the same condition as a stale
heartbeat, because from the user's side it is the same problem.

**A quoting bug worth naming.** `notify.toast()` builds a PowerShell command by string
interpolation, and halt reasons come **from the bot** — so that text is not under our
control. A single quote would end the string literal and mangle the notification.
`safe_for_toast()` strips quotes, collapses newlines and bounds the length.

**`decide()` is a pure function** of previous state, current health and the clock, so an
hour of behaviour is tested in milliseconds instead of waited for. That is deliberate:
**both bugs in this feature's first day were hidden by code that could not be run.**

The watcher is a daemon thread started by the service. It waits thirty seconds before
its first check so a network blip at boot is not reported as a dead bot, and every
check is wrapped — a watcher that can take down the assistant is worse than no watcher.

39 checks. Commit `6beb002`.

## 3. "any update on my bot" was answered about BEASTT's own code

**Symptom.** Reported from a live session:

> **any update on my bot**
> I'm up to date (latest change: Merge pull request #25 from klingesh/feat/bot-alerts).

That is BEASTT's git revision. The question was about a trading bot.

**Two causes, both fixed** rather than either alone.

`MaintenanceSkill._CHECK_UPDATE` contained a bare `"any updates?"`, which matches the
`"any update"` inside `"any update on my bot"`. A negative lookahead now excludes a
following `on/for/about/to/regarding/from/with`: **"any updates?" alone asks about
BEASTT; "any update ON something" asks about the something.**

And the registration order had `MaintenanceSkill` checked first. `TradingBotSkill`'s
patterns all require an explicit reference to the bot or to trades, which makes it the
**more specific** of the two, so it is registered last and therefore checked first.
Specific before general.

Either fix alone would have handled this phrase. The pair handles the ones nobody
thought of.

**Two incidental findings in the same file**, both natural phrasings that silently
reached the model instead of the skill:

- `"run a diagnostic"` failed on the article, where `"run diagnostics"` worked.
- `"what's wrong"` was absent altogether, though `"what went wrong"` was there — and
  **the present tense is what someone types when something is wrong now.**

31 routing checks, including confirmation that with no bot configured the update
questions behave exactly as before. Commit `b90044a`.

## 4. The crash-loop warning could never expire

**Symptom.** From a live report:

```
Note: it has restarted 9 times — possibly crash-looping
```

The bot had been stable for hours. Those nine restarts were **manual**, during an
unrelated cleanup that morning.

**Cause.** `restarts` is a lifetime counter that never resets. So the warning was not
merely wrong — it would have repeated on **every report for the life of the install**.
A warning that cannot expire is not a warning; it is a thing you learn to scroll past,
and then the real one goes past with it.

**Fix.** `crash_looping()` judges `restarts_last_hour`, published by the bot alongside
the lifetime total, and fires at three within the hour. The lifetime figure moves into
the report as a plain statement of fact, with the hourly count beside it — the history
is worth knowing even when it means nothing is wrong now.

**An older bot publishes only the total.** Rather than infer a rate from it, say
nothing: a false alarm every five minutes forever is worse than a missed one, and the
count is still shown either way.

The same function now drives **all three paths** — the report, the concern list and the
proactive alert — which is how it emerged that `alerts()` had its own copy of the
threshold and would have kept the old behaviour.

19 checks, including the reported false alarm written out as a fixture. Commit `4c4cbd6`.
Requires the bot-side fix `99bfc4f`.

## 5. `update.py` broke the moment the repository went private

**Symptom.** Every update failed with a bare `404` and no indication why.

**Why that is nearly fatal.** `update.py` **is** how this install receives code. If it
breaks, there is no way to pull the fix for it.

**Cause.** It fetched the file list from `api.github.com` and each file from
`raw.githubusercontent.com`, both unauthenticated.

**Fix.** If `BEASTT_GITHUB_TOKEN` is set, sign the requests and fetch contents through
the Contents API with `Accept: application/vnd.github.raw`, which is the documented way
to read a file from a private repository. Without a token nothing changes —
`raw.githubusercontent` stays the default because it is quicker and counts against no
rate limit.

**The token is read straight out of `.env`**, not through `beastt.config`, because this
script has to work *before* the code it is updating is in place — including when a
broken module is the reason someone is running it. Trailing comments, quotes and stray
whitespace are all handled, since this file is edited by hand in Notepad.

Errors now say what to do: a 403/404 **without** a token says a private repository needs
one and names the setting; **with** a token it says the token was refused rather than
blaming the branch name. The header line reports which path was taken:

```
Updating BEASTT from klingesh/project-beastt (feat/beastt-ai-companion) [signed in]...
```

Commit `bed1321`.

## 6. Memory dropped irrelevant people into replies

**Symptom.** Asked to generate an image, the reply recommended a particular friend for
artistic advice. Asked nothing at all — just `"Hello."` — it volunteered that friend's
opinion of the user's mood. She had nothing to do with either conversation.

**Cause.** The last few lines of `longterm.relevant()`:

```python
# If nothing matched, fall back to the most recently updated facts.
if len(picked) < min(limit, len(self.facts)):
    ...pad with the most recently updated...
```

A genuine token match is rare and the limit is eight, so **that fallback fired on almost
every turn.** A question about drawing a cat arrived carrying seven unrelated facts —
and a model handed facts about a person finds a use for them.

Relevance was the entire point of the method. Padding to a quota guaranteed the
opposite.

**Fix.** No padding. When nothing matches, nothing is sent. Core facts still go through,
because who someone is and what they like to be called bear on every reply. *"What do
you remember about me?"* is unaffected — `MemorySkill` answers that and does not come
through here.

**The instruction was the other half.** It said only "use them naturally when relevant",
which reads as a *promise* that the facts were relevant — and the recall code had just
finished making that false. It now says most replies will need none of it, forbids
steering the answer towards it, and names the specific failure: do not bring up a person
who has nothing to do with what was asked. It moved to a module-level function so the
wording can be asserted.

Both reply paths and the deliberate planner share this recall, so all three benefit.

**Two tests that failed for the wrong reason**, both worth recording: one grepped the
source across wrapped f-string lines (which is what prompted extracting the instruction
into a function), and one collided with the store's de-duplication, which had merged two
fixtures into one. A test that fails for the wrong reason teaches nothing.

32 checks. Commit `880cdc0`.

---

## Operator notes

**Restarting BEASTT after an update.** The background service holds the old code, so
`update.py` alone changes nothing:

```
python update.py
taskkill /F /IM pythonw.exe
wscript "C:\Users\Lingesh K\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\BEASTT.vbs"
```

**Confirming the watcher started.**

```
findstr /C:"botwatch" C:\project-beastt-feat-beastt-ai-companion\beastt_memory\beastt.log
```

Expect one line: `[botwatch] Watching klingesh/tradingbot-status every 5 min.` If it is
absent, the token or repo setting is missing — the watcher fails quietly by design
rather than taking the assistant down with it.

**Log output is not a command.** Pasting a line like `[botwatch] Watching ...` back into
`cmd` produces `'[botwatch]' is not recognized as an internal or external command`. It is
output, not input.

---

## The lessons

1. **Refuse explicitly; do not rely on the model to decline.** Silence falls through to
   a model that will answer as though it might comply.
2. **A false refusal is cheap. A missed one is not.** Widen the patterns.
3. **Thresholds belong on rates and changes, never on lifetime totals** — see entries 2
   and 4. A number that only rises fires forever once crossed.
4. **Tune staleness to the publisher's interval, not the bot's.** Otherwise the
   assistant cries wolf and gets trained out of being believed.
5. **Order severity checks so the worst condition cannot be masked** by a lesser one.
6. **Specific skills must be checked before general ones**, and a phrase like "any
   update" needs to know what it is *about*.
7. **The updater is the lifeline.** It must keep working when everything it updates is
   broken.
8. **Padding a relevance filter to a quota inverts its purpose.** Returning nothing is a
   valid answer.
9. **Make the decision a pure function.** An hour of watcher behaviour tested in
   milliseconds found bugs that waiting never would.
10. **Text from another system is untrusted input** — even when that system is your own
    trading bot, and even when it is only going into a toast notification.

## Scope

The trading monitor is **read-only**, permanently. It reports; it does not trade, pause,
restart or size anything. That boundary is enforced in code, tested, and is not a
setting.
