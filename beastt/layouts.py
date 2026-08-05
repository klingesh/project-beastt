"""Slide layouts.

Each layout is a small function that draws one kind of slide, so a deck can mix
bullet slides, comparisons, quotes, statistics, timelines and image slides
instead of repeating a single template. The model picks a layout per slide, and
the user can override it.

Everything is composed from shapes and text boxes rather than Office
placeholders, which is what makes the result look designed rather than templated.
"""

from __future__ import annotations

from typing import Dict, List, Optional

# Layouts the model may choose from, with a description used in prompts and in
# the "list layouts" reply.
CATALOGUE = {
    "bullets": "Classic title with bullet points (splits into two columns when long)",
    "image": "Bullets on the left, a supporting photograph on the right",
    "chart": "A native bar, line or pie chart drawn from real numbers",
    "comparison": "Two headed columns side by side, for before/after or pros/cons",
    "stat": "One big headline number with a short explanation",
    "quote": "A large pull-quote with attribution",
    "timeline": "A horizontal sequence of steps or milestones",
    "section": "A full-colour divider announcing the next part",
    "closing": "Full-colour closing slide",
}

ALIASES = {
    "bullet": "bullets", "points": "bullets", "list": "bullets", "content": "bullets",
    "picture": "image", "photo": "image", "visual": "image", "image_right": "image",
    "compare": "comparison", "versus": "comparison", "vs": "comparison",
    "two_column": "comparison", "columns": "comparison", "prosandcons": "comparison",
    "statistic": "stat", "number": "stat", "kpi": "stat", "bignumber": "stat",
    "graph": "chart", "barchart": "chart", "linechart": "chart", "piechart": "chart",
    "data": "chart", "plot": "chart",
    "pullquote": "quote", "testimonial": "quote",
    "milestones": "timeline", "roadmap": "timeline", "process": "timeline", "steps": "timeline",
    "divider": "section", "sectionbreak": "section", "chapter": "section",
    "end": "closing", "thanks": "closing", "final": "closing",
}


def normalise(name: str) -> str:
    key = str(name or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key in CATALOGUE:
        return key
    return ALIASES.get(key.replace("_", ""), ALIASES.get(key, "bullets"))


def describe() -> str:
    return "\n".join(f"  - {name}: {desc}" for name, desc in CATALOGUE.items())


class SlideBuilder:
    """Drawing helpers shared by every layout."""

    def __init__(self, prs, theme, deck_title: str):
        from pptx.util import Inches

        self.prs = prs
        self.th = theme
        self.deck_title = deck_title
        self.on_primary = theme.on_primary()
        self.W = prs.slide_width
        self.H = prs.slide_height
        self.MARGIN = Inches(0.9)
        self._blank = prs.slide_layouts[6]

    # --- primitives -------------------------------------------------------
    def new(self):
        return self.prs.slides.add_slide(self._blank)

    def rect(self, slide, left, top, width, height, colour):
        from pptx.enum.shapes import MSO_SHAPE

        shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
        shape.fill.solid()
        shape.fill.fore_color.rgb = self.th.rgb(colour)
        shape.line.fill.background()
        shape.shadow.inherit = False
        return shape

    def oval(self, slide, left, top, size, colour):
        from pptx.enum.shapes import MSO_SHAPE

        shape = slide.shapes.add_shape(MSO_SHAPE.OVAL, left, top, size, size)
        shape.fill.solid()
        shape.fill.fore_color.rgb = self.th.rgb(colour)
        shape.line.fill.background()
        shape.shadow.inherit = False
        return shape

    def frame(self, slide, left, top, width, height):
        box = slide.shapes.add_textbox(left, top, width, height)
        frame = box.text_frame
        frame.word_wrap = True
        return frame

    def write(self, frame, text, size, colour, bold=False, font=None,
              align=None, space_after=6, first=False, line_spacing=None):
        from pptx.enum.text import PP_ALIGN
        from pptx.util import Pt

        para = frame.paragraphs[0] if first else frame.add_paragraph()
        para.alignment = align or PP_ALIGN.LEFT
        para.space_after = Pt(space_after)
        if line_spacing:
            para.line_spacing = line_spacing
        run = para.add_run()
        run.text = str(text)
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.name = font or self.th.body_font
        run.font.color.rgb = self.th.rgb(colour)
        return para

    # --- chrome -----------------------------------------------------------
    def header(self, slide, title):
        from pptx.util import Inches, Pt

        self.rect(slide, 0, 0, self.W, Inches(1.15), self.th.primary)
        self.rect(slide, 0, Inches(1.15), self.W, Pt(4), self.th.accent)
        frame = self.frame(slide, Inches(0.7), Inches(0.22),
                          self.W - Inches(1.4), Inches(0.8))
        self.write(frame, title, self.th.slide_title_size, self.on_primary,
                   bold=True, font=self.th.heading_font, first=True)

    def footer(self, slide, number, total):
        from pptx.enum.text import PP_ALIGN
        from pptx.util import Inches

        frame = self.frame(slide, Inches(0.9), self.H - Inches(0.55),
                           self.W - Inches(3), Inches(0.35))
        self.write(frame, self.deck_title, self.th.caption_size,
                   self.th.text_muted, first=True)
        frame = self.frame(slide, self.W - Inches(1.6), self.H - Inches(0.55),
                           Inches(0.9), Inches(0.35))
        self.write(frame, f"{number} / {total}", self.th.caption_size,
                   self.th.text_muted, align=PP_ALIGN.RIGHT, first=True)

    def takeaway(self, slide, message):
        from pptx.util import Inches, Pt

        if not message:
            return
        top = self.H - Inches(1.55)
        self.rect(slide, Inches(0.9), top, self.W - Inches(1.8), Inches(0.72), self.th.light)
        self.rect(slide, Inches(0.9), top, Pt(5), Inches(0.72), self.th.accent)
        frame = self.frame(slide, Inches(1.15), top + Inches(0.12),
                           self.W - Inches(2.3), Inches(0.5))
        self.write(frame, message, 14, self.th.secondary, bold=True, first=True)

    def notes(self, slide, item, picture=None):
        text = str(item.get("notes") or "")
        if picture is not None:
            # Attribution travels with the deck, as the licences require.
            text = (text + "\n\n" if text else "") + f"Image: {picture.credit()}"
        if text:
            slide.notes_slide.notes_text_frame.text = text


# --- individual layouts -----------------------------------------------------
def _bullet_block(sb, slide, bullets, left, top, width, height, columns=True):
    from pptx.util import Emu, Inches

    if not bullets:
        return
    two_col = columns and len(bullets) >= 5
    col_width = width / (2 if two_col else 1)
    groups = (
        [bullets[: (len(bullets) + 1) // 2], bullets[(len(bullets) + 1) // 2 :]]
        if two_col else [bullets]
    )
    for index, group in enumerate(groups):
        if not group:
            continue
        frame = sb.frame(slide, left + Emu(int(col_width)) * index, top,
                         Emu(int(col_width)) - Inches(0.3), height)
        for i, bullet in enumerate(group):
            sb.write(frame, f"▪   {bullet}", sb.th.bullet_size, sb.th.text_dark,
                     space_after=12, first=(i == 0), line_spacing=1.15)


def bullets(sb, item, number, total, picture=None):
    from pptx.util import Inches

    slide = sb.new()
    sb.header(slide, item.get("title", ""))
    points = [str(b) for b in (item.get("bullets") or []) if str(b).strip()]
    top = Inches(1.65)
    height = sb.H - top - Inches(1.2)
    _bullet_block(sb, slide, points, sb.MARGIN, top, sb.W - Inches(1.8), height)
    sb.takeaway(slide, item.get("key_message"))
    sb.footer(slide, number, total)
    sb.notes(slide, item)
    return slide


def image(sb, item, number, total, picture=None):
    """Bullets beside a photograph -- or across the full width if there isn't one."""
    from pptx.util import Inches

    slide = sb.new()
    sb.header(slide, item.get("title", ""))

    top = Inches(1.65)
    height = sb.H - top - Inches(1.2)
    points = [str(b) for b in (item.get("bullets") or []) if str(b).strip()]

    # Place the photograph first, so the text can be sized to whatever is left.
    # A grey panel with a circle in it used to fill this space when no photo came
    # back, which made for the blandest slide in the deck; the content gets the
    # room instead now.
    img_left = int(sb.W * 0.58)
    img_width = int(sb.W - img_left - sb.MARGIN)
    if picture is not None:
        try:
            slide.shapes.add_picture(str(picture.path), img_left, int(top),
                                     width=img_width)
        except Exception:
            picture = None

    if picture is not None:
        _bullet_block(sb, slide, points, sb.MARGIN, top,
                      int(sb.W * 0.55) - Inches(1.0), height, columns=False)
    else:
        _bullet_block(sb, slide, points, sb.MARGIN, top, sb.W - Inches(1.8), height)

    sb.takeaway(slide, item.get("key_message"))
    sb.footer(slide, number, total)
    sb.notes(slide, item, picture)
    return slide


def comparison(sb, item, number, total, picture=None):
    from pptx.util import Inches, Pt

    slide = sb.new()
    sb.header(slide, item.get("title", ""))

    left_col = dict(item.get("left") or {})
    right_col = dict(item.get("right") or {})

    # Models sometimes supply the two headings but leave the points empty, which
    # would render as two bare labels. Fill them from the slide's bullets, and
    # fall back to a bullet slide if there's nothing to put in the columns.
    has_points = bool(left_col.get("points")) or bool(right_col.get("points"))
    if not has_points:
        points = [str(b) for b in (item.get("bullets") or []) if str(b).strip()]
        if not points:
            return bullets(sb, item, number, total)
        half = (len(points) + 1) // 2
        left_col.setdefault("heading", "Now")
        right_col.setdefault("heading", "Next")
        left_col["points"] = points[:half]
        right_col["points"] = points[half:]

    top = Inches(1.7)
    height = sb.H - top - Inches(1.3)
    gap = Inches(0.4)
    width = (sb.W - sb.MARGIN * 2 - gap) / 2

    for index, column in enumerate((left_col, right_col)):
        left = sb.MARGIN + (width + gap) * index
        sb.rect(slide, int(left), int(top), int(width), int(height), sb.th.light)
        sb.rect(slide, int(left), int(top), int(width), Inches(0.55),
                sb.th.secondary if index == 0 else sb.th.primary)
        frame = sb.frame(slide, int(left) + Inches(0.25), int(top) + Inches(0.08),
                         int(width) - Inches(0.5), Inches(0.45))
        sb.write(frame, column.get("heading", ""), 16, sb.on_primary, bold=True,
                 font=sb.th.heading_font, first=True)

        frame = sb.frame(slide, int(left) + Inches(0.25), int(top) + Inches(0.75),
                         int(width) - Inches(0.5), int(height) - Inches(1.0))
        points = [str(p) for p in (column.get("points") or []) if str(p).strip()]
        for i, point in enumerate(points):
            sb.write(frame, f"▪  {point}", 15, sb.th.text_dark, space_after=10,
                     first=(i == 0), line_spacing=1.1)

    sb.footer(slide, number, total)
    sb.notes(slide, item)
    return slide


def stat(sb, item, number, total, picture=None):
    """One big number -- useful for a headline metric."""
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    slide = sb.new()
    sb.header(slide, item.get("title", ""))

    value = str(item.get("stat") or (item.get("bullets") or [""])[0])
    label = str(item.get("stat_label") or item.get("key_message") or "")

    frame = sb.frame(slide, sb.MARGIN, Inches(2.2), sb.W - sb.MARGIN * 2, Inches(1.8))
    sb.write(frame, value, 96, sb.th.primary, bold=True, font=sb.th.heading_font,
             align=PP_ALIGN.CENTER, first=True)
    if label:
        frame = sb.frame(slide, sb.MARGIN, Inches(4.1), sb.W - sb.MARGIN * 2, Inches(0.9))
        sb.write(frame, label, 20, sb.th.text_muted, align=PP_ALIGN.CENTER, first=True)

    # Supporting points, minus any that just repeat the headline figure.
    rest = [
        str(b) for b in (item.get("bullets") or [])
        if str(b).strip() and str(b).strip() != value.strip()
    ]
    if len(rest) >= 2:
        frame = sb.frame(slide, sb.MARGIN, Inches(5.0), sb.W - sb.MARGIN * 2, Inches(1.0))
        sb.write(frame, "   ".join(f"▪  {b}" for b in rest[:3]), 13,
                 sb.th.text_dark, align=PP_ALIGN.CENTER, first=True)

    sb.footer(slide, number, total)
    sb.notes(slide, item)
    return slide


def quote(sb, item, number, total, picture=None):
    from pptx.util import Inches, Pt

    slide = sb.new()
    sb.rect(slide, 0, 0, sb.W, sb.H, sb.th.light)
    sb.rect(slide, 0, 0, Pt(10), sb.H, sb.th.accent)

    text = str(item.get("quote") or (item.get("bullets") or [""])[0])
    frame = sb.frame(slide, Inches(1.4), Inches(2.0), sb.W - Inches(2.8), Inches(3.0))
    sb.write(frame, f"\u201c{text}\u201d", 30, sb.th.primary, bold=False,
             font=sb.th.heading_font, first=True, line_spacing=1.2)

    source = str(item.get("attribution") or item.get("key_message") or "")
    if source:
        frame = sb.frame(slide, Inches(1.4), sb.H - Inches(2.0),
                         sb.W - Inches(2.8), Inches(0.6))
        sb.write(frame, f"— {source}", 16, sb.th.text_muted, first=True)

    sb.footer(slide, number, total)
    sb.notes(slide, item)
    return slide


def timeline(sb, item, number, total, picture=None):
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    slide = sb.new()
    sb.header(slide, item.get("title", ""))

    steps = item.get("timeline") or item.get("steps") or []
    if not steps:
        steps = [{"label": "", "text": str(b)} for b in (item.get("bullets") or [])]
    steps = steps[:5]
    if not steps:
        return bullets(sb, item, number, total)

    mid = sb.H * 0.52
    sb.rect(slide, sb.MARGIN, int(mid), sb.W - sb.MARGIN * 2, Pt(3), sb.th.accent)

    span = (sb.W - sb.MARGIN * 2) / len(steps)
    for index, step in enumerate(steps):
        if isinstance(step, str):
            step = {"label": "", "text": step}
        centre = sb.MARGIN + span * index + span / 2
        dot = Inches(0.28)
        sb.oval(slide, int(centre - dot / 2), int(mid - dot / 2 + Pt(1.5)), dot,
                sb.th.primary)

        label = str(step.get("label") or f"{index + 1}")
        frame = sb.frame(slide, int(centre - span / 2), int(mid - Inches(1.15)),
                         int(span), Inches(0.6))
        sb.write(frame, label, 16, sb.th.secondary, bold=True,
                 align=PP_ALIGN.CENTER, first=True)

        frame = sb.frame(slide, int(centre - span / 2) + Inches(0.1),
                         int(mid + Inches(0.35)), int(span) - Inches(0.2), Inches(1.4))
        sb.write(frame, str(step.get("text") or ""), 13, sb.th.text_dark,
                 align=PP_ALIGN.CENTER, first=True, line_spacing=1.1)

    sb.footer(slide, number, total)
    sb.notes(slide, item)
    return slide


def section(sb, item, number, total, picture=None):
    from pptx.util import Inches, Pt

    slide = sb.new()
    sb.rect(slide, 0, 0, sb.W, sb.H, sb.th.primary)
    sb.rect(slide, sb.MARGIN, sb.H * 0.42, Inches(1.6), Pt(6), sb.th.accent)
    frame = sb.frame(slide, sb.MARGIN, sb.H * 0.45, sb.W - Inches(2.4), Inches(1.4))
    sb.write(frame, item.get("title", ""), 40, sb.on_primary, bold=True,
             font=sb.th.heading_font, first=True)
    subtitle = item.get("key_message") or (item.get("bullets") or [""])[0]
    if subtitle:
        sb.write(frame, str(subtitle), 18, sb.th.accent, space_after=0)
    sb.notes(slide, item)
    return slide


#: Chart kinds we offer, mapped to python-pptx enum member names. Looked up by
#: name rather than imported at module scope so this file still imports when
#: python-pptx is absent -- the whole document feature is optional.
_CHART_KINDS = {
    "bar": "COLUMN_CLUSTERED",
    "column": "COLUMN_CLUSTERED",
    "line": "LINE_MARKERS",
    "pie": "PIE",
    "doughnut": "DOUGHNUT",
}


def chart(sb, item, number, total, picture=None):
    """A native chart, with the bullets alongside it as commentary.

    Native rather than an image: the numbers stay editable in PowerPoint, which
    is the difference between a deck someone can present from and a screenshot.

    If anything about the chart fails we fall back to a bullet slide. A missing
    chart is a disappointment; a half-drawn one is a broken file.
    """
    spec = item.get("chart") or {}
    categories = spec.get("categories") or []
    series = spec.get("series") or []
    if len(categories) < 2 or not series:
        return bullets(sb, item, number, total, picture)

    try:
        from pptx.chart.data import CategoryChartData
        from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
        from pptx.util import Inches, Pt
    except Exception:
        return bullets(sb, item, number, total, picture)

    kind = str(spec.get("type") or "bar").lower()
    chart_type = getattr(XL_CHART_TYPE, _CHART_KINDS.get(kind, "COLUMN_CLUSTERED"))

    slide = sb.new()
    sb.header(slide, item.get("title", ""))

    points = [str(b) for b in (item.get("bullets") or []) if str(b).strip()]
    top = Inches(1.65)
    height = sb.H - top - Inches(1.2)

    # With commentary, the chart takes the left two-thirds; without, the full width.
    if points:
        chart_width = int(sb.W * 0.60) - sb.MARGIN
        _bullet_block(sb, slide, points, int(sb.W * 0.63), top,
                      int(sb.W * 0.37) - sb.MARGIN, height, columns=False)
    else:
        chart_width = int(sb.W) - 2 * sb.MARGIN

    try:
        data = CategoryChartData()
        data.categories = categories
        for one in series:
            data.add_series(one.get("name") or "Series", one.get("values") or [])
        graphic = slide.shapes.add_chart(
            chart_type, sb.MARGIN, top, chart_width, int(height * 0.88), data
        )
    except Exception:
        # Nothing drawn yet beyond the header, but the slide exists -- fill it
        # with whatever text we have so the deck has no blank page.
        spare = points or [t for t in (item.get("key_message"),) if t]
        _bullet_block(sb, slide, spare, sb.MARGIN, top, sb.W - Inches(1.8), height)
        sb.takeaway(slide, item.get("key_message"))
        sb.footer(slide, number, total)
        sb.notes(slide, item)
        return slide

    # Styling is best-effort: a chart with default colours is fine, a crash isn't.
    try:
        obj = graphic.chart
        obj.has_title = False
        obj.font.size = Pt(12)
        obj.font.color.rgb = sb.th.rgb(sb.th.text_dark)
        obj.font.name = sb.th.body_font

        multi = len(series) > 1
        obj.has_legend = multi or kind in ("pie", "doughnut")
        if obj.has_legend:
            obj.legend.position = XL_LEGEND_POSITION.BOTTOM
            obj.legend.include_in_layout = False

        if kind not in ("pie", "doughnut"):
            # A quieter plot area reads as designed rather than as Excel output.
            try:
                value_axis = obj.value_axis
                value_axis.has_major_gridlines = True
                value_axis.major_gridlines.format.line.color.rgb = sb.th.rgb(sb.th.light)
                value_axis.format.line.fill.background()
                obj.category_axis.format.line.color.rgb = sb.th.rgb(sb.th.light)
            except Exception:
                pass
            if not multi:
                # Single series: paint it in the deck's accent colour.
                try:
                    obj.plots[0].series[0].format.fill.solid()
                    obj.plots[0].series[0].format.fill.fore_color.rgb = \
                        sb.th.rgb(sb.th.accent)
                except Exception:
                    pass
    except Exception:
        pass

    sb.takeaway(slide, item.get("key_message"))
    sb.footer(slide, number, total)
    sb.notes(slide, item)
    return slide


RENDERERS = {
    "bullets": bullets,
    "image": image,
    "chart": chart,
    "comparison": comparison,
    "stat": stat,
    "quote": quote,
    "timeline": timeline,
    "section": section,
}


def render(name: str, sb, item: Dict, number: int, total: int, picture=None):
    renderer = RENDERERS.get(normalise(name), bullets)
    return renderer(sb, item, number, total, picture)
