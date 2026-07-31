"""Conversation storage for the web interface.

Each chat is a JSON file under `beastt_memory/chats/`, holding its title and the
full message list. Long-term memory stays shared across every chat -- these files
are just the transcripts, the way a chat app keeps separate threads.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from ..paths import data_dir


def chats_dir() -> Path:
    path = data_dir() / "chats"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(chat_id: str) -> Path:
    # Ids are generated here, but never trust one arriving from a request.
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(chat_id))[:40]
    return chats_dir() / f"{safe}.json"


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
    path = _path(chat_id)
    if path.exists():
        path.unlink()
        return True
    return False


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
