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
  "design": {"palette": "navy|slate|plum|ember|custom", "primary": "0B2545", "accent": "3DA5D9", "rationale": "why this suits the topic"},
  "title": "short deck title",
  "subtitle": "one-line value proposition",
  "slides": [
    {"title": "slide title",
     "bullets": ["short bullet", "short bullet"],
     "key_message": "the single takeaway from this slide",
     "notes": "speaker notes"}
  ],
  "closing": "closing line, e.g. Thank you"
}""",
    "document": """{
  "design": {"palette": "navy|slate|plum|ember|custom", "primary": "0B2545", "accent": "3DA5D9", "rationale": "why this suits the topic"},
  "title": "document title",
  "subtitle": "one-line summary",
  "sections": [
    {"heading": "section heading", "paragraphs": ["a full paragraph"], "bullets": ["optional bullet"]}
  ]
}""",
    "spreadsheet": """{
  "design": {"palette": "navy|slate|plum|ember|custom", "primary": "0B2545", "accent": "3DA5D9", "rationale": "why this suits the topic"},
  "title": "workbook title",
  "sheets": [
    {"name": "Sheet name", "columns": ["Column A", "Column B"], "rows": [["value", 123]]}
  ]
}""",
}

_GUIDANCE = {
    "presentation": (
        "Aim for 6-9 slides that tell a clear story: context, then substance, then "
        "implications. Each slide needs 3-5 punchy bullets (max ~12 words each), a "
        "one-sentence key_message, and useful speaker notes. Include concrete "
        "figures, dates, or examples where you can. Avoid generic filler."
    ),
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
- Choose a "design" that fits the subject: pick one of the named palettes, or set
  "palette": "custom" with your own dark "primary" and bright "accent" hex colours
  (no '#'). Corporate/finance suits navy or slate; nature and health suit greens;
  creative and cultural topics suit plum; energy and food suit ember. Keep
  "primary" dark enough for white text to be readable on it.
"""


def ask_json(brain: Brain, prompt: str) -> str:
    """Ask the brain for JSON, using constrained decoding when supported.

    Ollama's `format: json` mode makes structured replies dramatically more
    reliable than prompting alone, and a larger context stops long documents
    being truncated. Backends that don't support these options ignore them.
    """
    try:
        return brain.reply(
            [Message(role="user", content=prompt)], json_mode=True, temperature=0.3
        )
    except TypeError:
        # Backend doesn't accept the extra options.
        return brain.reply([Message(role="user", content=prompt)])


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
    if isinstance(spec.get("design"), dict):
        out["design"] = spec["design"]
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
                    "bullets": bullets[:8],
                    "key_message": _clean(item.get("key_message") or "")[:180] or None,
                    "notes": _clean(item.get("notes") or "")[:600] or None,
                }
            )
        out["slides"] = slides
        if spec.get("closing"):
            out["closing"] = _clean(spec["closing"])[:80]
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


_REVISE_PROMPT = """You are editing an existing {kind} about "{topic}".

Here is its current content as JSON:
{current}

The user asks for this change:
"{instruction}"

Apply ONLY that change, keeping everything else exactly as it is. Return the
COMPLETE updated JSON in the same shape -- no markdown, no commentary.
"""


def revise(
    brain: Brain, kind: str, spec: Dict, instruction: str, topic: str = "",
    attempts: int = 2,
) -> Optional[Dict]:
    """Apply a natural-language edit to an existing spec.

    Returns the updated spec, or None if the model's reply can't be used -- in
    which case the caller keeps the previous version.
    """
    if kind not in _SCHEMAS:
        return None
    current = json.dumps(spec, ensure_ascii=False, indent=1)[:6000]
    prompt = _REVISE_PROMPT.format(
        kind=kind, topic=topic or spec.get("title", ""), current=current,
        instruction=instruction.strip(),
    )
    for _ in range(attempts):
        try:
            raw = ask_json(brain, prompt)
        except Exception as exc:
            print(f"[docs] Revision request failed: {exc}")
            return None
        data = _extract_json(raw)
        if data:
            updated = _normalise(kind, data, topic or spec.get("title", ""))
            if not _is_thin(kind, updated):
                # Preserve a design the model chose earlier unless it sent a new one.
                if spec.get("design") and not updated.get("design"):
                    updated["design"] = spec["design"]
                return updated
            print("[docs] Model returned too little content; keeping previous version.")
        else:
            print("[docs] Model reply wasn't valid JSON; retrying.")
        prompt += "\n\nYour previous reply was not valid JSON. Return ONLY the complete JSON."
    return None


# --- targeted edits ---------------------------------------------------------
_ONE_SLIDE = """Write ONE new presentation slide about: {topic}

It belongs in a deck titled "{deck}". Return ONLY this JSON:
{{"title": "slide title", "bullets": ["short bullet", "short bullet", "short bullet"],
  "key_message": "the single takeaway", "notes": "speaker notes"}}

Keep bullets under ~12 words, specific and factual. Plain text only.
"""

_ONE_SECTION = """Write ONE new section about: {topic}

It belongs in a document titled "{deck}". Return ONLY this JSON:
{{"heading": "section heading", "paragraphs": ["a full paragraph", "another paragraph"],
  "bullets": []}}

Substantive prose, 2-4 sentences per paragraph. Plain text only.
"""


def new_item(brain: Brain, kind: str, topic: str, deck_title: str) -> Optional[Dict]:
    """Generate a single slide or section.

    Asking for one small object is far more reliable on a local model than
    requesting a rewrite of the whole document, which tends to get truncated.
    """
    template = _ONE_SLIDE if kind == "presentation" else _ONE_SECTION
    prompt = template.format(topic=topic, deck=deck_title)
    try:
        raw = ask_json(brain, prompt)
    except Exception:
        return None
    data = _extract_json(raw)
    if not data:
        return None

    if kind == "presentation":
        bullets = [_clean(b) for b in (data.get("bullets") or []) if _clean(b)]
        if not data.get("title") or not bullets:
            return None
        return {
            "title": _clean(data["title"])[:120],
            "bullets": bullets[:8],
            "key_message": _clean(data.get("key_message") or "")[:180] or None,
            "notes": _clean(data.get("notes") or "")[:600] or None,
        }

    paragraphs = [_clean(p) for p in (data.get("paragraphs") or []) if _clean(p)]
    if not data.get("heading") or not paragraphs:
        return None
    return {
        "heading": _clean(data["heading"])[:120],
        "paragraphs": paragraphs,
        "bullets": [_clean(b) for b in (data.get("bullets") or []) if _clean(b)],
    }


def plan(brain: Brain, kind: str, topic: str, attempts: int = 2) -> Optional[Dict]:
    """Ask the model for document content; retry once if the reply is unusable."""
    prompt = _PROMPT.format(
        kind=kind, topic=topic, schema=_SCHEMAS[kind], guidance=_GUIDANCE[kind]
    )
    for attempt in range(attempts):
        try:
            raw = ask_json(brain, prompt)
        except Exception:
            return None
        data = _extract_json(raw)
        if data:
            spec = _normalise(kind, data, topic)
            if not _is_thin(kind, spec):
                return spec
        prompt += "\n\nYour previous reply was not valid JSON in the required shape. Return ONLY the JSON."
    return None
