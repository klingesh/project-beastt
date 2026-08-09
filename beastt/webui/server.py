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
import re
import threading
import time
import webbrowser
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

from .. import providers
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
        self._brain_checked = 0.0
        self._brain_status: Optional[Dict] = None

    def brain_status(self, max_age: float = 20.0) -> Dict:
        """Whether the default model is reachable, cached for a few seconds.

        The interface shows this so a fallback-mode session is obvious, instead
        of looking like a model that has mysteriously become terse. Cached
        because every page load asks, and the probe is a network round trip.
        """
        now = time.time()
        if self._brain_status is not None and now - self._brain_checked < max_age:
            return self._brain_status

        model_id = providers.default_model_id(self.config)
        provider_id, model = providers.split_model_id(model_id)
        provider = providers.get(provider_id)
        label = providers.describe(model_id)

        if provider is None:
            status = {"ready": False, "detail": f"Unknown model '{model_id}'",
                      "fix": "check BEASTT_DEFAULT_MODEL in your .env"}
        elif not providers.is_configured(self.config, provider):
            status = {
                "ready": False,
                "detail": f"No API key for {provider.label}",
                "fix": f"add {provider.env_var} to your .env",
            }
        else:
            brain = providers.build(self.config, model_id)
            if brain is not None and brain.is_available():
                status = {"ready": True, "detail": label}
            elif provider.is_local:
                # Distinguish "Ollama is off" from "the model isn't pulled" --
                # the fixes are completely different.
                running = getattr(brain, "server_running", lambda: False)()
                if running:
                    status = {
                        "ready": False,
                        "detail": f"Ollama is running, but '{model}' isn't installed",
                        "fix": f"ollama pull {model}",
                    }
                else:
                    status = {
                        "ready": False,
                        "detail": "Ollama isn't running — replies will be very basic",
                        "fix": "start Ollama, then reload this page",
                    }
            else:
                status = {
                    "ready": False,
                    "detail": f"{provider.label} didn't answer",
                    "fix": f"check {provider.env_var} in your .env is valid",
                }

        status["model"] = model_id
        status["label"] = label
        self._brain_checked, self._brain_status = now, status
        return status

    def assistant_for(self, chat_id: str) -> Assistant:
        with self._lock:
            found = self._assistants.get(chat_id)
            if found is None:
                chat = chats.load(chat_id) or {}
                chat_config = self.config
                # A chat linked to a repository uses it as the default target,
                # so pushes and reads in this thread go to the right place.
                linked = chat.get("repo")
                if linked:
                    chat_config = replace(self.config, github_repo=linked)
                found = Assistant(
                    config=chat_config,
                    verbose=False,
                    # A chat remembers its own model; empty falls back to the default.
                    model_id=chat.get("model") or None,
                )
                # Replay the transcript so the thread keeps its context after a
                # page reload or a server restart.
                for message in chat.get("messages", [])[-20:]:
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

    def _art(self, name: str):
        """Serve a generated image so the chat can show it inline.

        A filename, never a path. Everything outside [A-Za-z0-9._-] is stripped,
        which removes separators outright -- so no arrangement of dots and slashes
        can climb out of the folder. The resolved path is then checked against the
        folder anyway, because one guard on a file server is not enough.
        """
        from ..imagegen import art_dir

        safe = re.sub(r"[^A-Za-z0-9._-]", "", unquote(name or ""))
        if not safe or safe.startswith("."):
            return self._json({"error": "bad image name"}, 400)

        folder = art_dir().resolve()
        target = (folder / safe).resolve()
        if folder not in target.parents or not target.is_file():
            return self._json({"error": "no such image"}, 404)

        kind = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), kind)

    # --- routes -----------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path.startswith("/api/art/"):
            return self._art(path[len("/api/art/"):])

        if path == "/api/status":
            config = self.state.config
            return self._json({
                "name": config.name,
                "user": config.user_name,
                "model": config.model,
                "default_model": providers.default_model_id(config),
                "brain": self.state.brain_status(),
            })

        if path == "/api/chats":
            return self._json({"chats": chats.listing()})

        if path == "/api/models":
            return self._models()

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

        if path == "/api/stream":
            return self._stream(body)

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

            if path.endswith("/model"):
                return self._set_model(chat_id, body)

            if path.endswith("/detach"):
                chat = chats.load(chat_id)
                if chat is None:
                    return self._json({"error": "no such chat"}, 404)
                ok = chats.remove_attachment(chat, body.get("name", ""))
                return self._json({"ok": ok, "attachments": chat.get("attachments", [])})

        return self._json({"error": "not found"}, 404)

    # --- models -----------------------------------------------------------
    def _models(self):
        """Everything the model picker needs, including providers still to set up."""
        refresh = "refresh" in parse_qs(urlparse(self.path).query)
        try:
            data = providers.catalogue(self.state.config, force=refresh)
        except Exception as exc:
            data = {"models": [], "providers": [], "error": str(exc)[:160]}
        data["default"] = providers.default_model_id(self.state.config)
        return self._json(data)

    def _set_model(self, chat_id: str, body: dict):
        """Point one conversation at a different model."""
        if chats.load(chat_id) is None:
            return self._json({"error": "no such chat"}, 404)

        wanted = str(body.get("model") or "").strip()
        if not wanted:
            # Clearing sends this chat back to the configured default.
            chats.set_model(chat_id, "")
            self.state.forget(chat_id)
            default = providers.default_model_id(self.state.config)
            return self._json({
                "ok": True, "model": "", "label": providers.describe(default),
            })

        assistant = self.state.assistant_for(chat_id)
        problem = assistant.set_model(wanted)
        if problem:
            # Never record a model the assistant couldn't actually reach --
            # otherwise every following turn in this chat would fail.
            return self._json({"error": problem}, 400)

        chats.set_model(chat_id, assistant.model_id)
        return self._json({
            "ok": True,
            "model": assistant.model_id,
            "label": assistant.model_label(),
        })

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
    def _open_turn(self, body: dict):
        """Shared setup for a turn: the chat, its assistant, and the user's text.

        Returns (None, None, "") when there's nothing to answer -- the caller
        reports that itself, since one endpoint replies in JSON and the other
        in an event stream.
        """
        text = str(body.get("message") or "").strip()
        if not text:
            return None, None, ""

        chat_id = str(body.get("chat_id") or "").strip()
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
        return chat, assistant, text

    def _message(self, body: dict):
        chat, assistant, text = self._open_turn(body)
        if chat is None:
            return self._json({"error": "empty message"}, 400)

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
            "model": assistant.model_id,
            "model_label": assistant.model_label(),
            "attachments": chat.get("attachments", []),
        })

    def _stream(self, body: dict):
        """Answer over server-sent events, so the reply appears as it's written.

        Kept alongside /api/message rather than replacing it: the CLI still uses
        the blocking path, and the browser falls back to it if a stream fails.
        """
        chat, assistant, text = self._open_turn(body)
        if chat is None:
            return self._json({"error": "empty message"}, 400)

        # Headers by hand: an event stream has no Content-Length, and this
        # handler speaks HTTP/1.0, so the client reads until the socket closes.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        alive = True

        def emit(event: dict) -> bool:
            """Write one SSE frame. Returns False once the browser has gone."""
            nonlocal alive
            if not alive:
                return False
            try:
                payload = json.dumps(event, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                alive = False
                return False

        # Metadata first, so a brand-new chat gets its id and title into the
        # interface straight away rather than only once the answer lands.
        emit({
            "type": "meta",
            "chat_id": chat["id"],
            "title": chat["title"],
            "repo": chat.get("repo", ""),
            "model": assistant.model_id,
            "model_label": assistant.model_label(),
            "attachments": chat.get("attachments", []),
        })

        reply, parts, finished = "", [], False
        try:
            for event in assistant.respond_stream(text):
                kind = event.get("type")
                if kind == "chunk":
                    parts.append(event.get("text") or "")
                elif kind == "done":
                    reply, finished = event.get("reply") or "", True
                if not emit(event):
                    break
        except Exception as exc:
            from ..selfheal import record

            record(exc, context="the web interface (streaming)")
            reply, finished = f"Something went wrong there ({exc.__class__.__name__}).", True
            emit({"type": "chunk", "text": reply})
            emit({"type": "done", "reply": reply})

        # Save whatever was produced even if the tab closed mid-answer: the work
        # is already done, and a truncated message beats losing it entirely.
        if not reply:
            reply = "".join(parts).strip()
        if reply:
            chats.append(chat, "assistant", reply)
        chats.save(chat)

        if not finished:
            # The generator was abandoned, so the assistant's own short-term
            # memory may be missing this turn. Drop it and let the next request
            # rebuild it from the saved transcript.
            self.state.forget(chat["id"])


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
