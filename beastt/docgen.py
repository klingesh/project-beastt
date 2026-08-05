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
     "bullets": ["a substantive point carrying a fact, figure or example"],
     "key_message": "the single takeaway",
     "notes": "speaker notes",
     "image_query": "photo subject, only for layout image",
     "chart": {"type": "bar|line|pie", "categories": ["label"],
               "series": [{"name": "series name", "values": [0]}]}}
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

#: Word forms people use for a count ("a ten slide deck").
_WORD_NUMBERS = {
    "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "eighteen": 18, "twenty": 20,
}

_COUNT_BEFORE = re.compile(
    r"(\d{1,2}|" + "|".join(_WORD_NUMBERS) + r")[\s-]*"
    r"(?:slide|slides|page|pages|section|sections)\b",
    re.IGNORECASE,
)
_COUNT_AFTER = re.compile(
    r"(?:slide|slides|page|pages|section|sections)\s*(?:count|:|=|of)?\s*"
    r"(\d{1,2}|" + "|".join(_WORD_NUMBERS) + r")\b",
    re.IGNORECASE,
)

#: Sane bounds. Below 3 there is no deck; above 30 a local model will not hold it.
_MIN_ITEMS, _MAX_ITEMS = 3, 30


def requested_count(text: str) -> Optional[int]:
    """How many slides or sections the user explicitly asked for, if they said.

    "make a 10 slide deck about X" has to beat the built-in default, which
    previously told the model to aim for 6-9 regardless of what was asked.
    """
    for pattern in (_COUNT_BEFORE, _COUNT_AFTER):
        match = pattern.search(text or "")
        if not match:
            continue
        token = match.group(1).lower()
        value = _WORD_NUMBERS.get(token)
        if value is None:
            try:
                value = int(token)
            except ValueError:
                continue
        if _MIN_ITEMS <= value <= _MAX_ITEMS:
            return value
    return None


def _guidance(kind: str, want: Optional[int] = None) -> str:
    """Build the per-kind instructions, honouring an explicit count if given."""
    if kind == "presentation":
        size = (f"Produce exactly {want} content slides."
                if want else "Produce 8 to 12 content slides.")
        return (
            f"{size} They must tell a clear story: context, then evidence, then "
            "implications, then what to do next.\n"
            "  - Each slide needs 3 to 5 bullets. A bullet is a full, informative "
            "phrase of roughly 10 to 20 words that carries a concrete fact, figure, "
            "date, percentage, currency amount or named example. Two-word labels "
            "like \"Finite resource\" are not acceptable -- say what, how much, and "
            "why it matters.\n"
            "  - Vary the layouts. In a deck this size include at least one \"stat\" "
            "slide, at least one \"chart\" slide with real numbers, and one "
            "\"comparison\" slide. Use \"section\" dividers to separate the major "
            "parts of a longer deck.\n"
            "  - Every slide needs a one-sentence key_message and speaker notes that "
            "add something not already on the slide."
        )
    if kind == "document":
        size = (f"Produce exactly {want} sections."
                if want else "Produce 5 to 8 sections.")
        return (
            f"{size} Each needs 2 to 4 substantive paragraphs of 3 to 5 sentences, "
            "with concrete figures and named examples rather than generalities. "
            "For \"design\".\"font\" pick either \"Times New Roman\" for formal or "
            "academic subjects, or \"SF Pro Text\" for modern, product, or design "
            "subjects."
        )
    rows = f"{want}" if want else "10 to 20"
    return (f"Design sensible columns and {rows} realistic example rows. "
            "Numbers as numbers, not strings.")

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
  "chart" -- also give "chart" with a "type" of bar, line or pie, a
  "categories" list of labels, and a "series" list of {{name, values}}. Values
  must be real numbers you are confident about, and every series must have
  exactly as many values as there are categories. Prefer a chart over prose
  whenever the point is quantitative;
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


_CHART_TYPES = ("bar", "column", "line", "pie", "doughnut")


def _number(value) -> Optional[float]:
    """Coerce a model's value to a number, tolerating "1,200", "45%" and "$3.2"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^0-9.\-]", "", str(value or ""))
    if text in ("", "-", ".", "-."):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalise_chart(raw) -> Optional[Dict]:
    """Validate a chart description, or return None if it can't be drawn.

    Models are loose with this: series of the wrong length, numbers as strings
    with units attached, a single value where a list belongs. Anything that
    can't be made consistent is rejected here so the renderer never has to cope.
    """
    if not isinstance(raw, dict):
        return None

    kind = _clean(raw.get("type") or "bar").lower()
    if kind not in _CHART_TYPES:
        kind = "bar"

    categories = [_clean(c)[:40] for c in (raw.get("categories") or []) if _clean(c)]
    if len(categories) < 2:
        return None

    series = []
    for item in raw.get("series") or []:
        if isinstance(item, dict):
            name = _clean(item.get("name") or "Series")[:40]
            values = item.get("values")
        else:
            continue
        if not isinstance(values, (list, tuple)):
            continue
        numbers = [_number(v) for v in values]
        # Trim or pad to match the categories exactly; python-pptx requires it.
        numbers = [n for n in numbers if n is not None]
        if len(numbers) < 2:
            continue
        numbers = numbers[: len(categories)]
        if len(numbers) < len(categories):
            categories = categories[: len(numbers)]
        series.append({"name": name, "values": numbers})

    if not series:
        return None
    # Re-trim every series in case a later one was shorter than an earlier one.
    width = min(len(categories), min(len(s["values"]) for s in series))
    if width < 2:
        return None
    return {
        "type": kind,
        "categories": categories[:width],
        "series": [{"name": s["name"], "values": s["values"][:width]} for s in series[:4]],
    }


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
            chart = _normalise_chart(item.get("chart"))
            if chart:
                slide["chart"] = chart
            elif slide["layout"] == "chart":
                # A chart slide with no usable numbers would render as an empty
                # frame, so demote it rather than ship a blank.
                slide["layout"] = "bullets"
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
            or s.get("timeline") or s.get("left") or s.get("right") or s.get("chart")
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

    # Scale with the deck: two photos in a twenty-slide deck still reads as flat.
    wanted = max(wanted, len(slides) // 5)
    already = sum(1 for s in slides if str(s.get("layout") or "").lower() == "image")
    missing = wanted - already
    if missing <= 0:
        return

    # Prefer middle slides with a title and few bullets -- they have room for art.
    candidates = [
        slide
        for slide in slides
        if str(slide.get("layout") or "bullets").lower() == "bullets"
        and slide.get("title")
        and len(slide.get("bullets") or []) <= 4
    ]
    for slide in candidates[1 : 1 + missing]:
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


def _item_key(kind: str) -> str:
    return {"presentation": "slides", "document": "sections"}.get(kind, "sheets")


def _top_up(brain: Brain, kind: str, spec: Dict, topic: str, want: int) -> None:
    """Bring a short deck up to the requested length, one item at a time.

    Asking for a single slide is far more reliable than asking the model to
    regenerate the whole deck longer -- the same reason `new_item` exists for
    "add a slide about X". Capped so a stubborn model can't loop forever.
    """
    key = _item_key(kind)
    if key == "sheets":
        return
    items = spec.get(key) or []
    deck_title = spec.get("title") or topic
    for index in range(min(want - len(items), 6)):
        extra = new_item(brain, kind, f"{topic} (additional detail, part {index + 1})",
                         deck_title)
        if not extra:
            break
        items.append(extra)
    spec[key] = items


def plan(brain: Brain, kind: str, topic: str, attempts: int = 2,
         want: Optional[int] = None) -> Optional[Dict]:
    """Ask the model for document content; retry once if the reply is unusable.

    `want` is an explicit item count the user asked for. It drives the prompt and
    is then enforced, because a model told "exactly 10" will still hand back 7.
    """
    prompt = _PROMPT.format(
        kind=kind, topic=topic, schema=_SCHEMAS[kind],
        guidance=_guidance(kind, want),
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
                if want:
                    key = _item_key(kind)
                    items = spec.get(key) or []
                    if len(items) > want:
                        # Trim from the end: the opening slides carry the setup.
                        spec[key] = items[:want]
                    elif len(items) < want:
                        _top_up(brain, kind, spec, topic, want)
                    _ensure_visuals(spec.get("slides") or [], topic)
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
