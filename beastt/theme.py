"""Visual theme for generated documents.

Keeping colours, fonts, and sizing in one place means every renderer produces a
consistent, deliberately designed look instead of stock Office defaults. Change
a value here and presentations, documents, and spreadsheets all follow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Theme:
    # Core palette (hex, no '#').
    primary: str = "0B2545"      # deep navy -- headers, title backgrounds
    secondary: str = "134074"    # mid blue -- section accents
    accent: str = "3DA5D9"       # bright cyan -- rules, highlights
    light: str = "EEF4FA"        # very light blue -- panels, banded rows
    text_dark: str = "1A1A1A"
    text_muted: str = "5A6B7B"
    white: str = "FFFFFF"

    heading_font: str = "Calibri Light"
    body_font: str = "Calibri"

    # Presentation type scale (points)
    title_size: int = 44
    subtitle_size: int = 20
    slide_title_size: int = 30
    bullet_size: int = 18
    caption_size: int = 11

    #: Short description of the design intent, when the model chose it.
    rationale: str = ""

    def rgb(self, hex_colour: str):
        from pptx.dml.color import RGBColor

        return RGBColor.from_string(hex_colour)

    # --- contrast safety --------------------------------------------------
    @staticmethod
    def _luminance(hex_colour: str) -> float:
        """Relative luminance (0 = black, 1 = white), used to pick readable text."""
        try:
            r, g, b = (int(hex_colour[i : i + 2], 16) / 255 for i in (0, 2, 4))
        except Exception:
            return 0.0

        def channel(c: float) -> float:
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    def on(self, background_hex: str) -> str:
        """Return a legible text colour for the given background.

        Matters because the model may pick a pale primary colour, where white
        text would be unreadable.
        """
        return self.text_dark if self._luminance(background_hex) > 0.45 else self.white

    def on_primary(self) -> str:
        return self.on(self.primary)


DEFAULT = Theme()


# A few alternative palettes, selectable via BEASTT_DOC_THEME.
PALETTES = {
    "navy": DEFAULT,
    "slate": Theme(primary="1F2937", secondary="374151", accent="10B981",
                   light="F0FDF4", text_muted="6B7280"),
    "plum": Theme(primary="3B0764", secondary="6B21A8", accent="E879F9",
                  light="FAF5FF", text_muted="6B7280"),
    "ember": Theme(primary="7C2D12", secondary="C2410C", accent="FBBF24",
                   light="FFFBEB", text_muted="78716C"),
}


def get(name: str = "navy") -> Theme:
    return PALETTES.get((name or "navy").strip().lower(), DEFAULT)


_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _clean_hex(value, fallback: str) -> str:
    match = _HEX.match(str(value or "").strip())
    return match.group(1).upper() if match else fallback


def from_design(design, fallback_name: str = "navy") -> Theme:
    """Build a Theme from a model-chosen design spec.

    The spec may name a built-in palette or supply its own hex colours. Every
    field is validated and falls back to the named palette, so an odd model
    reply can never produce an unreadable or broken document.
    """
    base = get(fallback_name)
    if not isinstance(design, dict):
        return base

    named = str(design.get("palette") or "").strip().lower()
    if named in PALETTES:
        base = PALETTES[named]

    custom = {
        "primary": _clean_hex(design.get("primary"), base.primary),
        "secondary": _clean_hex(design.get("secondary"), base.secondary),
        "accent": _clean_hex(design.get("accent"), base.accent),
        "light": _clean_hex(design.get("light"), base.light),
    }
    fonts = {}
    heading = str(design.get("heading_font") or "").strip()
    body = str(design.get("body_font") or "").strip()
    if heading in _SAFE_FONTS:
        fonts["heading_font"] = heading
    if body in _SAFE_FONTS:
        fonts["body_font"] = body

    return replace(
        base,
        **custom,
        **fonts,
        rationale=str(design.get("rationale") or "").strip()[:200],
    )


# Fonts that ship with Office on Windows and macOS, so documents don't fall back
# to a substitute the user didn't choose.
_SAFE_FONTS = {
    "Calibri", "Calibri Light", "Segoe UI", "Segoe UI Light", "Arial",
    "Helvetica", "Georgia", "Garamond", "Times New Roman", "Verdana",
    "Trebuchet MS", "Tahoma", "Franklin Gothic Book", "Cambria", "Constantia",
}


def describe(theme: Theme) -> str:
    """One-line summary for telling the user what design was chosen."""
    parts = [f"#{theme.primary} / #{theme.accent}"]
    if theme.rationale:
        parts.append(theme.rationale)
    return " -- ".join(parts)
