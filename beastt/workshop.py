"""Collaborative document editing -- work on a document together, turn by turn.

The assistant keeps the current content spec in memory after creating a file, so
follow-up instructions ("add a slide about costs", "make it warmer", "shorten the
bullets") revise *that* document and re-render it, instead of starting over.

Design notes:
  * Colour and structural edits are handled deterministically where possible, so
    a small local model can't mangle them.
  * Anything else goes to the model as a revision request; the reply is validated
    and the previous spec is kept if it comes back unusable. An edit can degrade
    to "no change", but never to a broken document.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional

from .brain.base import Brain
from .theme import PALETTES, Theme, from_design, get as get_palette

# Colour words the user is likely to use, mapped to a palette or hue.
_COLOUR_WORDS = {
    "blue": "0B2545", "navy": "0B2545", "dark blue": "0B2545",
    "teal": "115E59", "green": "14532D", "emerald": "065F46",
    "purple": "3B0764", "violet": "4C1D95", "plum": "3B0764", "magenta": "701A75",
    "red": "7F1D1D", "maroon": "7F1D1D", "crimson": "881337",
    "orange": "7C2D12", "amber": "78350F", "warm": "7C2D12",
    "black": "111827", "grey": "1F2937", "gray": "1F2937", "slate": "1F2937",
    "brown": "44403C", "pink": "831843", "gold": "713F12",
}
_ACCENTS = {
    "0B2545": "3DA5D9", "115E59": "2DD4BF", "14532D": "4ADE80", "065F46": "34D399",
    "3B0764": "E879F9", "4C1D95": "A78BFA", "701A75": "F0ABFC", "7F1D1D": "FCA5A5",
    "881337": "FB7185", "7C2D12": "FBBF24", "78350F": "FCD34D", "111827": "9CA3AF",
    "1F2937": "10B981", "44403C": "D6D3D1", "831843": "F9A8D4", "713F12": "FCD34D",
}
_LIGHTS = {
    "0B2545": "EEF4FA", "115E59": "F0FDFA", "14532D": "F0FDF4", "065F46": "ECFDF5",
    "3B0764": "FAF5FF", "4C1D95": "F5F3FF", "701A75": "FDF4FF", "7F1D1D": "FEF2F2",
    "881337": "FFF1F2", "7C2D12": "FFFBEB", "78350F": "FFFBEB", "111827": "F3F4F6",
    "1F2937": "F0FDF4", "44403C": "FAFAF9", "831843": "FDF2F8", "713F12": "FEFCE8",
}

_HEX_IN_TEXT = re.compile(r"#([0-9a-fA-F]{6})\b")

# Instructions we can satisfy without asking the model.
_COLOUR_INTENT = re.compile(
    r"\b(colour|color|palette|theme|scheme|brand|"
    r"dark|darker|light|lighter|warm|warmer|cool|cooler|"
    r"vibrant|muted|bold|soft|softer|professional\s+look|restyle|recolour|recolor)\b",
    re.IGNORECASE,
)
# Verbs that, combined with a colour word, clearly mean "restyle it".
_STYLE_VERB = re.compile(
    r"\b(use|make|change|switch|try|go|set|apply|prefer|want)\b", re.IGNORECASE
)
_DONE = re.compile(
    r"^\s*(that'?s (it|all|perfect|great)|done|finished|finish|looks good|"
    r"perfect|thanks?( a lot)?|good|ok(ay)? (done|good)|stop editing|"
    r"nothing else|no more changes)\s*[.!]*\s*$",
    re.IGNORECASE,
)
_REVISION_INTENT = re.compile(
    r"\b(add|include|insert|remove|delete|drop|cut|change|rename|rewrite|reword|"
    r"replace|shorten|shorter|expand|longer|more|fewer|less|make it|make the|"
    r"use|swap|adjust|tweak|improve|polish|simplify|split|merge|reorder|move|"
    r"fix|update|title|slide|section|sheet|column|row|bullet|bullets|tone|"
    r"colour|color|palette|theme|font|professional|formal|casual|regenerate|"
    r"redo|again|layout|layouts|image|images|photo|chart|timeline|quote|stat)\b",
    re.IGNORECASE,
)


class Workshop:
    """Holds the document currently being worked on."""

    def __init__(self):
        self.kind: Optional[str] = None
        self.topic: str = ""
        self.spec: Optional[Dict] = None
        self.theme: Optional[Theme] = None
        self.path: Optional[Path] = None
        self.revisions = 0

    # --- state ------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.spec is not None

    def start(self, kind: str, topic: str, spec: Dict, theme: Theme, path: Path) -> None:
        self.kind, self.topic, self.spec = kind, topic, spec
        self.theme, self.path = theme, path
        self.revisions = 0

    def close(self) -> None:
        self.__init__()

    # --- routing ----------------------------------------------------------
    def looks_like_revision(self, text: str) -> bool:
        if not self.active:
            return False
        if _DONE.match(text):
            return True
        # Keep it to short, instruction-shaped follow-ups so ordinary chat isn't
        # swallowed by the editing session.
        return bool(_REVISION_INTENT.search(text)) and len(text.split()) <= 30

    def is_done(self, text: str) -> bool:
        return bool(_DONE.match(text))

    # --- outline ----------------------------------------------------------
    def outline(self) -> str:
        """A readable summary so the user can react to what exists."""
        spec = self.spec or {}
        lines = []
        if self.kind == "presentation":
            for i, slide in enumerate(spec.get("slides") or [], 1):
                bullets = len(slide.get("bullets") or [])
                lines.append(f"  {i}. {slide.get('title', '')}  ({bullets} bullets)")
        elif self.kind == "document":
            for i, section in enumerate(spec.get("sections") or [], 1):
                paras = len(section.get("paragraphs") or [])
                lines.append(f"  {i}. {section.get('heading', '')}  ({paras} paragraphs)")
        else:
            for sheet in spec.get("sheets") or []:
                cols = ", ".join(sheet.get("columns") or [])
                rows = len(sheet.get("rows") or [])
                lines.append(f"  - {sheet.get('name', '')}: {rows} rows [{cols}]")
        return "\n".join(lines) if lines else "  (empty)"

    # --- deterministic colour edits ---------------------------------------
    @staticmethod
    def _names_a_colour(text: str) -> bool:
        lowered = text.lower()
        if any(re.search(rf"\b{name}\b", lowered) for name in PALETTES):
            return True
        return any(
            re.search(rf"\b{word}(?:er|est|ish|en)?\b", lowered) for word in _COLOUR_WORDS
        )

    def _colour_edit(self, text: str) -> Optional[Theme]:
        """Handle colour requests locally: exact hex, colour word, or palette name."""
        lowered = text.lower()

        match = _HEX_IN_TEXT.search(text)
        if match:
            primary = match.group(1).upper()
            return from_design(
                {
                    "primary": primary,
                    "secondary": primary,
                    "accent": _ACCENTS.get(primary, self.theme.accent if self.theme else "3DA5D9"),
                    "light": _LIGHTS.get(primary, "F2F5F8"),
                    "rationale": f"using your colour #{primary}",
                }
            )

        for name in PALETTES:
            if re.search(rf"\b{name}\b", lowered):
                return get_palette(name)

        # Longest colour phrase first, so "dark blue" beats "blue".
        # Allow comparative/adjectival endings: warm/warmer, gold/golden.
        for word in sorted(_COLOUR_WORDS, key=len, reverse=True):
            if re.search(rf"\b{word}(?:er|est|ish|en)?\b", lowered):
                primary = _COLOUR_WORDS[word]
                return from_design(
                    {
                        "primary": primary,
                        "secondary": primary,
                        "accent": _ACCENTS.get(primary, "3DA5D9"),
                        "light": _LIGHTS.get(primary, "F2F5F8"),
                        "rationale": f"{word} palette, as you asked",
                    }
                )
        return None

    # --- deterministic structural edits -----------------------------------
    _ADD = re.compile(
        r"\b(?:add|include|insert|append)\b(?:\s+(?:a|an|another|one))?\s*"
        r"(?:new\s+)?(?:slide|section|page|part|chapter)?\s*"
        r"(?:about|on|for|covering|regarding|titled|called)?\s+(.+)$",
        re.IGNORECASE,
    )
    _REMOVE = re.compile(
        r"\b(?:remove|delete|drop|cut|get rid of|take out)\b\s*"
        r"(?:the\s+)?(.+?)\s*(?:slide|section|page|part)?\s*$",
        re.IGNORECASE,
    )
    _SHORTEN = re.compile(
        r"\b(shorten|shorter|trim|tighten|condense|more concise|less wordy|"
        r"cut down)\b", re.IGNORECASE
    )
    _RETITLE = re.compile(
        r"\b(?:rename|retitle|change)\b[^.]*?\btitle\b[^\w]*(?:to|as|into)?\s*[\"']?(.+?)[\"']?\s*$",
        re.IGNORECASE,
    )

    def _items(self):
        """The editable list for this document kind, and its label."""
        if self.kind == "presentation":
            return self.spec.setdefault("slides", []), "slides", "title"
        if self.kind == "document":
            return self.spec.setdefault("sections", []), "sections", "heading"
        return None, "", ""

    _LAYOUT_FOR = re.compile(
        r"\b(?:use|make|change|switch|set|turn)\b[^.]*?\b(?:the\s+)?"
        r"(bullets?|image|photo|picture|comparison|compare|stat|statistic|number|"
        r"quote|timeline|steps|section|divider)\b[^.]*?\blayout\b"
        r"|(?:\blayout\b[^.]*?\b(bullets?|image|photo|comparison|stat|quote|timeline|section)\b)",
        re.IGNORECASE,
    )
    _SLIDE_NUMBER = re.compile(r"\bslide\s+(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s+slide\b",
                               re.IGNORECASE)

    def _layout_edit(self, text: str) -> Optional[str]:
        """Change a slide's layout, e.g. "use the timeline layout for slide 3"."""
        from .layouts import normalise

        if self.kind != "presentation":
            return None
        match = self._LAYOUT_FOR.search(text)
        if not match:
            return None
        wanted = normalise(next(g for g in match.groups() if g))

        slides = self.spec.get("slides") or []
        if not slides:
            return None

        number = self._SLIDE_NUMBER.search(text)
        if number:
            index = int(number.group(1) or number.group(2)) - 1
            if 0 <= index < len(slides):
                slides[index]["layout"] = wanted
                return f"slide {index + 1} now uses the {wanted} layout"
            return None

        # No slide named: try matching a title mentioned in the request.
        lowered = text.lower()
        for index, slide in enumerate(slides):
            title = str(slide.get("title") or "").lower()
            if title and title in lowered:
                slide["layout"] = wanted
                return f"{slide.get('title')!r} now uses the {wanted} layout"

        # Otherwise apply to every content slide.
        for slide in slides:
            slide["layout"] = wanted
        return f"all slides now use the {wanted} layout"

    def _structural_edit(self, brain: Brain, text: str) -> Optional[str]:
        """Handle add / remove / shorten / retitle / layout without a full rewrite."""
        from .docgen import new_item

        layout_change = self._layout_edit(text)
        if layout_change:
            return layout_change

        items, label, key = self._items()

        # Retitle the whole document.
        match = self._RETITLE.search(text)
        if match:
            new_title = match.group(1).strip(" .\"'")
            if new_title:
                self.spec["title"] = new_title[:120]
                return f"title changed to {new_title!r}"

        if items is None:  # spreadsheets fall through to the model
            return None

        # Add one slide/section -- generated as a single small object.
        match = self._ADD.search(text)
        if match:
            topic = match.group(1).strip(" .?!")
            topic = re.sub(r"^(?:a|an|the)\s+", "", topic, flags=re.IGNORECASE)
            if topic:
                item = new_item(brain, self.kind, topic, self.spec.get("title", self.topic))
                if item:
                    items.append(item)
                    name = item.get(key, topic)
                    return f"added {name!r}"
                return None

        # Remove by fuzzy title match.
        match = self._REMOVE.search(text)
        if match:
            target = match.group(1).strip(" .?!").lower()
            target = re.sub(r"^(?:the|a|an)\s+", "", target)
            if target:
                for index, item in enumerate(items):
                    name = str(item.get(key, "")).lower()
                    if target in name or name in target or (
                        target.split() and target.split()[0] in name
                    ):
                        removed = items.pop(index)
                        return f"removed {removed.get(key, '')!r}"
                return None

        # Shorten bullets across the document.
        if self._SHORTEN.search(text):
            trimmed = 0
            for item in items:
                bullets = item.get("bullets") or []
                for i, bullet in enumerate(bullets):
                    words = str(bullet).split()
                    if len(words) > 8:
                        bullets[i] = " ".join(words[:8])
                        trimmed += 1
                if len(bullets) > 5:
                    item["bullets"] = bullets[:5]
                    trimmed += 1
            if trimmed:
                return "bullets tightened"

        return None

    _LIST_LAYOUTS = re.compile(
        r"\b(?:list|show|what|which)\b[^.?!]*\blayouts?\b|\blayout options\b",
        re.IGNORECASE,
    )

    # --- applying a revision ----------------------------------------------
    def revise(self, brain: Brain, text: str) -> str:
        """Apply an instruction to the current document and re-render it."""
        from .docgen import revise as revise_spec
        from .documents import MissingLibrary, build

        if not self.active:
            return "There's no document open at the moment."

        # Just answering a question about layouts -- no edit needed.
        if self._LIST_LAYOUTS.search(text):
            from .layouts import describe

            return (
                "These are the slide layouts I can use:\n" + describe() +
                "\n\nSay something like \"use the timeline layout for slide 3\" "
                "or \"use the image layout for the costs slide\"."
            )

        notes = []

        # 1. Colour/theme changes are handled without the model.
        #    A hex code is unambiguous; otherwise require either an explicit
        #    colour/style word, or a styling verb next to a colour name, so
        #    "add a slide on the blue economy" isn't mistaken for a restyle.
        wants_restyle = bool(
            _HEX_IN_TEXT.search(text)
            or _COLOUR_INTENT.search(text)
            or (_STYLE_VERB.search(text) and self._names_a_colour(text))
        )
        new_theme = self._colour_edit(text) if wants_restyle else None
        content_change = True
        if new_theme is not None:
            self.theme = new_theme
            notes.append(f"restyled ({new_theme.rationale or 'new palette'})")
            # A pure colour request doesn't need the content rewritten.
            content_change = bool(
                re.search(
                    r"\b(add|remove|delete|change the (title|text)|rewrite|reword|"
                    r"shorten|expand|more|fewer|slide|section|sheet|bullet)\b",
                    text,
                    re.IGNORECASE,
                )
            )

        # 2. Content edits: try the cheap, reliable structural handlers first,
        #    and only fall back to a full model rewrite if none of them fit.
        if content_change:
            handled = self._structural_edit(brain, text)
            if handled:
                notes.append(handled)
            else:
                updated = revise_spec(brain, self.kind, self.spec, text, self.topic)
                if updated:
                    self.spec = updated
                    notes.append("content updated")
                elif new_theme is None:
                    return (
                        "I couldn't apply that one. Try being a bit more direct -- "
                        "for example \"add a slide about costs\", \"remove the "
                        "challenges slide\", \"shorten the bullets\", or "
                        "\"rename the title to X\"."
                    )

        # 3. Re-render, fetching images if any slide asks for them.
        finder = None
        if self.kind == "presentation":
            from .config import Config
            from .images import ImageFinder

            wants_images = any(
                str(s.get("layout", "")).lower() in ("image", "photo", "picture")
                for s in (self.spec.get("slides") or [])
            )
            if wants_images and Config.load().images_enabled:
                finder = ImageFinder(enabled=True)

        try:
            self.path = build(self.kind, self.spec, theme_name=self.theme, finder=finder)
        except MissingLibrary as exc:
            return str(exc)
        except Exception as exc:
            return f"I updated the content but couldn't save the file ({exc})."

        self.revisions += 1
        return (
            f"Updated -- {', '.join(notes) or 'rebuilt'}.\n"
            f"{self.outline()}\n"
            f"Saved to: {self.path}\n"
            "Anything else you'd like to change?"
        )
