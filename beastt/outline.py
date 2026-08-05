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

#: The bullet renderer splits a long list into two columns, so a slide holds
#: more than you'd guess. Splitting earlier than this fights the user's own
#: structure: they decided where the slide breaks go.
_MAX_BULLETS = 8
#: Only a genuinely unreadable slide is split beyond what the user asked for.
_SPLIT_ABOVE = 11
_MIN_SECTIONS = 3         # fewer than this isn't an outline worth trusting

#: A short line with no terminal punctuation is a label for the prose beneath
#: it, not a bullet of its own: "Microsoft Azure AI" heads the two sentences
#: and the "Risk:" note that follow, and together they are one point.
_LABEL_WORDS = 7
_LABEL_CHARS = 52


def _clean(text: str) -> str:
    text = _ASIDE.sub(" ", str(text or ""))
    text = text.replace("*", "").replace("#", "")
    return " ".join(text.split()).strip(" \t-–—:•▪·")


def _clean_line(text: str) -> str:
    """Like `_clean`, but preserves a trailing colon.

    That colon is the only signal distinguishing a lead-in ("Max Fashion can:")
    from a heading ("Microsoft Azure AI"), and stripping it early meant the
    lead-in absorbed the whole list beneath it.
    """
    out = _ASIDE.sub(" ", str(text or ""))
    out = out.replace("*", "").replace("#", "")
    return " ".join(out.split()).strip(" \t-–—•▪·")


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


def _is_label(text: str) -> bool:
    """A heading for the lines beneath it, rather than a point in its own right."""
    if not text or len(text) > _LABEL_CHARS:
        return False
    if text[-1] in ".!?":
        return False
    return len(text.split()) <= _LABEL_WORDS


def _group_labelled(rows: List[str]) -> List[str]:
    """Fold a label and the prose under it into a single bullet.

    Presentation scripts are written like:

        Microsoft Azure AI
        Predicts customer demand.
        Risk: High implementation cost.

    That is one item. Treating each line as a bullet turned five tools into
    twenty-one bullets, which then became five slides.
    """
    out: List[str] = []
    pending: Optional[str] = None
    detail: List[str] = []
    run = 0            # consecutive labels emitted with nothing beneath them

    def flush():
        nonlocal pending, detail, run
        if pending is None:
            return
        if detail:
            out.append(f"{pending} — {' '.join(detail)}")
            run = 0
        else:
            out.append(pending)
            run += 1
        pending, detail = None, []

    for row in rows:
        # A line ending in a colon introduces the list beneath it -- it is not a
        # heading that owns those lines. "By implementing AI, Max Fashion can:"
        # swallowed all six benefits into a single bullet.
        if row.endswith(":"):
            flush()
            out.append(row.rstrip(" :"))
            run = 0
            continue

        if _is_label(row):
            flush()
            pending = row
            continue

        # Absorb prose under a label, but not after a run of bare labels: that
        # is a parallel list ("Month 1 ... Month 5"), and a trailing sentence
        # belongs to the section, not to its last item.
        if pending is not None and len(detail) < 3 and run < 2:
            detail.append(row)
        else:
            flush()
            out.append(row)
    flush()
    return out


def _bullets_from(lines: List[str]) -> tuple:
    """Turn a section's body into (bullets, speaker notes).

    Explicit bullets win. Failing that, prose is grouped under its labels and
    then split into sentences -- a paragraph is not a slide, but its sentences
    make serviceable points.
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
            cleaned = _clean_line(stripped)
            if cleaned:
                prose.append(cleaned)

    notes = " ".join(prose)[:600] or None

    if not bullets:
        grouped = _group_labelled(prose)
        for chunk in grouped:
            # A grouped item is already one point; only split loose prose.
            bullets.extend([chunk] if "—" in chunk else _sentences(chunk))
    else:
        # Prose sitting under a bullet is usually that bullet's explanation.
        # Fold short ones in so the detail isn't lost.
        for chunk in _group_labelled(prose):
            if len(chunk) < 220 and len(bullets) < 12:
                bullets.append(chunk)

    seen, unique = set(), []
    for bullet in bullets:
        key = bullet.lower()
        if key not in seen and len(bullet) > 2:
            seen.add(key)
            unique.append(bullet[:300])
    return unique, notes


#: How a presenter states their subject in an opening line: "Today, I will
#: present my AI consulting solution for Max Fashion, one of India's ...".
_SUBJECT = re.compile(
    r"\b(?:present(?:ing)?|presentation\s+(?:on|about)|topic\s+is|talk(?:ing)?\s+about|"
    r"speak(?:ing)?\s+(?:on|about)|discuss(?:ing)?)\s+"
    r"(?:my|our|the|a|an|about|on)?\s*"
    r"(.{6,90}?)(?=[,.;:]|\s+(?:which|that|and\s+how|focusing)\b|$)",
    re.IGNORECASE,
)


def _deck_title(text: str, sections: List[Dict]) -> str:
    """The best available name for the deck.

    An explicit "Title:" line wins. Otherwise the opening slide usually says
    what the talk is about in prose -- far better than "About Max Fashion",
    which is merely the first section that isn't called "Introduction".
    """
    body = str(text or "")

    for line in body.splitlines():
        match = _TITLE_LINE.match(line)
        if match:
            return _clean(match.group(1))[:120]

    # Only look in the opening section: later mentions of "present" are content.
    opening = "\n".join(sections[0]["lines"]) if sections else body[:900]
    match = _SUBJECT.search(opening)
    if match:
        subject = _clean(match.group(1))
        # Guard against a fragment: it should read like a noun phrase.
        if 6 <= len(subject) <= 90 and len(subject.split()) >= 2:
            return subject[:120].strip(" ,.;:")

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

        # The user decided where the slide breaks go. Honour that: a section
        # spans two slides only if they wrote it as two ("Slide 10 & 11"), or if
        # it carries so many points that one slide would be unreadable.
        span = max(section.get("span", 1), 1)
        needed = span
        if len(bullets) > _SPLIT_ABOVE:
            needed = max(span, (len(bullets) + _MAX_BULLETS - 1) // _MAX_BULLETS)

        if needed <= 1:
            slides.append({"layout": "bullets", "title": title or "Slide",
                           "bullets": bullets, "notes": notes})
            continue

        # Spread evenly so no continuation is left with a single orphan point.
        per = -(-len(bullets) // needed)
        chunks = [bullets[i * per : (i + 1) * per] for i in range(needed)]
        chunks = [c for c in chunks if c]
        if len(chunks) > 1 and len(chunks[-1]) == 1:
            chunks[-2].extend(chunks.pop())

        for index, chunk in enumerate(chunks):
            slides.append({
                "layout": "bullets",
                "title": title if index == 0 else f"{title} (cont.)",
                "bullets": chunk,
                "notes": notes if index == 0 else None,
            })

    slides = [s for s in slides if s["title"] or s["bullets"]]
    if len(slides) < 2:
        return None

    spec = {"title": _deck_title(text, sections), "slides": slides}
    if closing:
        spec["closing"] = closing
    return spec
