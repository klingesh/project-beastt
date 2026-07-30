# BEASTT 🤖

> Your personal, JARVIS-inspired AI companion — a friend who talks, listens, knows your voice, and is always there for you.

BEASTT is a voice **and** text AI assistant that runs on a **free, local** language model (via [Ollama](https://ollama.com)) — private, offline-capable, and no API costs. It's built to feel less like a tool and more like a friend: it welcomes you by name, chats naturally, remembers the conversation, searches the web for live info, and responds to **only your voice**.

## ✨ Features

- 🧠 **Local brain** — runs a free model (`llama3.2` by default) on your own machine. No API keys, no cloud.
- 💬 **Text & voice** — type to it, or talk out loud and hear it reply.
- ❤️ **A real friend** — warm, witty personality that greets you and holds a genuine conversation.
- 🌐 **Live web search** — asks about news, weather, or current events get real answers, summarized in its own voice.
- 🎯 **Noise-robust listening** — ignores coughs, sips, and background clatter; reacts to actual speech.
- 🔐 **Voice lock** — recognizes your voiceprint and ignores other people.
- 🧵 **Memory** — remembers the flow of your chat within a session.
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

## 🔐 Voice lock — respond to only your voice

Install the voiceprint engine (skipping `webrtcvad`, which needs a compiler and
that BEASTT doesn't actually use):

```bash
pip install librosa
pip install --no-deps resemblyzer
```

Then:

```bash
python main.py --enroll         # 1. record YOUR voice
python main.py --enroll-other   # 2. record a friend's voice as "not me"
python main.py --my-voice       # 3. BEASTT now answers only you
```

**Step 2 is the important one.** A fixed similarity threshold can't reliably
separate two similar-sounding people. Instead BEASTT compares: *does this sound
more like the owner, or more like a known other person?* Run `--enroll-other`
once per additional person to sharpen the lock further.

Each utterance prints its decision, so it's easy to verify and tune:

```
[voice] match: you 0.88 vs others 0.72 (need lead 0.04)
```

## 👂 Standby mode — just call its name

```bash
python main.py --wake
```

BEASTT idles quietly, listening only for **"BEASTT"**. When you call it, it asks
whether you want to talk by **voice** or by **text**, has the conversation, then
slips back to standby when you say goodbye.

- Combine with `--my-voice` so only *your* voice can wake it.
- Say it all in one breath — *"BEASTT, what's the weather?"* — and it wakes **and**
  answers straight away.
- Skip the question with `--on-wake voice` or `--on-wake text`.
- Standby uses the fast `tiny` Whisper model to stay light on CPU, then switches
  to your normal `BEASTT_STT_MODEL` for the actual conversation.
- Ctrl+C shuts it down.

## 🤖 Always-on mode (like "Hey Google")

Make BEASTT start with Windows and sit invisibly in the background, ready whenever
you call its name:

```bash
python main.py --install-startup --my-voice
```

That's it. From the next login, BEASTT is always listening for you.

- **No window** — it runs under `pythonw.exe`, launched by a hidden `.vbs` shim in
  your Startup folder (per-user, no admin rights).
- **Chimes instead of a screen** — a rising two-tone chime means "I'm listening",
  a falling one means "back to standby".
- **Self-healing** — the standby loop is supervised and restarts with backoff if
  something (like the mic) fails.
- **Logs** — everything goes to `beastt_memory/beastt.log`.

Start it immediately without rebooting:

```bash
wscript "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\BEASTT.vbs"
```

Manage it:

```bash
python main.py --uninstall-startup   # stop starting with Windows
python main.py --wake --service      # run headless right now
```

Stop a running background BEASTT with Task Manager (end the `pythonw.exe` task).

## ⚙️ Configuration

Copy `.env.example` to `.env` and adjust. CLI flags override `.env` values.

| Setting | Env var | Default |
|---------|---------|---------|
| Model | `BEASTT_MODEL` | `llama3.2` |
| What BEASTT calls you | `BEASTT_USER_NAME` | `friend` |
| Voice on/off | `BEASTT_VOICE` | `off` |
| Speaking speed | `BEASTT_TTS_RATE` | `175` |
| Whisper model | `BEASTT_STT_MODEL` | `base` (`small` is more accurate) |
| Web search | `BEASTT_SEARCH` | `on` |
| Voice lock | `BEASTT_MY_VOICE_ONLY` | `off` |
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

Built with ❤️ as your own personal JARVIS.
