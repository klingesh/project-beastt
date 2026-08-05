"""A small local web interface for the assistant.

Built on the standard library's HTTP server so there's nothing to install. It
binds to localhost only -- this is a personal interface, not a service to expose.

Each conversation gets its own Assistant instance, so short-term context and any
open document session belong to that thread, while long-term memory (which lives
on disk) is shared across all of them.
"""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import threading
import time
import webbrowser
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

from ..assistant import Assistant
from ..brain.ollama_brain import OllamaBrain
from ..config import Config
from . import chats

STATIC = Path(__file__).resolve().parent / "static"


class _State:
    """Shared state: one Assistant per conversation, created on demand."""

    def __init__(self, config: Config):
        self.config = config
        self._assistants: Dict[str, Assistant] = {}
        self._lock = threading.Lock()
        self._brain_checked = 0.0
        self._brain_status: Optional[Dict] = None

    def brain_status(self, max_age: float = 20.0) -> Dict:
        """Whether the real model is reachable, cached for a few seconds.

        The interface shows this so a fallback-mode session is obvious, instead
        of looking like a model that has mysteriously become terse. Cached
        because every page load asks, and the probe is a network round trip.
        """
        now = time.time()
        if self._brain_status is not None and now - self._brain_checked < max_age:
            return self._brain_status

        probe = OllamaBrain(model=self.config.model, base_url=self.config.ollama_url)
        if probe.is_available():
            status = {"ready": True, "detail": f"{self.config.model} · local"}
        elif probe.server_running():
            status = {
                "ready": False,
                "detail": f"Ollama is running, but '{self.config.model}' isn't installed",
                "fix": f"ollama pull {self.config.model}",
            }
        else:
            status = {
                "ready": False,
                "detail": "Ollama isn't running — replies will be very basic",
                "fix": "start Ollama, then reload this page",
            }

        self._brain_checked, self._brain_status = now, status
        return status

    def assistant_for(self, chat_id: str) -> Assistant:
        with self._lock:
            found = self._assistants.get(chat_id)
            if found is None:
                chat_config = self.config
                # A chat linked to a repository uses it as the default target,
                # so pushes and reads in this thread go to the right place.
                linked = (chats.load(chat_id) or {}).get("repo")
                if linked:
                    chat_config = replace(self.config, github_repo=linked)
                found = Assistant(config=chat_config, verbose=False)
                # Replay the transcript so the thread keeps its context after a
                # page reload or a server restart.
                chat = chats.load(chat_id)
                for message in (chat or {}).get("messages", [])[-20:]:
                    if message["role"] == "user":
                        found.memory.add_user(message["content"])
                    else:
                        found.memory.add_assistant(message["content"])
                self._assistants[chat_id] = found
            return found

    def forget(self, chat_id: str) -> None:
        with self._lock:
            self._assistants.pop(chat_id, None)


class Handler(BaseHTTPRequestHandler):
    state: _State = None       # set in serve()
    server_version = "JarvisUI"

    # --- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):      # keep the console quiet
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json")

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _static(self, name: str) -> None:
        # Serve only files that actually sit in the static folder.
        target = (STATIC / name).resolve()
        if not str(target).startswith(str(STATIC.resolve())):
            self._json({"error": "not found"}, 404)
            return
        if not target.is_file():
            # A missing interface file is a setup problem, not a bad request --
            # say so plainly instead of returning an opaque 404.
            self._send(
                503,
                (
                    "<!DOCTYPE html><html><body style=\"font-family:system-ui;"
                    "background:#0b0f14;color:#e6edf5;padding:3rem;line-height:1.6\">"
                    f"<h2>Interface file missing: {name}</h2>"
                    "<p>The chat interface's files aren't on disk. Fetch them with:</p>"
                    "<pre style=\"background:#111823;padding:1rem;border-radius:8px\">"
                    "python update.py</pre>"
                    f"<p style=\"color:#8899ab\">Expected in: {STATIC}</p>"
                    "</body></html>"
                ).encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        kind = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), kind)

    # --- routes -----------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])

        if path == "/api/status":
            config = self.state.config
            return self._json({
                "name": config.name,
                "user": config.user_name,
                "model": config.model,
                "brain": self.state.brain_status(),
            })

        if path == "/api/chats":
            return self._json({"chats": chats.listing()})

        if path == "/api/repos":
            return self._repos()

        if path.startswith("/api/chats/"):
            chat = chats.load(path.rsplit("/", 1)[-1])
            if chat is None:
                return self._json({"error": "no such chat"}, 404)
            return self._json(chat)

        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_json()

        if path == "/api/chats":
            return self._json(chats.create())

        if path == "/api/message":
            return self._message(body)

        if path == "/api/upload":
            return self._upload(body)

        if path.startswith("/api/chats/"):
            chat_id = path.split("/")[3]

            if path.endswith("/rename"):
                ok = chats.rename(chat_id, body.get("title", ""))
                return self._json({"ok": ok}, 200 if ok else 404)

            if path.endswith("/repo"):
                ok = chats.set_repo(chat_id, body.get("repo", ""))
                # The assistant caches the repo, so rebuild it next turn.
                self.state.forget(chat_id)
                return self._json({"ok": ok}, 200 if ok else 404)

            if path.endswith("/detach"):
                chat = chats.load(chat_id)
                if chat is None:
                    return self._json({"error": "no such chat"}, 404)
                ok = chats.remove_attachment(chat, body.get("name", ""))
                return self._json({"ok": ok, "attachments": chat.get("attachments", [])})

        return self._json({"error": "not found"}, 404)

    # --- repositories -----------------------------------------------------
    def _repos(self):
        config = self.state.config
        if not config.github_token:
            return self._json({
                "repos": [],
                "error": "No GitHub token set. Add BEASTT_GITHUB_TOKEN to your .env.",
            })
        try:
            from ..github_client import GitHubClient

            repos = GitHubClient(config.github_token).list_repos(limit=100)
        except Exception as exc:
            return self._json({"repos": [], "error": str(exc)[:160]})
        return self._json({
            "repos": [{"name": r["name"], "private": r["private"]} for r in repos],
            "default": config.github_repo,
        })

    # --- attachments ------------------------------------------------------
    def _upload(self, body: dict):
        from ..readers import Unsupported, extract, supported

        name = str(body.get("name") or "").strip()
        chat_id = str(body.get("chat_id") or "").strip()
        encoded = body.get("data") or ""
        if not name or not encoded:
            return self._json({"error": "nothing to upload"}, 400)

        if not supported(name):
            return self._json({
                "error": f"I can't read '{Path(name).suffix or name}'. I handle PDF, "
                         "Word, Excel, PowerPoint, CSV, JSON, text and source files."
            }, 400)

        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return self._json({"error": "that file didn't arrive intact"}, 400)

        if len(data) > chats.MAX_UPLOAD_BYTES:
            limit = chats.MAX_UPLOAD_BYTES // (1024 * 1024)
            return self._json({"error": f"that file is over the {limit} MB limit"}, 400)

        chat = chats.load(chat_id) if chat_id else None
        if chat is None:
            chat = chats.create()

        try:
            text, note, truncated = extract(name, data)
        except Unsupported as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:
            return self._json({"error": f"couldn't read it ({exc.__class__.__name__})"}, 400)

        entry = chats.add_attachment(chat, name, note, text)
        # The assistant holds attachment context, so rebuild it next turn.
        self.state.forget(chat["id"])
        return self._json({
            "chat_id": chat["id"],
            "attachment": entry,
            "truncated": truncated,
            "attachments": chat.get("attachments", []),
        })

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/chats/"):
            chat_id = path.rsplit("/", 1)[-1]
            self.state.forget(chat_id)
            return self._json({"ok": chats.delete(chat_id)})
        return self._json({"error": "not found"}, 404)

    # --- the conversation -------------------------------------------------
    def _message(self, body: dict):
        text = str(body.get("message") or "").strip()
        chat_id = str(body.get("chat_id") or "").strip()
        if not text:
            return self._json({"error": "empty message"}, 400)

        chat = chats.load(chat_id) if chat_id else None
        if chat is None:
            chat = chats.create()

        # Name the thread after the first thing said in it.
        first = not chat.get("messages")
        chats.append(chat, "user", text)
        if first:
            chat["title"] = chats.auto_title(text)

        assistant = self.state.assistant_for(chat["id"])

        # Attachments are given to the assistant once per turn as background
        # context, so the model can answer questions about the files.
        attached = chats.attachment_text(chat)
        if attached:
            assistant.set_attachments(
                attached, [a["name"] for a in chat.get("attachments", [])]
            )

        try:
            reply = assistant.respond(text)
        except Exception as exc:
            from ..selfheal import record

            record(exc, context="the web interface")
            reply = f"Something went wrong there ({exc.__class__.__name__})."

        chats.append(chat, "assistant", reply)
        chats.save(chat)
        return self._json({
            "chat_id": chat["id"],
            "title": chat["title"],
            "reply": reply,
            "repo": chat.get("repo", ""),
            "attachments": chat.get("attachments", []),
        })


def serve(config: Optional[Config] = None, port: int = 8765,
          open_browser: bool = True) -> None:
    config = config or Config.load()

    # Fail loudly at startup rather than serving a broken page.
    missing = [f for f in ("index.html", "app.js", "style.css")
               if not (STATIC / f).is_file()]
    if missing:
        print(f"[ui] Missing interface file(s): {', '.join(missing)}")
        print(f"[ui] Expected in {STATIC}")
        print("[ui] Run `python update.py` to fetch them, then try again.")
        return

    Handler.state = _State(config)

    # Localhost only: this interface has no authentication by design.
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n[ui] {config.name} is available at {url}")
    print("[ui] Press Ctrl+C to stop.\n")

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[ui] Shutting down.")
    finally:
        server.server_close()
