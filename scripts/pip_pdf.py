"""PIP_SUMMARY_EASY.txt -> A4 PDF.  Monospaced, text only; no numbers are typed here.

Everything is set in one monospaced face.  Prose (unindented lines) is re-wrapped to the portrait
page width.  Tables (runs of lines indented by two spaces) are kept verbatim; a table wider than a
portrait page at the body size is placed on a landscape A4 page of its own, at the size that fits.

  python3 scripts/pip_pdf.py [in.txt] [out.pdf]     (defaults: reports/PIP_SUMMARY_EASY.txt/.pdf)
"""
import sys
import textwrap
from pathlib import Path

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, NextPageTemplate, PageBreak,
                                PageTemplate, Preformatted, Spacer)

ROOT = Path(__file__).resolve().parents[1]
MARGIN = 18 * mm
FONT = "DejaVuSansMono"
pdfmetrics.registerFont(TTFont(FONT, "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"))
CHAR = pdfmetrics.stringWidth("M", FONT, 1)      # width of one char at 1 pt
SIZE = 9.0                                        # body size, pt
PORTRAIT_W = A4[0] - 2 * MARGIN
LANDSCAPE_W = landscape(A4)[0] - 2 * MARGIN
COLS = int(PORTRAIT_W / (SIZE * CHAR))            # chars per portrait line at body size


def style(size):
    return ParagraphStyle("mono", fontName=FONT, fontSize=size, leading=size * 1.3)


def blocks(lines):
    """Yield ('para'|'table', text): tables are runs of 2-space-indented lines (blank lines inside allowed)."""
    i, n = 0, len(lines)
    while i < n:
        if not lines[i].strip():
            i += 1; continue
        if lines[i].startswith("  "):
            j = i
            while j < n and (lines[j].startswith("  ") or (not lines[j].strip() and j + 1 < n and lines[j + 1].startswith("  "))):
                j += 1
            yield "table", "\n".join(l.rstrip() for l in lines[i:j]); i = j; continue
        j = i + 1
        while j < n and lines[j].strip() and not lines[j].startswith("  "):
            j += 1
        yield "para", " ".join(l.strip() for l in lines[i:j]); i = j


def build(src: Path, dst: Path):
    lines = src.read_text().splitlines()
    doc = BaseDocTemplate(str(dst), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
                          title=src.stem.replace("_", " "), author="EASY plan generator (scripts/easyplan.py)")

    def footer(canvas, d):
        canvas.saveState(); canvas.setFont(FONT, 7)
        canvas.drawRightString(d.pagesize[0] - MARGIN, 10 * mm, f"{src.name}   page {d.page}")
        canvas.restoreState()

    P, L = A4, landscape(A4)
    doc.addPageTemplates([
        PageTemplate("portrait", [Frame(MARGIN, MARGIN, P[0] - 2 * MARGIN, P[1] - 2 * MARGIN, id="f")], pagesize=P, onPage=footer),
        PageTemplate("landscape", [Frame(MARGIN, MARGIN, L[0] - 2 * MARGIN, L[1] - 2 * MARGIN, id="f")], pagesize=L, onPage=footer),
    ])

    story, orient, caption = [], "portrait", None

    def switch(to):
        nonlocal orient
        if to != orient:
            story.extend([NextPageTemplate(to), PageBreak()]); orient = to

    def emit(items):
        story.append(KeepTogether(items)); story.append(Spacer(0, SIZE))

    items = list(blocks(lines))
    for k, (kind, text) in enumerate(items):
        if kind == "para":
            # a short line directly before a table is its caption: travel with the table
            if k + 1 < len(items) and items[k + 1][0] == "table" and len(text) <= 2 * COLS:
                caption = text; continue
            switch("portrait")
            emit([Preformatted(textwrap.fill(text, COLS), style(SIZE))])
        else:
            width = max(len(l) for l in text.splitlines())
            size, to = SIZE, "portrait"
            if width > COLS:
                to, size = "landscape", min(SIZE, LANDSCAPE_W / (width * CHAR))
            # shrink so the table (with its caption) also fits one page in height
            frame_h = (L if to == "landscape" else P)[1] - 2 * MARGIN - 2 * SIZE
            rows = len(text.splitlines()) + (3 if caption else 0)
            size = min(size, frame_h / (rows * 1.3))
            switch(to)
            group = []
            if caption:
                cols = int((LANDSCAPE_W if to == "landscape" else PORTRAIT_W) / (size * CHAR))
                group += [Preformatted(textwrap.fill(caption, cols), style(size)), Spacer(0, size)]; caption = None
            group.append(Preformatted(text, style(size)))
            emit(group)
    doc.build(story)
    return dst


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "reports/PIP_SUMMARY_EASY.txt"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".pdf")
    print(build(src, dst))
