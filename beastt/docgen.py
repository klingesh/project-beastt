"""Ask the local model to plan a document, and parse its answer into a spec.

The model only ever produces JSON describing *content*; documents.py handles all
layout. That split keeps generation robust: malformed output is detected and
repaired here, so we never hand junk to the renderers.
"""

from __future__ import annotations

import json
import re
from typing import Dict, Optional

from .brain.base import Brain, Message

_SCHEMAS = {
    "presentation": """{
  "title": "short deck title",
  "subtitle": "one-line subtitle",
  "slides": [
    {"title": "slide title", "bullets": ["short bullet", "short bullet"], "notes": "speaker notes"}
  ]
}""",
    "document": """{
  "title": "document title",
  "subtitle": "one-line summary",
  "sections": [
    {"heading": "section heading", "paragraphs": ["a full paragraph"], "bullets": ["optional bullet"]}
  ]
}""",
    "spreadsheet": """{
  "title": "workbook title",
  "sheets": [
    {"name": "Sheet name", "columns": ["Column A", "Column B"], "rows": [["value", 123]]}
  ]
}""",
}

_GUIDANCE = {
    "presentation": "Aim for 6-9 slides. Each slide: 3-5 short bullets (max ~12 words each).",
    "document": "Aim for 4-6 sections with substantive paragraphs (2-4 sentences each).",
    "spreadsheet": "Design sensible columns and 8-15 realistic example rows. Numbers as numbers.",
}

_PROMPT = """You are generating the CONTENT for a {kind}. Topic: {topic}

Return ONLY valid JSON matching exactly this shape (no markdown, no commentary):
{schema}

Rules:
- {guidance}
- Be specific and useful -- real facts and concrete detail, not placeholders.
- Keep all strings plain text: no markdown, asterisks, or newline characters.
"""


def _extract_json(raw: str) -> Optional[Dict]:
    """Pull the JSON object out of a model reply, tolerating stray prose/fences."""
    if not raw:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE)

    # Find the outermost balanced object.
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for idx in range(start, len(text)):
        if text[idx] == "{":
            depth += 1
        elif text[idx] == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : idx + 1]
                try:
                    data = json.loads(candidate)
                    return data if isinstance(data, dict) else None
                except json.JSONDecodeError:
                    break
    return None


def _clean(value) -> str:
    return " ".join(str(value).replace("*", "").split())


def _normalise(kind: str, spec: Dict, topic: str) -> Dict:
    """Coerce the model's output into exactly what the renderers expect."""
    out: Dict = {"title": _clean(spec.get("title") or topic)[:120]}
    if spec.get("subtitle"):
        out["subtitle"] = _clean(spec["subtitle"])[:200]

    if kind == "presentation":
        slides = []
        for item in spec.get("slides") or []:
            if not isinstance(item, dict):
                continue
            bullets = [_clean(b) for b in (item.get("bullets") or []) if _clean(b)]
            slides.append(
                {
                    "title": _clean(item.get("title") or "")[:120],
                    "bullets": bullets[:6],
                    "notes": _clean(item.get("notes") or "")[:600] or None,
                }
            )
        out["slides"] = slides
    elif kind == "document":
        sections = []
        for item in spec.get("sections") or []:
            if not isinstance(item, dict):
                continue
            sections.append(
                {
                    "heading": _clean(item.get("heading") or "")[:120],
                    "paragraphs": [_clean(p) for p in (item.get("paragraphs") or []) if _clean(p)],
                    "bullets": [_clean(b) for b in (item.get("bullets") or []) if _clean(b)],
                }
            )
        out["sections"] = sections
    else:  # spreadsheet
        sheets = []
        for item in spec.get("sheets") or []:
            if not isinstance(item, dict):
                continue
            columns = [_clean(c) for c in (item.get("columns") or [])]
            rows = []
            for row in item.get("rows") or []:
                if isinstance(row, dict):
                    rows.append({_clean(k): v for k, v in row.items()})
                elif isinstance(row, (list, tuple)):
                    rows.append(list(row))
                else:
                    rows.append([row])
            sheets.append({"name": _clean(item.get("name") or "Sheet1")[:31],
                           "columns": columns, "rows": rows})
        out["sheets"] = sheets
    return out


def _is_thin(kind: str, spec: Dict) -> bool:
    if kind == "presentation":
        return len(spec.get("slides") or []) < 2
    if kind == "document":
        return len(spec.get("sections") or []) < 2
    sheets = spec.get("sheets") or []
    return not sheets or not (sheets[0].get("rows"))


def plan(brain: Brain, kind: str, topic: str, attempts: int = 2) -> Optional[Dict]:
    """Ask the model for document content; retry once if the reply is unusable."""
    prompt = _PROMPT.format(
        kind=kind, topic=topic, schema=_SCHEMAS[kind], guidance=_GUIDANCE[kind]
    )
    for attempt in range(attempts):
        try:
            raw = brain.reply([Message(role="user", content=prompt)])
        except Exception:
            return None
        data = _extract_json(raw)
        if data:
            spec = _normalise(kind, data, topic)
            if not _is_thin(kind, spec):
                return spec
        prompt += "\n\nYour previous reply was not valid JSON in the required shape. Return ONLY the JSON."
    return None
