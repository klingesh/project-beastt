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
    r"redo|again)\b",
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

    # --- applying a revision ----------------------------------------------
    def revise(self, brain: Brain, text: str) -> str:
        """Apply an instruction to the current document and re-render it."""
        from .docgen import revise as revise_spec
        from .documents import MissingLibrary, build

        if not self.active:
            return "There's no document open at the moment."

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

        # 2. Content edits go to the model, with the current spec as context.
        if content_change:
            updated = revise_spec(brain, self.kind, self.spec, text, self.topic)
            if updated:
                self.spec = updated
                notes.append("content updated")
            elif new_theme is None:
                return (
                    "I couldn't work out how to apply that change. Could you say it "
                    "another way? For example: \"add a slide about costs\" or "
                    "\"make the bullets shorter\"."
                )

        # 3. Re-render.
        try:
            self.path = build(self.kind, self.spec, theme_name=self.theme)
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
