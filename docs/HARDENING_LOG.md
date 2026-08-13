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

## 7. Every check in this log had been written, run, and thrown away

**Symptom.** Entries 1 to 6 each end with a count — 73 checks, 39, 31, 19, 32.
None of them were in the repository. There was no `tests/` folder, no pytest
configuration, no CI, and no `.github/` directory at all. The counts were true
when they were written and had been meaningless ever since.

**Why that is worse than never having tested.** The log's own ninth lesson is
*"make the decision a pure function — an hour of watcher behaviour tested in
milliseconds found bugs that waiting never would."* `botwatch.decide()` is shaped
that way on purpose, and its docstring says so. But a pure function nobody calls
is just a function. Every one of these had drifted back to being unverified:
`decide()`, `wake.detect()`, `shell.check()`, `trading.assess()`,
`crash_looping()`, `providers.split_model_id()`, `code._safe_relpath()` — all
pure, all edge-case-heavy, several of them the only thing standing between a
model's suggestion and the filesystem.

And `selfupdate.py` overwrites this install's own source from a branch with
nothing in front of it. A syntax error pushed to `feat/beastt-ai-companion` ships
straight onto the laptop.

**Fix.** The counts are now real: **846 checks in ten files**, running in about
half a second, with a CI workflow on every push and pull request.

| File | Covers | Checks |
| --- | --- | --- |
| `test_trading_health.py` | severity ladder, staleness, crash-loop (entries 1, 4) | 83 |
| `test_trading_refusals.py` | the line it will not cross (entry 1) | 134 |
| `test_botwatch.py` | alert discipline, `decide()` (entry 2) | 49 |
| `test_routing.py` | skill precedence and phrasing (entry 3) | 75 |
| `test_longterm.py` | relevance without padding (entry 6) | 38 |
| `test_shell.py` | the three tiers of command vetting | 151 |
| `test_wake.py` | wake-word tolerance and false wakes | 61 |
| `test_pure_helpers.py` | model ids, path confinement, contrast, chat ids | 102 |
| `test_docgen_charts.py` | chart validation and citation honesty | 55 |
| `test_updater.py` | both updaters, and that they agree (lesson 7) | 98 |

Three decisions worth recording, because each was a trade-off:

**The suite runs with nothing installed.** `beastt/trading.py` imports `requests`
at module scope, so importing it on a bare machine fails. Refusing to run the
tests until someone pip-installs a networking library in order to check that
*"sell 0.04 lots of brent"* is refused is the kind of friction that stops a suite
being run at all — so `tests/conftest.py` stubs `requests` when it is genuinely
absent, and the stub **raises** on any actual call. A CI job installs nothing but
pytest specifically to keep that honest.

**Every historical bug was reintroduced to check the tests notice.** Twenty-six
mutations — padding put back into `relevant()`, `crash_looping()` pointed at the
lifetime counter, the `any update ON` lookahead deleted, the registration order
reversed, the toast quoting removed, project confinement disabled. All twenty-six
were caught. Two were instructive:

* The refusal gap widened in entry 1 is **not** pinned by that entry's own
  example. `"sell 0.04 lots of brent"` is matched twice over — by the widened gap
  *and* by the separate "direction with a size" branch — so narrowing the gap
  again leaves it passing. `"close 0.04 lots"` is the case that actually needs
  it: the verb is outside `buy|sell|long|short`, so only a gap that can span a
  decimal reaches the noun. A test that cannot fail proves nothing, which is the
  same lesson as entry 6's two tests that failed for the wrong reason.
* Weakening `_normalise_chart`'s category check changed no behaviour, because a
  later width guard rejects the same input. Defence in depth, confirmed by
  accident.

**Known gaps are recorded as tests, not comments.** Nineteen checks are marked
`xfail(strict=True)` with the reason written out: the suite stays green, the bug
is documented where someone will trip over it, and CI fails the moment it is
fixed without the marker being removed. What they cover:

* **A sourced chart loses its citation on revision.** `_normalise_chart()`
  rebuilds the chart dict with only `type`, `categories` and `series`, dropping
  `source`, `units` and `illustrative`. So a chart built from a real FRED series
  renders after any model-driven edit with **no caption at all** — precisely the
  unlabelled invented chart entry 5's sibling work set out to prevent. It cannot
  be recovered either: `attach_real_data()` *pops* `data_query`, and
  `Workshop.revise()` never calls it again. The flag is dropped in the worse
  direction too — a chart that correctly admitted its figures were invented stops
  admitting it.
* **`"what are my open positions"` is refused instead of answered.** `_ACT`'s
  first branch reads the verb `open` reaching the noun `positions`. This is the
  one place a false refusal is *not* harmless: it withholds the report the skill
  exists to give.
* **`"should I close brent?"` reaches the model.** No trading noun, no bot word,
  so neither `_ACT` nor `_ASK` matches — and the model then gives exactly the
  investment advice the module's docstring says is out of scope. `"reduce 2.5
  lots"` escapes the same way; `reduce` is not in the verb list.
* **BEASTT wakes on the word "best".** `wake.py` states that `_SIMILARITY = 0.85`
  is "kept high so everyday lookalikes (e.g. `best` vs `beast`) don't trigger a
  false wake". `SequenceMatcher("best", "beast").ratio()` is **0.889**, so an
  assistant renamed to BEASTT wakes on `best` and on `breast`. 0.90 fixes both
  and still matches every shipped mishearing. The default name JARVIS is
  unaffected.
* **`git config` is on the read-only allowlist**, so
  `git config --global user.email x` runs with no confirmation.
* **Only three commands are actually confined.** `cat`, `ls` and `mkdir` go
  through `_resolve_inside`. `head`, `tail` and `find` are allowlisted but reach
  `subprocess` directly, so `head /etc/passwd` is "read-only".
* **`docker exec` is permanently blocked**, because the denylist entry is
  `\beval\b|\bexec\b` and matches the word anywhere. A block cannot be confirmed
  past, so there is no way for the user around it.
* **`python -V` is not allowlisted** although `("python", "-V")` is in the table:
  `check()` lowercases the first two tokens before comparing, so the entry is
  unreachable. A one-character fix.
* **`closing` is advertised but not rendered.** It is in `layouts.CATALOGUE`, so
  it is offered to the model and listed by `describe()`, but has no entry in
  `RENDERERS` — a slide asking for it silently comes out as bullets.

846 checks. Nothing in the source was changed to make a test pass.

**One source change was needed to make the suite reachable at all**, and it is
the same bug as the web assets. Both updaters carry a fixed list of file
suffixes, and `.ini` was not on it — so `pytest.ini` would never arrive on an
install, and the suite would land with nothing telling pytest where to look or
which markers exist. `.ini`, `.cfg`, `.toml`, `.yml` and `.yaml` are now synced.

While there, `update.py`'s skip list was aligned with `selfupdate.py`'s. The
hand-run updater skipped only `beastt_memory/` and `.git/`, leaving
`beastt_output/`, `beastt_workspace/` and `jarvis/` fair game — and a scaffolded
project contains `.py` files at paths like
`beastt_workspace/proj/tests/test_x.py`. Nothing in the repository collides
today; the in-app updater has always skipped those folders, and the hand-run one
promising less was an accident rather than a decision.

`test_updater.py` asserts every rule against **both** implementations, plus that
they agree file-for-file, share a suffix list, share a skip list, and target the
same branch. A file type one syncs and the other does not is invisible until
something is missing at runtime — which is how this happened twice.

**Updating past this commit needs `python update.py` run twice.** The first run
replaces `update.py` itself, but that run's file list was already built with the
old suffix list, so `pytest.ini` only arrives on the second.

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
11. **A test that is not committed is a test that ran once.** Six entries above end
    with a count of checks that no longer existed. Making the decision a pure
    function (lesson 9) only pays if something still calls it — otherwise the
    design intent survives and the verification does not.
12. **Reintroduce the bug to prove the test.** Two of these checks passed against
    the broken code: one because the example in this log is caught by a second
    pattern, one because a later guard rejects the same input. Both looked like
    coverage and were not.

## Scope

The trading monitor is **read-only**, permanently. It reports; it does not trade, pause,
restart or size anything. That boundary is enforced in code, tested, and is not a
setting.
