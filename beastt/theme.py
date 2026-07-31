"""Visual theme for generated documents.

Keeping colours, fonts, and sizing in one place means every renderer produces a
consistent, deliberately designed look instead of stock Office defaults. Change
a value here and presentations, documents, and spreadsheets all follow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


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

    def rgb(self, hex_colour: str):
        from pptx.dml.color import RGBColor

        return RGBColor.from_string(hex_colour)


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
