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

**Two more tests that failed for the wrong reason**, in the tradition of entry 6,
both found by running the suite on the laptop rather than the machine it was
written on:

* **A literal heartbeat timestamp.** One check moved the equity and the heartbeat
  and asserted the signature had not changed. It wrote the heartbeat as
  `"2026-08-13T08:59:30+00:00"` — which is a *fresh* heartbeat for fifteen
  minutes and a stale one for ever afterwards. So it passed where it was written
  and failed that afternoon, reporting a state change that was really a clock.
  The heartbeat now moves relatively, like every other one in the suite.
* **The `.env` leaked into the fixture.** `Config`'s defaults are read from the
  environment when the class body runs, so a real `.env` is baked in before any
  test can intervene — monkeypatching afterwards is too late. The fixture named
  the fields it thought mattered and missed the five provider API keys, so two
  checks asserting *"a cloud provider with no key is unconfigured"* failed on a
  machine that had a Groq key, against a provider that was correctly configured.
  The keys are now blanked from the provider registry itself rather than listed,
  so a sixth provider cannot reintroduce it, and an autouse fixture fails the run
  if any real key is ever carried in again.

The shared shape is worth naming: **both passed on the author's machine for
reasons that had nothing to do with the code.** A suite that only runs in one
place is only a little better than one that never runs.

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

## 8. It volunteered a friend's name, and invented the numbers it was asked for

Two complaints from one session, with different causes and one thing in common:
in both cases the assistant produced something confident out of nothing.

### 8a. A friend in every reply

**Symptom.** Three replies, quoted from the session:

> **nothing much jarvis**
> Sometimes doing nothing can be nice too, Lingaa. ... **How's Prahathi doing, by
> the way?** Haven't heard about her in a while.

> **so whats happening in tamilnadu, india**
> [a summary] ... **How's your friend Prahadhesvaryaa doing, by the way?**

> **the values are wrong, can you stop hallucinating**
> I couldn't find a direct connection between the information about you,
> **possibly referring to the 2014 Indian film starring Rajinikanth**, and
> Prahadhesvaryaa K S to the complaint about incorrect values.

**Cause.** Entry 6 removed the padding, and the same facts came back through the
other door. `relevant()` still returned every `core` fact unconditionally, and
`core` is set by exactly one thing -- `MemorySkill` handling *"remember that
..."*. So being asked once to remember a friend put her in every prompt for good.
The reasoning in the comment was right -- what someone likes to be called is
relevant to every reply -- but **`core` was the wrong carrier for it**: it means
"the user asked me to keep this", not "this is who the user is".

**The third reply is a different bug with the same root.** `_deliberate()` handed
those facts to the research planner as its *context*, so the planner treated the
user's own nickname as a subject to be researched, searched "Lingaa", and found a
Rajinikanth film. Background about who is asking is not material to be
researched.

**Fix.** Only identity facts bypass relevance, via a narrow `is_identity()`
requiring an identity phrase with the *user* as its subject. "Lingaa likes to be
called Lingaa" passes; "Prahadhesvaryaa is Lingaa's close friend" does not.
`core` still earns a scoring boost and protection from eviction -- it just has to
match the question first. The always-on set is capped at three so it cannot
reassemble itself one fact at a time. And the planner gets attachments but no
memory.

**Nothing is forgotten**, which was the actual requirement: she is recalled the
moment she is mentioned. The ask was never "forget my friend", it was "don't bring
her up at random".

### 8b. Five gainers, five losers, all invented

**Symptom.**

> **how about top gainer stocks and loser stocks state 5 nos in india today**
> According to Moneycontrol, here are 5 top gainer stocks ...
> 1. Adani Enterprises - up 4.55%   2. Vedanta - up 3.65% ...

Then, asked for prices: `Rs 2,341.90`, `Yesterday's close: Rs 2,234.15`. Then
Tata Steel at `Rs 117.45`. Then, corrected to 184.60 by the user: *"According to
my latest update, the current share price of Tata Steel is indeed ₹184.60, as you
mentioned."*

**Four causes, stacked.**

1. **No search ran for the worst one.** The trigger was `\btoday'?s\b`, which
   matches "today's" and not "today". So the question retrieved nothing at all and
   the model was the only thing left to answer it. *"the values are wrong ... do
   some research"* did not trigger a search either.
2. **There was no source for a share price anywhere.** `data.py` is FRED and the
   World Bank -- right for inflation, silent on equities. Nothing could have
   answered that question correctly.
3. **Nothing checked the output.** The persona forbids exactly this failure, in as
   many words: *"Quoting a remembered number as though you had just fetched it is
   the same failure as inventing a filename -- worse, because a number looks
   checkable."* An instruction is not a mechanism. A warm, helpful 8B model fills
   a gap rather than admitting one.
4. **The persona pushed the wrong way when retrieval was empty.** It says the
   assistant *can* look things up and should never claim otherwise -- true, and
   still sitting in the prompt when a search returns nothing, at which point the
   model narrates a lookup that did not happen.

**Fixes, in the order they matter.**

* **A grounding check, deterministic.** Every figure in a reply must appear in the
  material actually retrieved. If it does not, the model is asked again with the
  offending numbers named; if the second attempt still cannot stand them up, the
  reply ships with a visible admission of which figures are unsourced. No second
  model call in the common case, so it costs nothing when the answer was already
  honest. **A number the user supplied does not count as a source** -- agreeing
  with someone is not verification, which is what produced "indeed ₹184.60".
* **Live quotes**, in `quotes.py`: Yahoo Finance with a Stooq fallback, keyless,
  carrying the previous close so the change is arithmetic done here rather than by
  a language model, and a timestamp because a price without one is barely a fact.
  Name resolution goes through a search endpoint, so "tata steel" and "apple" both
  work without a hard-coded table.
* **A ranked list is refused rather than approximated.** "Top gainers" needs a
  screener, which there isn't one of. Same shape as the trading monitor's refusal:
  say so, offer individual quotes, name where the full list lives.
* **Search that researches.** Triggers widened substantially -- the bias is now
  that a needless search costs a second while a missing one costs a fabricated
  answer. Questions are classified (quote / news / product / factual) and asked
  several ways, because one query is one engine's opinion: asked what was
  happening in Tamil Nadu, that opinion was an encyclopaedia entry on culture and
  tourism, which the reply presented as the day's news. **And the top pages are
  now opened and read**, rather than answered from twenty-word snippets.
* **A no-results note**, the mirror of `data.no_data_prompt`, so an empty
  retrieval reads as a gap to admit rather than licence to answer from memory.

**Two tests that proved nothing until they were rewritten.** Reverting `today'?s?`
to `today'?s` broke no test, because the real question also matches on "top" and
on "gainer". Removing the `i heard` trigger broke none either, for the same
reason. Redundant triggers are good in the code and useless in a test, so each new
trigger is now pinned by a *pair* -- the same sentence with and without it. Nineteen
of the twenty mutations were caught before this; the two that were not were both
in this class.

290 checks across `test_memory_relevance.py`, `test_grounding.py`,
`test_quotes.py`, `test_research.py` and `test_answer_pipeline.py`, the last of
which runs the transcript's worst turns through a real `Assistant` with a stubbed
brain -- because the parts passing does not prove the pipeline does.

### 8c. What the first live run then found

The fixes above were written on a machine with no route to the open web, so the
parsers were exercised against fixtures and the live paths were not exercised at
all. Running it for real found three things, in rising order of interest.

**The quote path worked.** *"whats tata steel current share price"* returned
`184.90 INR on the BSE, as of 2026-08-13 10:23 UTC, change -0.55 (-0.30%) from a
previous close of 185.45` -- fetched, timestamped, and attributed. The memory fix
worked too: *"nothing much jarvis"* twice, with no friend volunteered, and *"how
is prahadhesvaryaa doing"* recalled her immediately.

**A caveat fired on a good answer.** The Tamil Nadu roundup -- five outlets, real
stories, relative timestamps -- ended with *"I could not verify these figures
against any source I retrieved: 40"*. The "40" was from "(about 40 minutes ago)",
an interval the assistant had computed itself from a page's timestamp. There was
nothing to verify. Worse, the same flaw meant "as of 2026-08-13 10:23 UTC" was
being read as the four separate figures 2026, 13, 10 and 23, which had passed only
because the quote block happened to contain the same digits; a reply that
reformatted the timestamp would have been flagged for it. Dates, clock times and
relative intervals are now masked before figures are extracted. **"40 minutes ago"
is narration; "lasted 40 days" is still a claim.**

**And the interesting one: every figure was real, sourced, and two years old.**
Asked for today's top gainers, the assistant read a Moneycontrol page, took five
movers and their percentages off it, and gave them as the day's biggest. The
grounding check passed it -- correctly, because every number was on the page. The
page was from **August 2024**.

> **Grounding proves a figure came from a source. It does not prove the source was
> current, and those are different claims.** Only the first is decidable by
> comparing strings, which is exactly why it was the one implemented, and exactly
> why it is not sufficient on its own.

So pages are now dated. `stale_hint()` reads the years a page mentions and, when
the current one is absent, reports the most recent year it does carry. The hint
travels with the source into the prompt, and every instruction now requires that a
figure taken from a dated source is given *with* that date and never presented as
today's. The check is deliberately blunt -- it misses an undated page and will
occasionally mislabel a historical article, both of which cost a caveat, whereas
presenting two-year-old prices as live costs the answer.

The movers rule changed with it. Flatly forbidding a ranked list was aimed at the
right failure and was the wrong rule; the model half-ignored it anyway, and when a
real page has been read its figures are worth having. It now says *report it, name
the page, give its date, say it is a snapshot* -- because the difference between a
useful answer and a wrong one here was never the list, it was the missing date.

**A mutation that could not be observed, which turned out to be a design error.**
Removing the guard that skipped staleness checks for unread pages broke no test --
because an unread page has no text, so the check returned nothing either way. The
guard was defensible on the stated reasoning (a snippet is too short to carry a
dateline) and the reasoning was simply wrong: search engines routinely put the
publication date at the *front* of a snippet, and when a page will not load that
snippet is the only place its age is visible. Snippets are now judged too. The
mutation check did not find a missing test here; it found a line whose only
justification was an assumption nobody had checked.

44 further checks in `test_staleness.py`. All ten applied mutations caught.

### 8d. "Where do I live" could not find "lives in Chennai"

Recorded as a known gap in 8a and closed here, because it is the kind of fault
that costs trust rather than correctness.

**Symptom.** Recall is exact-token overlap, so *"where do I live"* produced no
match against *"Lingaa lives in Chennai"* -- `live` and `lives` are different
strings. The fact was present, indexed, and simply never scored. From the outside
that is indistinguishable from the assistant having forgotten, and forgetting is
the one thing a memory feature cannot be caught doing.

**Why it waited.** `_tokens()` feeds three things with three different costs:

| Caller | Threshold | Cost of a *missed* match | Cost of a *false* match |
| --- | --- | --- | --- |
| `relevant()` | any overlap | a fact is not recalled | an irrelevant fact is injected |
| `add()` | 0.8 | a fact is stored twice | **one of two distinct memories is destroyed** |
| `forget()` | 0.5 | nothing is removed | **something nobody asked to lose is deleted** |

Widening matching helps the first and endangers the other two, which is why this
was not a one-line change and why the tests are in two halves: the forms that must
meet, and the words that must stay apart.

**Fix.** `stem()`, thirty lines of suffix rules and no dependency. Plurals
(`laptops` → `laptop`, `cities` → `city`, `classes` → `class`), the `-s` verb form
(`lives` → `live`), and `-ing`/`-ed` with the two cases that make a naive version
useless: a doubled consonant is undoubled (`running` → `run`) and an elided "e" is
restored when one was probably there (`lived` → `live`, `hoped` → `hope`). The
signal separating those is that **English doubles a consonant precisely to stop
the preceding vowel being read as long, so a doubled base never wanted an "e"** --
which is what keeps `running` from becoming `rune`.

Two guards do the defensive work. A `_NEVER_STEM` set, because "analysis" is not a
plural and stripping its "s" would fuse it with unrelated facts; and a
three-character floor, because the `-s` rule would otherwise turn "gps" into "gp".

**The strict-xfail gate paid for itself here.** The known-gap test from 8a started
*passing* the moment the stemmer landed, and `xfail(strict=True)` failed the run
for it -- `XPASS(strict)` -- forcing the marker off rather than letting a fixed bug
sit in the suite still described as broken.

**And one test was quietly vacuous.** Removing the three-character floor broke
nothing, because the words that test used -- "cat", "his", "was", "us" -- are all
protected by some *other* rule, so none of them changed. It asserted an invariant
it never exercised. Replaced with "gps", "sms", "abs" and "ops", which the missing
floor really does damage. That is twice now that the mutation check has found not a
missing test but **a passing one that proved nothing**, which is turning out to be
its most useful property.

110 checks in `test_stemming.py`. All thirteen applied breaks caught.

### 8e. "i live in chennai jarvis fyi"

The worst answer this project has produced, and the most instructive, because
every safeguard built in 8b worked correctly and the answer was still wrong.

**Symptom.**

> **i live in chennai jarvis fyi**
> Got it, Lingaa -- so you're already enjoying the **Jarvis community** in
> Chennai! It's the Casagrand-built, 8.5-acre gated enclave with 469 apartments,
> 2/3/4 BHK options and over 100 amenities, slated for handover in March 2028.

Then, when corrected:

> **i live in pg not casagrand**
> Nice to hear you've got a PG set up in Chennai! Stanza Living has a bunch of
> options -- a triple-share in Porur starts around Rs 19,199 per month ...

**Two faults, compounding.**

*The message was a statement.* A fact was being offered, to be remembered. It was
researched instead, because `live` had been added as a recency trigger for "live
price" and "i **live** in chennai" contains it. The bias set in 8b -- that a
needless search is cheap -- is right for questions and wrong for statements,
because researching a statement does not produce a useless answer, it produces a
confident answer about something else.

*Then the assistant's own name went into the query.* The lead-in stripping
carried a hardcoded `beastt` and this assistant is called Jarvis, so "jarvis"
survived into the search. There is a Chennai apartment development called Jarvis.

**Why the safeguards could not help.** The grounding check passed that reply, and
had to: every figure was on a page that had genuinely been retrieved. The
staleness check passed it too -- the page was current. Both mechanisms answer
"did this come from a source", and neither can answer **"is this source about the
thing that was asked"**. That is the third question, and the only defence against
it is not to run the search in the first place.

**Fixes.** `looks_like_statement()` -- first-person, no question mark, and none of
the words that turn a sentence into a request. Hearsay is deliberately excluded:
"i heard a student was murdered" is first-person and is the clearest possible ask
to go and check. `strip_assistant_name()` removes every spelling the assistant
answers to, reusing `wake.variants_for()` so a rename and a mishearing are both
covered. And `live` now needs a noun after it.

**Three more things the same session exposed.**

* **Reference markers were read as figures.** The model emitted its own citations
  as `【2†L31-L35】`, and the caveat came back reading *"I could not verify these
  figures: 35, 61, 64, 17, 22"* -- line numbers, and meaningless to anyone reading
  it. Masked now, and the instruction asks for plain citations instead.
* **The retrieved block had no ceiling.** Three pages at six thousand characters
  plus a dozen snippets is over twenty thousand -- roughly five thousand tokens
  before the persona, conversation and memory. The per-page limit had always been
  there; the total was what mattered and nothing measured it.
* **A rejected request said only "(HTTPError)".** `ollama_brain` called
  `raise_for_status()`, so the user saw the exception class and nothing about the
  cause -- for a fault that was, in fact, the prompt being too long. It now reads
  Ollama's own reason and, for the context-length case, names the setting that
  fixes it.

**Two mutations that could not be observed.** Deleting the bracket-marker rule
broke nothing, because the line numbers inside those markers were also caught by
the line-reference rule and the source number was filtered as structure -- so a
test using the session's own marker could not tell the rule existed. Fixed with a
marker citing source 12 rather than source 2. And removing the assistant's name
from the `needs_search` call broke nothing either, because "jarvis" is stripped as
a shipped name whether or not the configured one is passed; that line only matters
for a renamed assistant, so it is now pinned by asserting the argument rather than
a behaviour. Third and fourth time this check has found a passing test that proved
nothing.

87 checks in `test_statements_and_names.py`. All sixteen applied mutations caught.

### 8f. It was told where the user lived, and forgot

**Symptom.** Said, restarted, gone:

> **so jarvis where do i live**
> You live with Prahadhesvaryaa K S, but I don't recall you mentioning the exact
> location -- would you like to share that with me, Lingaa?

**Three faults met to produce that answer.**

*Nothing said in the browser was ever remembered.* `remember_session()` is called
from four places in `cli.py` and from nowhere else, so the entire web interface
wrote no durable facts at all. Every fact a browser conversation produced was
lost. And in the CLI it only runs on a *graceful* exit -- while the restart
procedure documented in these very operator notes is `taskkill /F`.

*A plain statement was never captured.* `MemorySkill` needs the literal word
"remember", so "i live in chennai" had no path into the store except
end-of-session reflection, which needs a model call and a clean exit.

*And 8e closed half a loop.* It correctly identified a first-person statement as
**a fact being offered, to be remembered** -- stopped researching it, and then did
not remember it. The judgement was right and only half-used.

The fact it answered from was itself invented: end-of-session reflection had
turned somebody being a *friend* into a living arrangement.

**Fix.** `statements.fact_from()` captures durable facts as they are said,
deterministically -- no model call, no waiting for the session to end, no
dependence on how the process dies. A model is still better at distilling a whole
conversation and still does that, but **"I live in Chennai" does not need a
language model to be understood, and making it depend on one is how it came to be
lost.**

Three things the design turns on:

* **Superseding.** Facts sharing a key are alternatives, so the newest wins:
  nobody lives in two cities. Facts without one accumulate, because somebody can
  own two laptops. Living *in* a place and living *with* someone get separate
  keys, so neither erases the other.
* **Keys are inferred for facts already on disk.** Keys are new, so every stored
  fact predates them. Without reading a key back off the sentence, superseding
  would pass every test and never once fire on an install that had been running
  for months.
* **Denials remove.** "i don't live with prahadhesvaryaa" is how someone corrects
  a fact that was wrong, and this session had one to correct.

The web interface now reflects every six assistant turns, on a background thread.
On a timer rather than at the end, because a web chat has no end -- the tab is
closed or the machine sleeps, and there is no exit hook to hang it on.

**Five mutations were not caught, and three were real.** The transient guard was
unpinned -- every case in that test was rejected for not matching any pattern at
all, so the guard could have been deleted with the suite still green; it now has
cases that *do* match and are still rejected ("i have a meeting today"). The
stored key was unpinned, because `infer_key` made dropping it behaviourally
invisible. And the one line in the streaming handler that triggers reflection was
unpinned, that line being the entire fix -- the third call site in this project
found this way, after `gather`'s name argument and a stub whose signature had
fallen behind its caller.

The other two were harness bugs worth recording for the shape of them. `return ""
or (f"...")` does not disable anything, because the f-string is truthy. And
removing the `^` from a pattern changed nothing, because `.match()` anchors
regardless -- the anchoring is held twice over, and only removing both defences
fails a test.

99 checks in `test_remembering_statements.py`. All seventeen applied mutations
caught.

---

## 9. A new front end talking to an old back end

**Symptom.** Minutes after an update that installed cleanly, pasting a screenshot
into the chat:

> I couldn't attach pasted-2026-08-15-10-50-20-09.png: I can't read '.png'. I
> handle PDF, Word, Excel, PowerPoint, CSV, JSON, text and source files.

Nothing in the repository could produce that sentence. The filename
`pasted-...png` is invented by pasting code that only exists in the new version;
the refusal is the wording of the *old* uploader, which had never heard of images.
Both halves were current at the same moment.

**Cause.** An asymmetry nobody had written down. The interface's Javascript, HTML
and CSS are read from disk on **every request**, so the front end updates the
instant the files change. Its Python was read into memory when the process
started and stays there until the process ends. So `update.py`, run with the
server already open — which is how everyone runs it — upgrades one half and not
the other, and the result is error messages describing a version of the program
that exists nowhere on disk. Unsearchable, and indistinguishable from an update
that failed.

The advice was already in these operator notes. It was in the notes and not in
the program, which is the same as not existing.

**Fix.** `beastt/freshness.py` hashes the package's `.py` files when the server
starts and again whenever asked. `/api/status` carries the verdict, the page asks
at load and every twenty seconds after, and a stale server says so in the banner
with the command to fix it. `update.py` closes the other end: after writing
files it looks for a listening interface and a windowless BEASTT, and names what
is still holding the old code.

Four decisions worth keeping:

* **Not a reload.** Swapping modules under a live server means open sockets,
  threads mid-reply and two copies of a class failing `isinstance`. The restart
  takes two seconds and is honest.
* **Contents, not timestamps.** A stat cache went in and came straight back out:
  two writes close together share an `mtime` on some filesystems, so a same-size
  edit inside one tick was invisible. The tests that caught it are in
  `TestTimestampsAreNotTrusted`, and they caught it on the machine this is
  developed on, not in theory.
* **`.py` counts, static assets do not.** The half that already updates itself
  must not raise a restart notice, or the notice becomes noise.
* **Every `.py`, not only the imported ones.** Being exact would mean walking
  `sys.modules`, which fails in the worse direction: lazily imported modules —
  `imageread` among them — are absent at check time and would report as fine.
  Over-reporting costs two seconds; under-reporting costs an evening.

`sys.platform`, not `os.name`, for the two Windows branches. A test that patched
`os.name` changed which class `pathlib.Path` instantiates and took pytest's own
internals down with it.

**Three mutations were not caught, and two were real test weaknesses.** Asserting
`"noteStaleCode(status.code)" in app.js` passed against a mutant that commented
the call out, and `"staleNoticed" in app.js` passed against one that renamed the
declaration and left every use pointing at nothing. The third was the guard that
stops `update.py` shelling out to `tasklist` on a machine that has none: the
function swallows exceptions, so removing the guard still returned `False` — it
just started a process to do it. The call itself is now what is asserted.

68 checks in `tests/test_code_freshness.py`. All 26 applied mutations caught.

---

## 10. "Either the bot has stopped, or the publisher has" — and it was the publisher

**Symptom.** The monitor working exactly as designed, and being no help:

> Bot: NOT REPORTING — last heartbeat 25.6 hours ago
> Note: Either the bot has stopped, or the publisher on the VPS has.
> Note: Worth checking both windows are still running.

The bot was **fine**. It had been evaluating all eight instruments on schedule for
fourteen hours after the last push, and `C:\Tradingbot\logs\status.json` was being
rewritten every cycle. Only the publisher had died — at `10:05:26`, one second
after its last successful push. `publisher_service.log` stopped at the same second
and `bot.log` carried on.

**A wrong inference on the way there, which is the more useful half of this entry.**
The argument used was: the final push (`10:05:25Z`) carried a heartbeat from
`10:05:13.268Z`, twelve seconds earlier, and two processes falling silent twelve
seconds apart means one event took both down. It sent the user to Event Viewer
looking for a shutdown that had never happened.

The flaw: **the last push is always a successful push, so the heartbeat inside it
is always fresh.** A publisher dying alone leaves an identical fingerprint. The
twelve seconds were evidence of nothing at all — they are the signature of a
healthy push, and the final push is always healthy. It looked like a deduction
because it involved two timestamps and a subtraction.

**Fix.** `trading.stale_verdict()` compares the age of the last push against the
age of the heartbeat inside it, and the asymmetry only runs one way:

* pushes continued long after the heartbeat froze → **the bot stopped.** Certain,
  because a dead publisher cannot push. The VPS and its network are cleared by
  deduction, and the report says so.
* both stopped together → **the publishing side stopped.** The report says the bot
  *may still be running and trading*, and says to check its log before restarting
  it — a healthy bot four days up should not be restarted to fix a publisher.

What it refuses to do is name which of publisher, VPS or network. From a laptop
those are one indistinguishable event, and picking one is the same guess as
before, pointed the other way.

`fetch_publish_age()` is one extra request, spent only once a heartbeat has
actually gone quiet, and it **never raises** — without it the report falls back to
the old wording, which was vague but true. The monitor is what someone reaches for
when things are already broken; it must not gain a new way to fail. It caches the
commit *time*, not the age: a cached age is a number that was true a minute ago,
which is the exact class of bug the feature exists to detect.

Three decisions worth keeping:

* **The `SILENT` tuple.** Three reports suppress the "(heartbeat ...)" suffix for
  a state whose headline already carries the age. That was keyed on the single
  string `"stale"`, so adding states would have silently switched it back on.
* **Both new states added to `botwatch.BAD`.** Leaving them out would have been
  the quietest possible regression — the watcher would have stopped alerting on a
  dead bot with every test still green. `test_botwatch` now reads the producible
  states out of the source with `ast` and requires each to be classified.
* **One threshold, not two.** `publish_age_if_needed()` asks at `<= stale_after`
  and `assess` calls stale at `> stale_after`. Split across three call sites those
  would drift, leaving a state reported as stale and never explained.

**Five mutations were not caught, and all five were real.** Three were the same
mistake three times: the call sites in the skill, the watcher and `--status` all
still compiled and reported happily while passing `None`, which is this project's
signature failure (lesson 11) and its fourth occurrence. The fourth was the ask
threshold's `<=`, which no single-value test could pin — it is now asserted
*against* `assess`'s own verdict across a range of ages, so the pair cannot drift.
The fifth was the HTTP status check, which turned out to be load-bearing rather
than belt-and-braces: a 4xx whose body happens to parse would produce a confident
publish time from a failed request, and name the wrong process to restart.

82 checks in `tests/test_publisher_vs_bot.py`, including the incident replayed
from its real timestamps. All 35 applied mutations caught.

---

## Operator notes

**Restarting BEASTT after an update.** The background service holds the old code, so
`update.py` alone changes nothing. Since entry 9 the program says so itself — the
updater names what is still running, and the chat shows a banner — but the
sequence is unchanged:

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
13. **If two halves of a program update on different schedules, the program has to
    say so.** Entry 9 was one confusing evening caused entirely by a mismatch the
    code knew about and never mentioned. Advice in an operator note is not a
    feature; the failure happens in front of someone who is not reading the notes.
14. **An inference that involves arithmetic can still be worthless.** Entry 10's
    twelve-second gap looked like a deduction and was a tautology: the last push
    is always a successful one. Before trusting a signal, ask what the *other*
    explanation would have looked like — if it looks identical, the signal is not
    evidence. Both diagnoses this log records as wrong were confident.

## Scope

The trading monitor is **read-only**, permanently. It reports; it does not trade, pause,
restart or size anything. That boundary is enforced in code, tested, and is not a
setting.
