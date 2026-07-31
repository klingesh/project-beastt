"""Turn a structured spec into a real Office document.

The LLM produces a JSON spec (see docgen.py); this module renders it into an
actual .pptx / .docx / .xlsx file. Rendering is kept separate from generation so
the layout code is deterministic and testable, and a weak model can't produce a
corrupt file -- only a thin or oddly-worded one.

Each builder degrades gracefully: if the relevant library isn't installed, it
raises a clear message telling the user exactly what to install.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .paths import project_root


def output_dir() -> Path:
    path = project_root() / "beastt_output"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(title: str, suffix: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", title or "document").strip("_")[:60]
    slug = slug or "document"
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return output_dir() / f"{slug}_{stamp}{suffix}"


class MissingLibrary(RuntimeError):
    pass


# --- PowerPoint -------------------------------------------------------------
def build_presentation(spec: Dict) -> Path:
    """Render a slide deck. Spec: {title, subtitle, slides:[{title, bullets[], notes}]}."""
    try:
        from pptx import Presentation
        from pptx.util import Pt
    except Exception as exc:
        raise MissingLibrary(
            f"PowerPoint support needs python-pptx ({exc}). Run: pip install python-pptx"
        )

    title = spec.get("title") or "Presentation"
    prs = Presentation()

    # Title slide
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = title
    if len(slide.placeholders) > 1:
        slide.placeholders[1].text = spec.get("subtitle") or datetime.now().strftime("%B %d, %Y")

    for item in spec.get("slides", []):
        layout = prs.slide_layouts[1]  # Title and Content
        s = prs.slides.add_slide(layout)
        s.shapes.title.text = str(item.get("title") or "")

        bullets = [str(b) for b in item.get("bullets", []) if str(b).strip()]
        if bullets and len(s.placeholders) > 1:
            frame = s.placeholders[1].text_frame
            frame.clear()
            for idx, bullet in enumerate(bullets):
                para = frame.paragraphs[0] if idx == 0 else frame.add_paragraph()
                para.text = bullet
                para.level = 0
                for run in para.runs:
                    run.font.size = Pt(18)

        notes = item.get("notes")
        if notes:
            s.notes_slide.notes_text_frame.text = str(notes)

    path = _safe_name(title, ".pptx")
    prs.save(path)
    return path


# --- Word -------------------------------------------------------------------
def build_document(spec: Dict) -> Path:
    """Render a text document. Spec: {title, subtitle, sections:[{heading, paragraphs[], bullets[]}]}."""
    try:
        from docx import Document
    except Exception as exc:
        raise MissingLibrary(
            f"Word support needs python-docx ({exc}). Run: pip install python-docx"
        )

    title = spec.get("title") or "Document"
    doc = Document()
    doc.add_heading(title, level=0)
    if spec.get("subtitle"):
        doc.add_paragraph(str(spec["subtitle"]))

    for section in spec.get("sections", []):
        heading = section.get("heading")
        if heading:
            doc.add_heading(str(heading), level=1)
        for para in section.get("paragraphs", []) or []:
            if str(para).strip():
                doc.add_paragraph(str(para))
        for bullet in section.get("bullets", []) or []:
            if str(bullet).strip():
                doc.add_paragraph(str(bullet), style="List Bullet")

    path = _safe_name(title, ".docx")
    doc.save(path)
    return path


# --- Excel ------------------------------------------------------------------
def build_spreadsheet(spec: Dict) -> Path:
    """Render a workbook. Spec: {title, sheets:[{name, columns[], rows[[...]]}]}."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
    except Exception as exc:
        raise MissingLibrary(
            f"Excel support needs openpyxl ({exc}). Run: pip install openpyxl"
        )

    title = spec.get("title") or "Workbook"
    wb = Workbook()
    sheets = spec.get("sheets") or []
    if not sheets:
        sheets = [{"name": "Sheet1", "columns": [], "rows": []}]

    for idx, sheet in enumerate(sheets):
        name = str(sheet.get("name") or f"Sheet{idx + 1}")[:31]
        ws = wb.active if idx == 0 else wb.create_sheet()
        ws.title = name

        columns = [str(c) for c in (sheet.get("columns") or [])]
        if columns:
            ws.append(columns)
            for cell in ws[1]:
                cell.font = Font(bold=True)

        for row in sheet.get("rows") or []:
            if isinstance(row, dict):  # tolerate dict rows keyed by column
                ws.append([row.get(col, "") for col in columns])
            elif isinstance(row, (list, tuple)):
                ws.append(list(row))
            else:
                ws.append([row])

        # Roughly size columns to their content for readability.
        widths: List[int] = []
        for row in ws.iter_rows(values_only=True):
            for i, value in enumerate(row):
                length = len(str(value)) if value is not None else 0
                if i >= len(widths):
                    widths.append(length)
                else:
                    widths[i] = max(widths[i], length)
        for i, width in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 60)

    path = _safe_name(title, ".xlsx")
    wb.save(path)
    return path


BUILDERS = {
    "presentation": build_presentation,
    "document": build_document,
    "spreadsheet": build_spreadsheet,
}


def build(kind: str, spec: Dict) -> Path:
    builder = BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"Unknown document kind: {kind}")
    return builder(spec)
