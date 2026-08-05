"""Conversation storage for the web interface.

Each chat is a JSON file under `beastt_memory/chats/`, holding its title and the
full message list. Long-term memory stays shared across every chat -- these files
are just the transcripts, the way a chat app keeps separate threads.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

MAX_UPLOAD_BYTES = 20 * 1024 * 1024      # 20 MB per file

from ..paths import data_dir


def chats_dir() -> Path:
    path = data_dir() / "chats"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_id(chat_id: str) -> str:
    """Ids are generated here, but never trust one arriving from a request."""
    return re.sub(r"[^A-Za-z0-9_-]", "", str(chat_id))[:40]


def _path(chat_id: str) -> Path:
    return chats_dir() / f"{_safe_id(chat_id)}.json"


def create(title: str = "New chat") -> Dict:
    chat = {
        "id": uuid.uuid4().hex[:12],
        "title": title,
        "created": time.time(),
        "updated": time.time(),
        "messages": [],
    }
    save(chat)
    return chat


def save(chat: Dict) -> None:
    chat["updated"] = time.time()
    _path(chat["id"]).write_text(
        json.dumps(chat, indent=1, ensure_ascii=False), encoding="utf-8"
    )


def load(chat_id: str) -> Optional[Dict]:
    path = _path(chat_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def delete(chat_id: str) -> bool:
    """Remove a chat and everything stored alongside it."""
    path = _path(chat_id)
    existed = path.exists()
    if existed:
        path.unlink()

    # Attachment text lives in its own folder. Without this it would linger on
    # disk long after the conversation it belonged to was deleted.
    safe = _safe_id(chat_id)
    if safe:
        shutil.rmtree(data_dir() / "uploads" / safe, ignore_errors=True)
    return existed


def listing() -> List[Dict]:
    """All chats, newest first, without their message bodies."""
    out = []
    for path in chats_dir().glob("*.json"):
        try:
            chat = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "id": chat.get("id", path.stem),
            "title": chat.get("title") or "Untitled",
            "updated": chat.get("updated", 0),
            "count": len(chat.get("messages") or []),
        })
    return sorted(out, key=lambda c: c["updated"], reverse=True)


def append(chat: Dict, role: str, content: str) -> None:
    chat.setdefault("messages", []).append(
        {"role": role, "content": content, "at": time.time()}
    )


# --- attachments ------------------------------------------------------------
def uploads_dir(chat_id: str) -> Path:
    path = data_dir() / "uploads" / _safe_id(chat_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def add_attachment(chat: Dict, name: str, note: str, text: str) -> Dict:
    """Store an attachment's extracted text and record it on the chat."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "file"
    target = uploads_dir(chat["id"]) / f"{safe_name}.txt"
    target.write_text(text, encoding="utf-8")

    entry = {"name": name, "note": note, "chars": len(text), "stored": target.name}
    attachments = chat.setdefault("attachments", [])
    # Replace an earlier upload of the same file rather than duplicating it.
    attachments[:] = [a for a in attachments if a.get("name") != name]
    attachments.append(entry)
    save(chat)
    return entry


def attachment_text(chat: Dict, budget: int = 9000) -> str:
    """Concatenated attachment text for the model, newest first, within a budget."""
    parts, used = [], 0
    for entry in reversed(chat.get("attachments") or []):
        path = uploads_dir(chat["id"]) / entry.get("stored", "")
        if not path.exists():
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        room = budget - used
        if room <= 200:
            break
        if len(body) > room:
            body = body[:room] + "\n[...truncated]"
        parts.append(f"--- {entry['name']} ({entry.get('note', '')}) ---\n{body}")
        used += len(body)
    return "\n\n".join(reversed(parts))


def remove_attachment(chat: Dict, name: str) -> bool:
    attachments = chat.get("attachments") or []
    remaining = [a for a in attachments if a.get("name") != name]
    if len(remaining) == len(attachments):
        return False
    for entry in attachments:
        if entry.get("name") == name:
            path = uploads_dir(chat["id"]) / entry.get("stored", "")
            if path.exists():
                path.unlink()
    chat["attachments"] = remaining
    save(chat)
    return True


def set_repo(chat_id: str, repo: str) -> bool:
    chat = load(chat_id)
    if chat is None:
        return False
    chat["repo"] = " ".join(str(repo or "").split())[:120]
    save(chat)
    return True


def set_model(chat_id: str, model_id: str) -> bool:
    """Remember which model this conversation thinks with.

    Stored per chat, like the linked repo, so one thread can stay on the local
    model while another uses a faster hosted one. An empty value means "use
    whatever the configured default is".
    """
    chat = load(chat_id)
    if chat is None:
        return False
    chat["model"] = " ".join(str(model_id or "").split())[:120]
    save(chat)
    return True


def auto_title(text: str) -> str:
    """A short title derived from the first thing the user said."""
    cleaned = " ".join(str(text or "").split())
    cleaned = re.sub(r"^(hey\s+\w+[,\s]+|please\s+|can you\s+)", "", cleaned, flags=re.I)
    if len(cleaned) <= 42:
        return cleaned or "New chat"
    return cleaned[:42].rsplit(" ", 1)[0] + "..."


def rename(chat_id: str, title: str) -> bool:
    chat = load(chat_id)
    if chat is None:
        return False
    chat["title"] = " ".join(str(title).split())[:80] or "Untitled"
    save(chat)
    return True
