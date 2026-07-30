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
