"""Build a deck from content the user has already written.

`docgen` asks the model to *invent* content from a topic. That is the wrong job
when someone has already written their slides -- a presentation script, an
assignment outline, notes from a meeting. Inventing new content there is worse
than useless: it throws away the work and replaces it with generalities.

So this module parses the text instead. It is deliberately deterministic: no
model call, nothing to hallucinate, and the words on the slides are the user's
own. If the text isn't recognisably an outline, `parse` returns None and the
caller falls back to asking the model.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

#: "Slide 1", "Slide 3 – Title", "🎤 Slide 10 & 11 – Benefits", "Slide 2:"
_MARKER = re.compile(
    r"^[^\w\n]*"                                  # emoji, bullets, stray symbols
    r"(?:slide|page|section)\s*"
    r"(\d{1,2})"                                  # first number
    r"(?:\s*(?:&|and|\+|-|–|to)\s*(\d{1,2}))?"    # optional range: "10 & 11"
    r"\s*[)\]]?\s*[–—:.\-]*\s*"
    r"(.*)$",
    re.IGNORECASE,
)

#: Markdown-style headings, used when there are no explicit slide markers.
_HEADING = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$")

#: A bullet the user already wrote.
_BULLET = re.compile(r"^\s*(?:[-*•▪·‣●]|\d{1,2}[.)]|[a-z][.)])\s+(.*)$", re.IGNORECASE)

#: Timing hints and stage directions we shouldn't put on the slide.
_ASIDE = re.compile(
    r"\s*\(\s*(?:approx\.?\s*)?\d+\s*(?:[-–]\s*\d+\s*)?"
    r"(?:sec|secs|second|seconds|min|mins|minute|minutes)\s*\)\s*",
    re.IGNORECASE,
)

#: Titles too generic to name the whole deck.
_WEAK_TITLE = re.compile(
    r"^(intro|introduction|agenda|overview|contents|outline|title|start|"
    r"beginning|about\s+me|thank\s*you|thanks|questions|q\s*&\s*a|conclusion|"
    r"summary|the\s+end|end)\b",
    re.IGNORECASE,
)

#: An explicit title the user typed.
_TITLE_LINE = re.compile(r"^\s*(?:deck\s+)?title\s*[:\-–]\s*(.+)$", re.IGNORECASE)

#: Closing slides -- rendered by the deck's own closing slide, not a content one.
_CLOSING = re.compile(r"^(thank\s*you|thanks|questions|q\s*&\s*a|any\s+questions)\b",
                      re.IGNORECASE)

_MAX_BULLETS = 5          # beyond this a slide is split, not crammed
_MIN_SECTIONS = 3         # fewer than this isn't an outline worth trusting


def _clean(text: str) -> str:
    text = _ASIDE.sub(" ", str(text or ""))
    text = text.replace("*", "").replace("#", "")
    return " ".join(text.split()).strip(" \t-–—:•▪·")


def _sentences(text: str) -> List[str]:
    """Split prose into sentence-sized bullets, keeping the user's wording."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    return [p.strip() for p in parts if len(p.strip()) > 2]


def _split_sections(text: str):
    """Break the text into (title, body-lines) using slide markers or headings."""
    lines = str(text or "").splitlines()

    sections, current = [], None
    for line in lines:
        marker = _MARKER.match(line)
        # A marker line must actually look like a marker, not a sentence that
        # happens to start with the word "slide".
        if marker and len(_clean(marker.group(3))) < 90:
            title = _clean(marker.group(3))
            span = 1
            if marker.group(2):
                try:
                    span = max(1, int(marker.group(2)) - int(marker.group(1)) + 1)
                except ValueError:
                    span = 1
            current = {"title": title, "lines": [], "span": span}
            sections.append(current)
            continue
        if current is None:
            heading = _HEADING.match(line)
            if heading:
                current = {"title": _clean(heading.group(1)), "lines": [], "span": 1}
                sections.append(current)
                continue
        if current is not None:
            current["lines"].append(line)

    if len(sections) >= _MIN_SECTIONS:
        return sections

    # No slide markers: try markdown headings on their own.
    sections, current = [], None
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            current = {"title": _clean(heading.group(1)), "lines": [], "span": 1}
            sections.append(current)
        elif current is not None:
            current["lines"].append(line)
    return sections


def looks_like_outline(text: str) -> bool:
    """True when the text is content to lay out rather than a topic to research."""
    if len(str(text or "")) < 120:
        return False
    return len(_split_sections(text)) >= _MIN_SECTIONS


def _bullets_from(lines: List[str]) -> tuple:
    """Turn a section's body into (bullets, speaker notes).

    Explicit bullets win. Failing that, prose is split into sentences -- a
    paragraph is not a slide, but its sentences make serviceable points.
    """
    bullets, prose = [], []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        match = _BULLET.match(stripped)
        if match:
            value = _clean(match.group(1))
            if value:
                bullets.append(value)
        else:
            cleaned = _clean(stripped)
            if cleaned:
                prose.append(cleaned)

    notes = " ".join(prose)[:600] or None

    if not bullets:
        for chunk in prose:
            bullets.extend(_sentences(chunk))
    else:
        # Prose sitting under a bullet is usually that bullet's explanation.
        # Fold short ones in so the detail isn't lost.
        for chunk in prose:
            if len(chunk) < 160 and len(bullets) < 12:
                bullets.append(chunk)

    seen, unique = set(), []
    for bullet in bullets:
        key = bullet.lower()
        if key not in seen and len(bullet) > 2:
            seen.add(key)
            unique.append(bullet[:300])
    return unique, notes


def _deck_title(text: str, sections: List[Dict]) -> str:
    explicit = None
    for line in str(text or "").splitlines():
        match = _TITLE_LINE.match(line)
        if match:
            explicit = _clean(match.group(1))
            break
    if explicit:
        return explicit[:120]
    for section in sections:
        title = section.get("title") or ""
        if title and not _WEAK_TITLE.match(title):
            return title[:120]
    return "Presentation"


def parse(text: str, kind: str = "presentation") -> Optional[Dict]:
    """Build a spec from the user's own content, or None if it isn't an outline."""
    sections = _split_sections(text)
    if len(sections) < _MIN_SECTIONS:
        return None

    if kind == "document":
        out_sections = []
        for section in sections:
            bullets, notes = _bullets_from(section["lines"])
            paragraphs = [b for b in bullets if len(b) > 60] or bullets
            if not (section["title"] or paragraphs):
                continue
            out_sections.append({
                "heading": section["title"] or "Section",
                "paragraphs": paragraphs,
                "bullets": [b for b in bullets if len(b) <= 60][:6],
            })
        if len(out_sections) < 2:
            return None
        return {"title": _deck_title(text, sections), "sections": out_sections}

    slides: List[Dict] = []
    closing = None
    for section in sections:
        title = section["title"]
        bullets, notes = _bullets_from(section["lines"])

        # "Thank you for your time." is a sign-off, not a slide's worth of
        # content -- the deck already ends with its own closing slide.
        trivial = not bullets or (len(bullets) <= 2
                                  and all(len(b) < 70 for b in bullets))
        if title and _CLOSING.match(title) and trivial:
            closing = title
            continue
        if not title and not bullets:
            continue

        # A section the user wrote for two slides, or one carrying more points
        # than fit, becomes several slides rather than an unreadable wall.
        span = max(section.get("span", 1), 1)
        needed = max(span, (len(bullets) + _MAX_BULLETS - 1) // _MAX_BULLETS or 1)
        if needed <= 1:
            slides.append({"layout": "bullets", "title": title or "Slide",
                           "bullets": bullets[:_MAX_BULLETS], "notes": notes})
            continue

        per = max(1, (len(bullets) + needed - 1) // needed)
        for index in range(needed):
            chunk = bullets[index * per : (index + 1) * per]
            if not chunk and index:
                break
            slides.append({
                "layout": "bullets",
                "title": title if index == 0 else f"{title} (cont.)",
                "bullets": chunk[:_MAX_BULLETS],
                "notes": notes if index == 0 else None,
            })

    slides = [s for s in slides if s["title"] or s["bullets"]]
    if len(slides) < 2:
        return None

    spec = {"title": _deck_title(text, sections), "slides": slides}
    if closing:
        spec["closing"] = closing
    return spec
