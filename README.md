# BEASTT 🤖

> Your personal, JARVIS-inspired AI companion — a friend who talks, listens, and is always there for you.

BEASTT is a voice **and** text AI assistant that runs on a **free, local** language model (via [Ollama](https://ollama.com)) — so it's private, offline-capable, and costs nothing. It's built to feel less like a tool and more like a friend: it welcomes you, chats naturally, remembers the conversation, and has a warm personality.

## ✨ Features

- 🧠 **Local brain** — runs a free model (`llama3.2` by default) on your own machine via Ollama. No API keys, no cloud, no cost.
- 💬 **Text & voice** — chat by typing, or talk to it out loud and hear it reply.
- ❤️ **A real friend** — warm, witty personality that welcomes you and holds a genuine conversation.
- 🧵 **Memory** — remembers the flow of your chat within a session.
- 🔌 **Pluggable skills** — instant answers for things like the time and date, and easy to extend.
- 🪶 **Graceful fallback** — boots and chats in a "basic mode" even before you install the model.

## 🏗️ How it works

```
You ──▶ Ears (speech-to-text) ──▶ ┌──────────────────────────┐
                                  │  Assistant (orchestrator) │
Typed text ─────────────────────▶ │  1. try Skills (instant)  │
                                  │  2. else ask the Brain    │
                                  │  + Memory of the chat     │
                                  └──────────────────────────┘
                                              │
        Voice (text-to-speech) ◀── reply ◀────┘
```

| Layer | Module | Tech |
|-------|--------|------|
| Brain | `beastt/brain/` | Ollama (local LLM) + rule-based fallback |
| Memory | `beastt/memory.py` | rolling conversation window |
| Skills | `beastt/skills/` | pluggable instant-answer abilities |
| Voice out | `beastt/voice/tts.py` | `pyttsx3` (offline) |
| Voice in | `beastt/voice/stt.py` | `SpeechRecognition` + Whisper (offline) |
| Personality | `beastt/personality.py` | the system prompt that makes BEASTT *BEASTT* |

## 🚀 Quick start

### 1. Install BEASTT's dependencies

```bash
pip install -r requirements.txt
```

### 2. Give BEASTT its brain (recommended)

Install [Ollama](https://ollama.com), then pull a model:

```bash
ollama pull llama3.2      # ~2GB, runs great on most laptops
```

> Want more power? Try `ollama pull qwen3`, `llama3.1:8b`, or `phi4` and run BEASTT with `--model <name>`.
> BEASTT still boots and chats in a limited "basic mode" without this step.

### 3. Talk to BEASTT

```bash
python main.py                 # text chat
python main.py --name Tony     # BEASTT calls you "Tony"
python main.py --model qwen3   # use a different local model
```

### 4. (Optional) Enable voice

```bash
pip install -r requirements-voice.txt
python main.py --voice
```

**OS notes for the microphone (PyAudio):**
- **macOS:** `brew install portaudio` then `pip install pyaudio`
- **Debian/Ubuntu:** `sudo apt install portaudio19-dev python3-pyaudio`
- **Windows:** `pip install pyaudio` usually works out of the box

## ⚙️ Configuration

Copy `.env.example` to `.env` and adjust anything you like (model, your name, voice speed, etc.). CLI flags override the `.env` values.

| Setting | Env var | Default |
|---------|---------|---------|
| Model | `BEASTT_MODEL` | `llama3.2` |
| What BEASTT calls you | `BEASTT_USER_NAME` | `friend` |
| Voice on/off | `BEASTT_VOICE` | `off` |
| Speaking speed | `BEASTT_TTS_RATE` | `175` |
| Whisper model | `BEASTT_STT_MODEL` | `base` |

## 🧩 Adding a new skill

Skills give instant, deterministic answers before BEASTT bothers the LLM. Create a class in `beastt/skills/`:

```python
from .base import Skill

class WeatherSkill(Skill):
    name = "weather"

    def matches(self, text: str) -> bool:
        return "weather" in text.lower()

    def run(self, text: str) -> str:
        return "It's a beautiful day! (hook me up to a weather API 😉)"
```

Then register it in `beastt/skills/__init__.py` inside `default_skills()`.

## 💬 Chatting

Type naturally. Say `bye`, `quit`, or `exit` (or press Ctrl+C) to leave. BEASTT will be right there next time.

## 🗺️ Roadmap ideas

- More skills: weather, reminders/timers, web search, controlling your files & apps
- Higher-quality neural voice with [Piper TTS](https://github.com/rhasspy/piper)
- Wake word ("Hey BEASTT") for hands-free listening
- Long-term memory that persists across sessions

---

Built with ❤️ as your own personal JARVIS.
