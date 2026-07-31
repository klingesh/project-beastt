# JARVIS 🤖

> Your own personal AI companion — a friend who talks, listens, knows your voice, and is always there for you.

JARVIS is a voice **and** text AI assistant that runs on a **free, local** language model (via [Ollama](https://ollama.com)) — private, offline-capable, and no API costs. It's built to feel less like a tool and more like a friend: it welcomes you by name, chats naturally, remembers the conversation, searches the web for live info, and responds to **only your voice**.

## ✨ Features

- 🧠 **Local brain** — runs a free model (`llama3.2` by default) on your own machine. No API keys, no cloud.
- 💬 **Text & voice** — type to it, or talk out loud and hear it reply.
- ❤️ **A real friend** — warm, witty personality that greets you and holds a genuine conversation.
- 🌐 **Live web search** — asks about news, weather, or current events get real answers, summarized in its own voice.
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

## ⚙️ Configuration

Copy `.env.example` to `.env` and adjust. CLI flags override `.env` values.

| Setting | Env var | Default |
|---------|---------|---------|
| Assistant name / wake word | `BEASTT_NAME` | `JARVIS` |
| Model | `BEASTT_MODEL` | `llama3.2` |
| What it calls you | `BEASTT_USER_NAME` | `friend` |
| Voice on/off | `BEASTT_VOICE` | `off` |
| Speaking speed | `BEASTT_TTS_RATE` | `175` |
| Whisper model | `BEASTT_STT_MODEL` | `base` (`small` is more accurate) |
| Web search | `BEASTT_SEARCH` | `on` |
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

## 🗺️ Roadmap ideas

- More skills: reminders, timers, controlling files & apps
- Long-term memory that persists across sessions
- Higher-quality neural voice with [Piper TTS](https://github.com/rhasspy/piper)
- Wake word ("Hey BEASTT") for hands-free standby

---

Built with ❤️ as your own personal AI companion.
