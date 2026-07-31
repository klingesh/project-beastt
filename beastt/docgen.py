"""Ask the local model to plan a document, and parse its answer into a spec.

The model only ever produces JSON describing *content*; documents.py handles all
layout. That split keeps generation robust: malformed output is detected and
repaired here, so we never hand junk to the renderers.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, Optional

from .brain.base import Brain, Message

_SCHEMAS = {
    "presentation": """{
  "design": {"palette": "navy|slate|plum|ember", "rationale": "why this suits the topic"},
  "title": "short deck title",
  "subtitle": "one-line value proposition",
  "cover_query": "two or three words describing a cover photo",
  "slides": [
    {"layout": "bullets",
     "title": "slide title",
     "bullets": ["short bullet", "short bullet", "short bullet"],
     "key_message": "the single takeaway",
     "notes": "speaker notes",
     "image_query": "photo subject, only for layout image"}
  ],
  "closing": "Thank you"
}""",
    "document": """{
  "design": {"palette": "navy|slate|plum|ember|custom", "primary": "0B2545", "accent": "3DA5D9", "font": "Times New Roman|SF Pro Text", "rationale": "why this suits the topic"},
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
    "document": (
        "Aim for 4-6 sections with substantive paragraphs (2-4 sentences each). "
        "For \"design\".\"font\" pick either \"Times New Roman\" for formal or academic "
        "subjects, or \"SF Pro Text\" for modern, product, or design subjects."
    ),
    "spreadsheet": "Design sensible columns and 8-15 realistic example rows. Numbers as numbers.",
}

_PROMPT = """You are generating the CONTENT for a {kind}. Topic: {topic}

Return ONLY valid JSON matching exactly this shape (no markdown, no commentary):
{schema}

Rules:
- {guidance}
- Be specific and useful -- real facts and concrete detail, not placeholders.
- Keep all strings plain text: no markdown, asterisks, or newline characters.
- Set "layout" on each slide. Use "bullets" for most slides, and "image" (with an
  "image_query") for two or three where a photograph helps. Other options:
  "stat" -- also give "stat" and "stat_label";
  "comparison" -- also give "left" and "right", each with a heading and points;
  "quote" -- also give "quote" and "attribution";
  "timeline" -- also give "timeline", a list of items with label and text;
  "section" -- a divider slide.
- Choose a "design" that fits the subject: pick one of the named palettes, or set
  "palette": "custom" with your own dark "primary" and bright "accent" hex colours
  (no '#'). Corporate/finance suits navy or slate; nature and health suit greens;
  creative and cultural topics suit plum; energy and food suit ember. Keep
  "primary" dark enough for white text to be readable on it.
"""


DEBUG = os.environ.get("BEASTT_DEBUG", "").lower() in ("1", "on", "true", "yes")


def ask_json(brain: Brain, prompt: str, json_mode: bool = True) -> str:
    """Ask the brain for JSON, using constrained decoding when supported.

    Ollama's `format: json` mode makes structured replies much more reliable than
    prompting alone. Backends that don't accept the extra options ignore them.
    """
    try:
        return brain.reply(
            [Message(role="user", content=prompt)],
            json_mode=json_mode,
            temperature=0.3,
        )
    except TypeError:
        # Backend doesn't accept the extra options.
        return brain.reply([Message(role="user", content=prompt)])


def _unwrap(data):
    """Unwrap the shapes models produce when they don't follow the schema exactly.

    Common cases: the whole document nested under a key like "presentation", or
    a bare list of slides returned with no wrapper.
    """
    if isinstance(data, list):
        return {"slides": data, "sections": data}
    if not isinstance(data, dict):
        return None
    # Already the right shape?
    if any(k in data for k in ("slides", "sections", "sheets")):
        return data
    # Single wrapper key containing the real object.
    for key in ("presentation", "document", "spreadsheet", "deck", "content",
                "result", "output", "data"):
        inner = data.get(key)
        if isinstance(inner, dict) and any(
            k in inner for k in ("slides", "sections", "sheets")
        ):
            return inner
        if isinstance(inner, list) and inner:
            return {**data, "slides": inner, "sections": inner}
    return data


def _extract_json(raw: str) -> Optional[Dict]:
    """Pull the JSON out of a model reply, tolerating fences, prose, and wrappers."""
    if not raw:
        return None
    text = re.sub(r"```(?:json)?", "", raw.strip())

    # Try the whole thing first (json_mode replies are usually clean).
    for candidate in (text,):
        try:
            return _unwrap(json.loads(candidate))
        except json.JSONDecodeError:
            pass

    # Otherwise find the outermost balanced object or array.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for idx in range(start, len(text)):
            char = text[idx]
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return _unwrap(json.loads(text[start : idx + 1]))
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
            slide = {
                "layout": _clean(item.get("layout") or "bullets").lower(),
                "title": _clean(item.get("title") or "")[:120],
                "bullets": bullets[:8],
                "key_message": _clean(item.get("key_message") or "")[:180] or None,
                "notes": _clean(item.get("notes") or "")[:600] or None,
            }
            # Carry through the fields the chosen layout needs.
            for field in ("image_query", "stat", "stat_label", "quote", "attribution"):
                if item.get(field):
                    slide[field] = _clean(item[field])[:200]
            if slide["layout"] == "image":
                # Keep the search on-topic even if the model's query is vague.
                slide["image_query"] = image_query_for(
                    slide.get("image_query") or slide["title"], topic
                )
            for side in ("left", "right"):
                col = item.get(side)
                if isinstance(col, dict):
                    slide[side] = {
                        "heading": _clean(col.get("heading") or "")[:60],
                        "points": [_clean(x) for x in (col.get("points") or []) if _clean(x)][:6],
                    }
            steps = item.get("timeline") or item.get("steps")
            if isinstance(steps, list) and steps:
                cleaned = []
                for step in steps[:5]:
                    if isinstance(step, dict):
                        cleaned.append({"label": _clean(step.get("label") or "")[:24],
                                        "text": _clean(step.get("text") or "")[:120]})
                    else:
                        cleaned.append({"label": "", "text": _clean(step)[:120]})
                slide["timeline"] = cleaned
            slides.append(slide)
        # Drop stray slides with no title and no content.
        slides = [
            s for s in slides
            if s["title"] or s["bullets"] or s.get("stat") or s.get("quote")
            or s.get("timeline") or s.get("left") or s.get("right")
        ]
        _ensure_visuals(slides, topic)
        out["slides"] = slides
        if spec.get("cover_query"):
            out["cover_query"] = _clean(spec["cover_query"])[:80]
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


# Slide titles that say nothing about the subject, so they make poor image
# search terms on their own ("Challenges and Opportunities" found a photo of a
# man demonstrating a drilling alternative).
_GENERIC_TITLES = re.compile(
    r"^(the\s+)?(problem|problems|challenge|challenges|opportunit\w*|overview|"
    r"introduction|background|context|conclusion|summary|next steps|"
    r"recommendations|findings|results|benefits|advantages|disadvantages|"
    r"key\s+\w+|why\s+\w+\s+matters?|what\s+is\s+it|how\s+it\s+works|"
    r"the\s+future|looking\s+ahead|our\s+approach)\b",
    re.IGNORECASE,
)


def image_query_for(title: str, topic: str) -> str:
    """Build a photo search term that stays on-subject.

    A generic heading is replaced by the deck's topic; a specific heading is
    combined with it, so the photograph matches the subject rather than an
    unrelated reading of the words.
    """
    title = _clean(title)
    topic = _clean(topic)
    if not title or _GENERIC_TITLES.match(title):
        return topic or title
    # Keep it short: search engines do better with a few strong nouns.
    words = [w for w in f"{title} {topic}".split() if len(w) > 2]
    seen, ordered = set(), []
    for word in words:
        low = word.lower()
        if low not in seen:
            seen.add(low)
            ordered.append(word)
    return " ".join(ordered[:5])


def _ensure_visuals(slides: list, topic: str = "", wanted: int = 2) -> None:
    """Promote a couple of bullet slides to the image layout.

    Models often ignore the instruction to vary layouts and return an all-bullets
    deck, which looks flat. Converting a few slides that have a usable title
    guarantees some photography without changing any content.
    """
    if len(slides) < 4:
        return
    if any(str(s.get("layout") or "").lower() == "image" for s in slides):
        return

    # Prefer middle slides with a title and few bullets -- they have room for art.
    candidates = [
        slide
        for slide in slides
        if str(slide.get("layout") or "bullets").lower() == "bullets"
        and slide.get("title")
        and len(slide.get("bullets") or []) <= 4
    ]
    for slide in candidates[1 : 1 + wanted]:
        slide["layout"] = "image"
        if not slide.get("image_query"):
            slide["image_query"] = image_query_for(slide["title"], topic)


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
    # First try constrained JSON decoding; if that fails, fall back to plain
    # prompting, since some models produce better content unconstrained.
    for attempt in range(max(attempts, 2)):
        json_mode = attempt == 0
        try:
            raw = ask_json(brain, prompt, json_mode=json_mode)
        except Exception as exc:
            print(f"[docs] The model call failed: {exc.__class__.__name__}: {exc}")
            if "timeout" in str(exc).lower() or "Timeout" in exc.__class__.__name__:
                print("[docs] It ran out of time. A smaller model (llama3.2) is much "
                      "quicker, or raise BEASTT_TIMEOUT in .env.")
            return None

        if DEBUG:
            print(f"[docs] raw reply ({len(raw)} chars): {raw[:400]}")

        data = _extract_json(raw)
        if data is None:
            print(f"[docs] Attempt {attempt + 1}: reply wasn't valid JSON "
                  f"(got {len(raw)} chars). Retrying.")
        else:
            spec = _normalise(kind, data, topic)
            if not _is_thin(kind, spec):
                return spec
            got = (
                len(spec.get("slides") or []) if kind == "presentation"
                else len(spec.get("sections") or []) if kind == "document"
                else len(spec.get("sheets") or [])
            )
            print(f"[docs] Attempt {attempt + 1}: only {got} item(s) came back; "
                  f"keys were {list(data)[:6]}. Retrying.")

        prompt += (
            "\n\nYour previous reply could not be used. Return ONLY a JSON object "
            "with the exact keys shown above, and make sure the list has several "
            "entries."
        )
    return None
