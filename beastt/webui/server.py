"""A small local web interface for the assistant.

Built on the standard library's HTTP server so there's nothing to install. It
binds to localhost only -- this is a personal interface, not a service to expose.

Each conversation gets its own Assistant instance, so short-term context and any
open document session belong to that thread, while long-term memory (which lives
on disk) is shared across all of them.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

from ..assistant import Assistant
from ..config import Config
from . import chats

STATIC = Path(__file__).resolve().parent / "static"


class _State:
    """Shared state: one Assistant per conversation, created on demand."""

    def __init__(self, config: Config):
        self.config = config
        self._assistants: Dict[str, Assistant] = {}
        self._lock = threading.Lock()

    def assistant_for(self, chat_id: str) -> Assistant:
        with self._lock:
            found = self._assistants.get(chat_id)
            if found is None:
                found = Assistant(config=self.config, verbose=False)
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
            brain = getattr(self.state, "_probe", None)
            return self._json({
                "name": config.name,
                "user": config.user_name,
                "model": config.model,
            })

        if path == "/api/chats":
            return self._json({"chats": chats.listing()})

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

        if path.endswith("/rename") and path.startswith("/api/chats/"):
            chat_id = path.split("/")[3]
            ok = chats.rename(chat_id, body.get("title", ""))
            return self._json({"ok": ok}, 200 if ok else 404)

        return self._json({"error": "not found"}, 404)

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
