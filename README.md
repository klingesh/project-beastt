# JARVIS 🤖

> Your own personal AI companion — a friend who talks, listens, knows your voice, and is always there for you.

JARVIS is a voice **and** text AI assistant that runs on a **free, local** language model (via [Ollama](https://ollama.com)) — private, offline-capable, and no API costs. It's built to feel less like a tool and more like a friend: it welcomes you by name, chats naturally, remembers the conversation, searches the web for live info, and responds to **only your voice**.

## ✨ Features

- 🧠 **Local brain** — runs a free model (`llama3.2` by default) on your own machine. No API keys, no cloud.
- 💬 **Text, voice, or a browser chat UI** — type, talk, or open a proper chat window.
- ❤️ **A real friend** — warm, witty personality that greets you and holds a genuine conversation.
- 🌐 **Live web search** — asks about news, weather, or current events get real answers, summarized in its own voice.
- 📊 **Real published data** — inflation, GDP, unemployment, rates and trade fetched from FRED and the World Bank, quoted with the observation date and a citation instead of recalled from training data.
- 🤖 **Watches your trading bot** — read-only health, drawdown against its limits and open positions, for a bot running on another machine. It reports; it never trades.
- 🎨 **Generates slide artwork** — Flux images for the slides no photograph can illustrate, always credited as AI-generated.
- 🎯 **Noise-robust listening** — ignores coughs, sips, and background clatter; reacts to actual speech.
- 🔐 **Voice lock** — recognizes your voiceprint and ignores other people.
- 🧵 **Memory** — remembers the flow of your chat within a session.
- 📄 **Makes documents** — real PowerPoint, Word, and Excel files from an idea.
- 🐙 **GitHub** — pushes what it creates to your repos, and clones them back.
- 🩺 **Looks after itself** — diagnoses problems, repairs what it can, and updates its own code.
- 📖 **Reads documents** — PDF, Word, Excel, PowerPoint from your repos.
- 💻 **Writes code** — scripts, web pages, and whole project scaffolds.
- 🖥️ **Runs commands** — opt-in, with destructive operations refused outright.
- 🔌 **Pluggable skills** — instant answers for time/date, easy to extend.
- 🪶 **Graceful fallback** — every optional feature degrades cleanly instead of crashing.

Documentation:
- **Hardening log:** `docs/HARDENING_LOG.md` — real failures from live sessions, and what each fix changed.

## 🏗️ How it works

```
You ──▶ Ears (mic → VAD → Whisper) ──▶ Voice lock ──▶ ┌────────────────────────┐
                                        (is it you?)  │ Assistant (orchestrator)│
Typed text ─────────────────────────────────────────▶ │ 1. Skills (instant)     │
                                                      │ 2. Web search if needed │
                                                      │ 3. Local LLM brain      │
                                                      │  + conversation memory  │
                                                      └────────────────────────┘
                                                                   │
                              Voice out (text-to-speech) ◀── reply ─┘
```

| Layer | Module | Tech |
|-------|--------|------|
| Brain | `beastt/brain/` | Ollama local LLM + rule-based fallback |
| Search | `beastt/search.py` | DuckDuckGo (`ddgs` if installed, else `requests`) |
| Memory | `beastt/memory.py` | rolling conversation window |
| Skills | `beastt/skills/` | pluggable instant-answer abilities |
| Voice out | `beastt/voice/tts.py` | `pyttsx3` (offline) |
| Voice in | `beastt/voice/stt.py` | `sounddevice` + Whisper (offline) |
| Voice lock | `beastt/voice/speaker.py` | Resemblyzer voiceprint, cohort scoring |
| Personality | `beastt/personality.py` | the system prompt that makes BEASTT *BEASTT* |

## 🚀 Quick start

### 1. Core dependencies

```bash
pip install -r requirements.txt
```

### 2. Give BEASTT its brain

Install [Ollama](https://ollama.com/download), then pull a model:

```bash
ollama pull llama3.2      # ~2GB, runs great on most laptops
```

> More power? Try `ollama pull llama3.1:8b` / `qwen3` / `phi4` and run with `--model <name>`.
> Without this, BEASTT still boots in a limited "basic mode".

### 3. Talk to BEASTT

```bash
python main.py                 # text chat
python main.py --name Tony     # BEASTT calls you "Tony"
python main.py --model qwen3   # different local model
```

## 🔊 Voice setup

```bash
pip install pyttsx3                     # BEASTT speaks
pip install sounddevice openai-whisper  # BEASTT listens
python main.py --voice
```

> **Note:** we use `sounddevice` rather than PyAudio because it bundles its own
> audio engine — no C++ compiler needed, which matters on Windows / new Pythons.

## 🔐 Voice lock (advanced, optional)

> **Off by default, and worth skipping.** Speaker verification is genuinely
> unreliable for similar-sounding voices: when it misjudges, the assistant simply
> stops responding to you, which is far more annoying than the feature is useful.
> The wake word already keeps it from reacting to ordinary conversation.

If you still want it, install the voiceprint engine (skipping `webrtcvad`, which
needs a compiler and isn't actually used):

```bash
pip install librosa
pip install --no-deps resemblyzer
```

```bash
python main.py --enroll         # 1. record your voice
python main.py --enroll-other   # 2. REQUIRED: record other voices to reject
python main.py --wake --my-voice
```

**Step 2 is not optional in practice.** Without a cohort to compare against,
verification falls back to a fixed similarity threshold that rejects the owner
about as often as anyone else.

Locked out? Override it any time:

```bash
python main.py --wake --no-voice-lock
```

## 📄 Creating documents

Ask in plain language and you get a real Office file:

```
"make a ppt about renewable energy"
"create a word document on the history of cricket"
"generate an excel sheet of monthly expenses"
```

```bash
pip install -r requirements-docs.txt
```

Files land in `beastt_output/`. The local model writes the *content* as JSON and
the renderer handles all layout, so a weak model can only produce a thin
document -- never a corrupt one.

Presentations are composed slide by slide rather than using stock Office
layouts: 16:9, a full-bleed title slide, an auto-generated agenda, coloured
header bands with an accent rule, bullets that split into two columns when
there are enough of them, a key-takeaway panel per slide, footers with slide
numbers, and a closing slide.

`BEASTT_DOC_THEME` sets the fallback palette (`navy`, `slate`, `plum`, `ember`)
for when the model doesn't choose one.

| Ask for | You get |
|---------|---------|
| ppt, powerpoint, presentation, slides, deck | `.pptx` with title slide, bullets, speaker notes |
| word, document, report, essay | `.docx` with headings, paragraphs, bullet lists |
| excel, spreadsheet, workbook | `.xlsx` with bold headers, typed cells, sized columns |

## 📊 Real numbers, not remembered ones

Ask a language model what US inflation is and it will tell you, confidently,
with no source — from training data that is months or years old. You cannot tell
from the answer that it is wrong.

So JARVIS looks it up instead. Two publishers, both free:

| Source | Covers | Key | Freshness |
| --- | --- | --- | --- |
| **World Bank** | Every country: GDP, inflation, unemployment, population, trade, FDI, debt, literacy, inequality | none needed | annual, lags a year or two |
| **FRED** (St. Louis Fed) | US: CPI and PCE inflation, unemployment, payrolls, fed funds, Treasury yields, mortgage rates, oil, S&P 500, USD/INR | free | monthly to daily |

The World Bank needs no key at all, so this works the moment you install JARVIS.
For US figures, add a free FRED key:

```
BEASTT_FRED_KEY=your_key_here
```

Get one instantly at <https://fredaccount.stlouisfed.org/apikeys>.

Then just ask:

```
You:    what is US inflation right now
Jarvis: US CPI inflation was 2.4% year over year in June 2026, down from 2.9%
        in April. That's FRED series CPIAUCSL — fred.stlouisfed.org/series/CPIAUCSL.
```

```
You:    compare India and China GDP
Jarvis: India's GDP was $3.91 trillion in 2024 against China's $18.74 trillion...
        (World Bank, NY.GDP.MKTP.CD — note this is annual data, so 2025 isn't
        published yet.)
```

Three things this deliberately does:

- **Always gives the observation date.** A figure without its date is
  misleading, especially for World Bank series that trail by a year or more.
- **Skips the web search when it has the real series.** The top search results
  for "US inflation" are blog posts quoting last year's number; putting those
  next to the official series only invites the model to split the difference.
  News questions still search, because a figure alone doesn't say what happened.
- **Reports, and does not advise.** It will explain what the data shows. It will
  not tell you to buy, sell or hold anything, and it will not forecast a price.

Data lookups also work as a step inside the plan-then-work loop, so "analyse
India's growth against its peers" fetches each series itself rather than guessing.

Turn the whole thing off with `BEASTT_DATA=off`.

## 🤖 Watching a trading bot

If you run a trading bot on another machine, JARVIS can tell you how it's doing:

```
You:    how's my bot?
Jarvis: Bot: RUNNING   (heartbeat 2 min ago)
        Account 1100219238 — live orders
        Equity 9,738.06 USD   balance 9,735.26
        Drawdown 2.62% of 20.00% limit   (peak 10,000.00)
        Today 0.00% of 6.00% limit
        Open (1):
          BRENT.ecn sell 0.04 lots @ 88.5   +2.80
```

The bot publishes a small `status.json` to a **private** GitHub repository; JARVIS
reads it back. That avoids opening any port on the bot's machine, and the same file
is readable from a phone.

```
BEASTT_BOT_STATUS_REPO=yourname/tradingbot-status
BEASTT_BOT_STATUS_TOKEN=github_pat_readonly_token
```

Use a token with **Contents: Read** on that repository only — JARVIS never writes
there.

### It reports; it does not trade

Ask it to close a position and it declines:

```
You:    close my brent position
Jarvis: I only watch the bot — I can't place, close or change trades, and I'm not
        going to be able to.
```

That is a deliberate limit, not a missing feature. A language model in the order
path is a bad idea however good the reporting around it gets. JARVIS also doesn't
offer opinions on the positions it reports: stating that Brent is short and 2.80 up
is a fact, while suggesting what to do about it would be investment advice.

### Being told, rather than having to ask

Asking only works if you think to ask. With the background service running, JARVIS
checks every five minutes and interrupts you when something is wrong:

```
Trading bot HALTED — total drawdown 21.00% >= 20.00%. It will take no new
entries until you clear it on the VPS.
```

```
Trading bot is not reporting. Either it or the status publisher has stopped —
check both windows on the VPS.
```

Three rules keep it from becoming noise you learn to dismiss:

- **Alert on change, not on level.** A halt is announced when it starts, not every
  time it's noticed.
- **Remind slowly.** A kill switch that fires at 3am should still be visible at
  breakfast, so it repeats hourly — not every five minutes.
- **Say when it recovers.** `Trading bot is reporting normally again.` Otherwise the
  last thing you were told is still bad news.

It also mentions drawdown crossing three quarters of the kill-switch limit, and new
errors, without treating them as emergencies.

Turn it off with `BEASTT_BOT_ALERTS=off`.

### Knowing when it has stopped

`BEASTT_BOT_STALE_MINUTES` (default 15) decides when a heartbeat counts as dead.
It has to exceed the *publisher's* interval rather than the bot's poll interval —
the heartbeat is written every 60 seconds but only published every 300, so a
healthy bot legitimately looks a few minutes old. Set it too low and JARVIS cries
wolf every few minutes, which trains you to ignore it.

### How this was built

`docs/HARDENING_LOG.md` records the faults found while building the monitor and what
each one changed — the stale snapshot that read as healthy, the crash-loop warning
that could never expire, and why the refusals are matched explicitly instead of left
to the model. The bot side of the same work is logged in the Tradingbot repository at
`docs/HARDENING_LOG.md`.

## 🐙 GitHub access

The assistant can push what it creates straight to your repositories:

```
"push it to github"           -> asks which repo, then uploads
"upload it to my notes repo"  -> pushes straight there
"list my repos"
"what files are in project-beastt"
```

When you say "push it to github" without naming a repo, it lists your
repositories and waits for you to pick -- by name ("notes"), by number ("3"), or
by position ("the second one"). Say "default" to use `BEASTT_GITHUB_REPO`, or
"cancel" to back out. Set `BEASTT_GITHUB_ASK=off` to skip the question and
always use the default repo.

Set it up with a [personal access token](https://github.com/settings/tokens)
(scope: `repo`) in your `.env`:

```
BEASTT_GITHUB_TOKEN=ghp_yourtokenhere
BEASTT_GITHUB_REPO=my-notes        # default push target
BEASTT_GITHUB_FOLDER=jarvis        # folder inside the repo
```

Deliberately conservative: it only creates or updates single files inside the
configured folder. It never deletes, rewrites history, or force-pushes. Keep
`.env` out of version control -- it's already in `.gitignore`.

## 🤝 Working on a document together

Creating a document opens a working session. The first reply is a *draft* with an
outline, and you refine it conversationally:

```
You:    make a ppt about renewable energy in india
JARVIS: Here's a first draft of your PowerPoint presentation...
          1. Where We Stand  (3 bullets)
          2. Economics       (4 bullets)
          3. Challenges      (3 bullets)
        Design: #14532D / #4ADE80 -- greens suit an environmental topic

You:    add a slide about costs
You:    make the bullets shorter
You:    use a warmer colour
You:    actually use #6B21A8
You:    remove the challenges slide
You:    that's perfect
```

Each instruction edits *that* document and re-renders it. Say "that's it",
"done", or "perfect" to close the session -- or "push it to GitHub" to upload.

**JARVIS chooses the design.** It picks a palette to suit the subject (greens for
environmental topics, navy for finance, plum for creative work) and explains why.
You can override it any time with a colour word ("make it teal", "something
warmer"), a built-in palette name, or an exact hex code.

Colour changes are applied directly without involving the model, so they're
always exact. Text colour is computed from the background's luminance, so even a
pale AI-chosen colour stays readable. Content edits go to the model, and if its
reply is unusable the previous version is kept -- an edit can do nothing, but it
can't corrupt your document.

## 📐 Slide layouts

Decks mix layouts instead of repeating one template. The model picks per slide,
and you can override it:

| Layout | Use |
|--------|-----|
| `bullets` | General points (splits into two columns when long) |
| `image` | Bullets on the left, photograph on the right |
| `comparison` | Two headed columns -- before/after, pros/cons |
| `stat` | One big headline number |
| `quote` | Large pull-quote with attribution |
| `timeline` | Horizontal sequence of dates or steps |
| `section` | Full-colour divider |

```
"what layouts can you do"
"use the timeline layout for slide 3"
"use the image layout for the how it works slide"
"use the comparison layout"          (applies to all slides)
```

## 🖼️ Images

Presentations automatically get a cover photo and pictures on `image` slides,
sourced from [Openverse](https://openverse.org) filtered to licences that permit
commercial use and modification.

**Attribution is added for you** -- each photo's creator and licence go into the
slide's speaker notes, and an "Image credits" slide is appended. The CC licences
these images use require that credit, so please keep it.

Turn it off with `BEASTT_IMAGES=off`. If there's no network the deck still builds,
using drawn graphics instead of photos.

### 🎨 Generated artwork

Photographs cover real subjects. Nobody has photographed *"the three phases of an
AI rollout"* — and those slides used to fall back to an abstract shape, which is
exactly where a deck starts to look plain.

So JARVIS generates the picture instead:

| Backend | Model | Setup | Allowance |
| --- | --- | --- | --- |
| **Pollinations** | Flux | **nothing at all** | free, no signup |
| **Cloudflare Workers AI** | FLUX-1-schnell | account id + token | ~170 images/day free |

Pollinations needs no key, so this works on a fresh install. Cloudflare is
steadier once configured and is tried first when it is:

```
BEASTT_CF_ACCOUNT=your_account_id
BEASTT_CF_TOKEN=your_token
```

Token from <https://dash.cloudflare.com/profile/api-tokens> with Workers AI access.

**Photos are tried first, generation second.** A real photograph of a real
storefront is more credible than an invented one, and Openverse answers in a
second where Flux takes tens of them. Flip it with `BEASTT_IMAGE_PREFER=generated`
for a deck about concepts rather than places.

**Every generated image is credited as AI-generated** on the same credits slide
as the photographs, attributed to the model that made it. A deck should never
pass invented imagery off as a photograph.

Two details worth knowing:

- The prompt explicitly forbids text in the image. Flux renders convincing
  gibberish, which looks worse on a slide than no text at all.
- Anonymous Pollinations requests are documented to reject a seed and any
  non-square aspect ratio, so without a key JARVIS asks for a square and lets
  the renderer crop, then falls back to the older endpoint if the gateway still
  refuses. Adding `BEASTT_POLLINATIONS_KEY` lifts both restrictions.

Turn it off with `BEASTT_IMAGE_GEN=off`.

## 🔤 Typography

Presentations use clean sans-serif faces chosen to suit the subject.

Prose documents follow fixed rules:

- **Reports are always Times New Roman.**
- Other Word documents use Times New Roman or SF Pro Text, whichever the
  assistant judges to fit -- serif for formal or academic subjects, SF Pro for
  modern, product, or design ones.

`report` in your request selects report rules, e.g. *"write a report on X"*.

## 💻 Writing code

```
"write a python script that renames files by their modified date"
"make an html page for my portfolio"
"write a sql query to find duplicate emails"
```

The file is saved under `beastt_workspace/`, and you can push it to GitHub the
same way as a document.

### Project layouts

```
"what project layouts can you do"
"create a flask project called notes-api"
"set up a static site called my-portfolio"
```

| Layout | Contents |
|--------|----------|
| `python-cli` | argparse app with an entry point |
| `python-package` | Installable package with a test and `pyproject.toml` |
| `flask` | App, template, stylesheet, requirements |
| `fastapi` | Service with a typed request model |
| `static-site` | HTML, CSS and JavaScript |

Scaffolds are fixed templates rather than model output, so the structure is always
correct -- only generated file *contents* can vary.

## 🖥️ Running commands

**Off by default.** Enable with `BEASTT_SHELL=on`.

```
"run git status"
"run pip list"
```

Commands are vetted in three tiers:

1. **Always refused** -- deletion (`rm -rf`, `del /f /s`), formatting, shutdown,
   registry edits, `sudo`, force pushes, `git reset --hard`, piping the internet
   into a shell, and similar. These cannot be confirmed past.
2. **Run immediately** -- a short read-only allowlist: `git status`, `git log`,
   `ls`, `python --version`, `pip list`, `ollama list`...
3. **Confirmed first** -- everything else. The exact command is shown and you
   answer yes or no.

In voice mode *every* command is confirmed, because a misheard word should never
execute. Commands never run through a shell, so pipes and chaining can't smuggle
in extra work, and output is captured with a timeout.

This reduces obvious footguns. It is not a sandbox -- keep `BEASTT_SHELL=off`
unless you want it.

## 📥 Cloning repositories

```
"clone my notes repo"
"clone owner/some-repo"
"pull the latest in project-beastt"
```

Repositories land in `beastt_workspace/`. Private repos use your token for the
clone only -- the remote is rewritten afterwards so no credential is left in
`.git/config`, and the token is never echoed in output.

## 🩺 Looking after itself

```
"check yourself"          -> health check of everything
"fix yourself"            -> repair what it can, report what it can't
"what went wrong"         -> recent errors, in plain language
"are you up to date"      -> check GitHub for newer code
"update yourself"         -> fetch and apply it
"update your model"       -> pull the latest build of the local model
"roll back the last update"
```

Repairs are **deterministic**, not the model editing its own source:

| Problem | Fix |
|---------|-----|
| Missing Python package | Installs it |
| Ollama not running | Starts it |
| Model not pulled | Pulls it |
| Missing data folders | Creates them |
| Corrupt memory file | Backs it up and starts fresh |
| Anything needing a code change | **Reports it** -- with the traceback |

> The assistant deliberately cannot rewrite its own code. A model editing the
> source it is running from can break itself irrecoverably, so real code fixes are
> diagnosed and handed to you.

Failures are logged to `beastt_memory/errors.log`, so "what went wrong" has
something concrete to answer with. Updates keep a backup of every replaced file
(`beastt_memory/backups/`) and never touch `.env`, memory, or generated files --
and take effect on restart.

## 📖 Reading documents

```
"what documents are in project-beastt"
"read the renewable energy ppt in project-beastt"
"summarise report.pdf from my notes repo"
"read beastt_output/plan.docx"          (a local file)
```

Files are found by fuzzy name match, fetched from the repository, converted to
text, and summarised by the local model.

| Format | Read as |
|--------|---------|
| PDF | Page text (scans need OCR, which isn't supported) |
| Word `.docx` | Paragraphs and tables |
| PowerPoint `.pptx` | Slide-by-slide text |
| Excel `.xlsx` | Sheets as rows |
| CSV, JSON, TXT, MD, and source files | Directly |

Long documents are truncated before reaching the model, since a 200-page PDF
would otherwise exceed its context window.

## 💬 Chat interface

```bash
python main.py --ui
```

Opens a browser chat at `http://127.0.0.1:8765` — separate conversations in a
sidebar, full history, the lot. Everything the assistant can do works here:
documents, code, reading files, health checks.

- **Replies stream in** as the model writes them, with a progress line above the
  answer for the slow steps ("Searching the web for …"). This matters most where
  it used to hurt most: generating a document can take minutes, and a spinner
  gave you no way to tell thinking apart from a freeze.
- **Multiple chats.** Each conversation keeps its own context and any open
  document session; long-term memory is shared across all of them.
- **History is saved** to `beastt_memory/chats/`, so threads survive a restart —
  the assistant's context is rebuilt from the transcript when you reopen one.
- Chats are titled automatically from your first message, and can be renamed or
  deleted.
- Built on Python's standard library, so there's nothing extra to install.

Useful flags: `--port 9000` to change the address, `--no-browser` to start
without opening a window.

The interface listens on localhost only and has no authentication, so keep it to
your own machine. Note that if `BEASTT_SHELL=on`, commands can be run from here too.

## 🔀 Choosing a model

The local Ollama model is the default and needs no account. Add an API key for a
hosted provider and its models appear in the **model pill** in the chat
interface's header — pick one per conversation, the way you'd switch models in
any chat app.

| Provider | Env var | Cost | Notes |
|----------|---------|------|-------|
| Local (Ollama) | `BEASTT_MODEL` | Free | Nothing leaves your machine |
| Groq | `BEASTT_GROQ_KEY` | Free tier, no card | Very fast; good for everyday chat |
| Cerebras | `BEASTT_CEREBRAS_KEY` | Free tier, no card | Large daily token budget; good for documents |
| OpenRouter | `BEASTT_OPENROUTER_KEY` | Free tier | Many models; names ending `:free` cost nothing |
| Mistral | `BEASTT_MISTRAL_KEY` | Free tier | — |
| OpenAI | `BEASTT_OPENAI_KEY` | Paid | Only if you want GPT models directly |

Model ids are `provider:model`, so `--model groq:llama-3.3-70b-versatile` works
from the command line too, and `BEASTT_DEFAULT_MODEL` sets the starting choice.

Three things worth knowing:

- **Cloud models send your messages to that provider.** The picker tags every
  model `local` or `cloud`, and the header pill shows a ☁ when the current one
  isn't running on your machine. Local remains the default precisely so this is
  always a deliberate choice.
- **Model lists are fetched live**, not hard-coded. Free line-ups change often,
  and a stale list fails silently at the worst moment. Hit **Refresh** in the
  picker if a provider has just added something.
- **A bad choice can't strand you.** Switching is refused with a clear reason if
  the key is missing or the local model isn't pulled, and each chat remembers its
  own model, so one thread can stay local while another uses something faster.

## ⚙️ Configuration

Copy `.env.example` to `.env` and adjust. CLI flags override `.env` values.

| Setting | Env var | Default |
|---------|---------|---------|
| Assistant name / wake word | `BEASTT_NAME` | `JARVIS` |
| Local model | `BEASTT_MODEL` | `llama3.2` |
| Starting model | `BEASTT_DEFAULT_MODEL` | (the local one) |
| Groq key | `BEASTT_GROQ_KEY` | (unset) |
| Cerebras key | `BEASTT_CEREBRAS_KEY` | (unset) |
| OpenRouter key | `BEASTT_OPENROUTER_KEY` | (unset) |
| Mistral key | `BEASTT_MISTRAL_KEY` | (unset) |
| OpenAI key | `BEASTT_OPENAI_KEY` | (unset) |
| What it calls you | `BEASTT_USER_NAME` | `friend` |
| Voice on/off | `BEASTT_VOICE` | `off` |
| Speaking speed | `BEASTT_TTS_RATE` | `175` |
| Whisper model | `BEASTT_STT_MODEL` | `base` (`small` is more accurate) |
| Web search | `BEASTT_SEARCH` | `on` |
| Published data | `BEASTT_DATA` | `on` |
| FRED key (US data) | `BEASTT_FRED_KEY` | _(none — World Bank needs no key)_ |
| Generated artwork | `BEASTT_IMAGE_GEN` | `on` |
| Photos or artwork first | `BEASTT_IMAGE_PREFER` | `photo` |
| Cloudflare artwork | `BEASTT_CF_ACCOUNT`, `BEASTT_CF_TOKEN` | _(none — Pollinations needs no key)_ |
| Documents | `BEASTT_DOCUMENTS` | `on` |
| GitHub token | `BEASTT_GITHUB_TOKEN` | (unset) |
| Ask which repo | `BEASTT_GITHUB_ASK` | `on` |
| Document palette | `BEASTT_DOC_THEME` | `navy` |
| Slide images | `BEASTT_IMAGES` | `on` |
| Coding help | `BEASTT_CODE` | `on` |
| Run commands | `BEASTT_SHELL` | `off` |
| Voice lock (advanced) | `BEASTT_MY_VOICE_ONLY` | `off` |
| Voice-lead margin | `BEASTT_SPEAKER_MARGIN` | `0.04` |

## 🧩 Adding a new skill

```python
from .base import Skill

class WeatherSkill(Skill):
    name = "weather"

    def matches(self, text: str) -> bool:
        return "weather" in text.lower()

    def run(self, text: str) -> str:
        return "It's a beautiful day!"
```

Register it in `beastt/skills/__init__.py` inside `default_skills()`.

## 💬 Chatting

Type or speak naturally. Say `bye`, `quit`, or `see you later` (or press Ctrl+C) to leave.

## 🧪 Tests

```bash
pip install pytest
pytest
```

748 checks, about half a second. They need **no** model, no network, no API keys
and no optional packages — every function they cover is pure, and
`tests/conftest.py` stubs `requests` if it isn't installed (the stub raises if
anything tries to make a real request).

What they cover, and why these functions in particular:

| Area | Why it is tested |
| --- | --- |
| `trading.assess` / `crash_looping` | severity must not be masked, and a warning must be able to expire |
| `TradingBotSkill` refusals | an action request must never reach the model |
| `botwatch.decide` | an hour of alert behaviour, tested in milliseconds |
| skill routing | "any update on my bot" must not be answered about BEASTT's own code |
| `longterm.relevant` | recall must be relevant, or silent |
| `shell.check` | the denylist, the allowlist, and project-folder confinement |
| `wake.detect` | tolerant of mishearings, quiet during ordinary speech |
| `code._safe_relpath`, `chats._safe_id` | a model-chosen name must not choose a location |
| chart citations | figures are sourced or labelled, never presented as fact without being one |

Nineteen of those checks are marked `xfail(strict=True)`: known bugs, written out
as the behaviour that *should* hold, with the cause in the reason string. The
suite stays green and CI fails the moment one is fixed without its marker being
removed. They are listed in
[`docs/HARDENING_LOG.md`](docs/HARDENING_LOG.md#7-every-check-in-this-log-had-been-written-run-and-thrown-away).

CI runs the suite on Python 3.10–3.13 on every push and pull request, plus one job
with nothing installed but pytest, and a byte-compile of every module — that last
one matters because `update.py` overwrites this install's own source from the
tracked branch.

## 🗺️ Roadmap ideas

- Image generation, with the results dropped straight into slides
- A research mode that plans, searches, reads and cites — showing each step
- Live data connectors: economic series, markets, search trends
- Native charts in presentations, built from described data
- More skills: reminders, timers, controlling files & apps
- Higher-quality neural voice with [Piper TTS](https://github.com/rhasspy/piper)
- Editing existing code: "add error handling to that script"

---

Built with ❤️ as your own personal AI companion.
