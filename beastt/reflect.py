"""Reflection -- BEASTT decides what's worth remembering from a conversation.

At the end of a session (or periodically), the transcript is handed back to the
local model with instructions to pull out durable facts about the user. Those
facts go into long-term memory so future sessions start already knowing you.
"""

from __future__ import annotations

import json
import re
from typing import List

from .brain.base import Brain, Message

_PROMPT = """You are a memory extractor. Read the conversation and list durable facts
about the USER that would be useful to remember in future conversations.

Rules:
- Only include stable, personal facts: their name, location, job/studies, hobbies,
  preferences, relationships, pets, goals, hardware/tools they own, ongoing projects,
  and important events in their life.
- Do NOT include small talk, passing moods, the assistant's own statements, questions,
  or anything about the weather/news that will be outdated tomorrow.
- Write each fact as one short standalone sentence in the third person, using the
  user's name if known (e.g. "Lingaa is building an AI assistant called BEASTT").
- If there is nothing worth remembering, return an empty list.

Return ONLY a JSON array of strings, nothing else. Example:
["Lingaa studies engineering", "Lingaa owns a laptop with an RTX 3050"]

CONVERSATION:
{transcript}
"""


def _parse_facts(raw: str) -> List[str]:
    """Pull a JSON array of strings out of the model's reply, tolerantly."""
    if not raw:
        return []
    match = re.search(r"\[.*\]", raw, re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    facts = []
    for item in data:
        if isinstance(item, str):
            fact = " ".join(item.strip().split())
            if 3 < len(fact) <= 200:
                facts.append(fact)
    return facts


def extract_facts(brain: Brain, transcript: List[Message], user_name: str) -> List[str]:
    """Ask the brain to distil durable facts from a conversation."""
    lines = []
    for msg in transcript:
        if msg.role == "user":
            lines.append(f"{user_name}: {msg.content}")
        elif msg.role == "assistant":
            lines.append(f"Assistant: {msg.content}")
    if not lines:
        return []

    prompt = _PROMPT.format(transcript="\n".join(lines[-40:]))
    try:
        reply = brain.reply([Message(role="user", content=prompt)])
    except Exception:
        return []
    return _parse_facts(reply)
