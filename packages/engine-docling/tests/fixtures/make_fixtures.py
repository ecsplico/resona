"""Generate the synthetic, non-sensitive document fixtures used by the engine-docling tests.

Run from the repository root (no project environment needed):

    uv run --no-project --with reportlab --with pillow \
        python packages/engine-docling/tests/fixtures/make_fixtures.py

Outputs (next to this script):
  digital.pdf  2 pages, German text with umlauts, text layer present.
  scan.pdf     2 pages, the same text rasterized to a grayscale JPEG; no text layer.
  table.pdf    page 1: 3x4 grid with 12 distinct cell values;
               page 2: two text columns (left column must be read first).
  scan_en.pdf  1 page, English text rasterized to an image-only PDF (small, for English OCR tests).

The text is invented for testing. Raster pages use macOS Arial (rendered with PIL);
vector PDFs use Helvetica (standard base font, WinAnsi, umlauts supported).
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

HERE = Path(__file__).resolve().parent
ARIAL = Path("/System/Library/Fonts/Supplemental/Arial.ttf")

PAGE_TEXT: list[list[str]] = [
    [
        "Dieses Dokument ist ein synthetisches Testdokument.",
        "Die Prüfung ergab, dass Größen über dem Schwellenwert liegen.",
        "Die Straße führt nördlich über die Brücke.",
    ],
    [
        "Übergewicht und Müdigkeit wurden nicht beobachtet.",
        "Die Wärme im Büro bleibt im Rahmen der Vorgaben.",
    ],
]

# One English page for the tesseract (English language pack) force-OCR test.
ENGLISH_PAGE_TEXT: list[list[str]] = [
    [
        "This is a synthetic test document.",
        "The values remain below the threshold.",
        "The road leads north across the bridge.",
    ],
]

TABLE_CELLS: list[list[str]] = [
    ["Äpfel", "Größe 12", "Übung 4", "Straße A"],
    ["Löwe 7", "Brücke B", "Fuß 9", "Mühle 21"],
    ["Öl 5", "Kälte C", "Höhe 88", "Wärme 6"],
]

COLUMN_LEFT = [
    "Linkspalte Absatz eins: Die Versuchsreihe beginnt im linken Feld.",
    "Linkspalte Absatz zwei: Sie endet vor der rechten Spalte.",
]
COLUMN_RIGHT = [
    "Rechtsspalte Absatz eins: Dieser Text folgt erst nach links.",
    "Rechtsspalte Absatz zwei: Das ist der letzte Satz des Testdokuments.",
]


def make_digital(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4)
    for lines in PAGE_TEXT:
        text = pdf.beginText(2.5 * cm, A4[1] - 3 * cm)
        text.setFont("Helvetica", 13)
        text.setLeading(22)
        for line in lines:
            text.textLine(line)
        pdf.drawText(text)
        pdf.showPage()
    pdf.save()


def _raster_page(lines: list[str]) -> Image.Image:
    dpi = 150
    width, height = int(8.27 * dpi), int(11.69 * dpi)
    image = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(ARIAL), 40)
    y = int(2.5 * dpi)
    for line in lines:
        draw.text((int(0.9 * dpi), y), line, fill=0, font=font)
        y += 90
    return image


def make_scan(path: Path, pages: list[list[str]] = PAGE_TEXT) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4)
    for lines in pages:
        buffer = io.BytesIO()
        _raster_page(lines).save(buffer, format="JPEG", quality=70)
        buffer.seek(0)
        pdf.drawImage(ImageReader(buffer), 0, 0, width=A4[0], height=A4[1])
        pdf.showPage()
    pdf.save()


def make_table(path: Path) -> None:
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    body.fontName = "Helvetica"
    body.fontSize = 12
    grid = Table(TABLE_CELLS, colWidths=[4 * cm, 4 * cm, 4 * cm, 4 * cm])
    grid.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.8, colors.black)]))

    doc = BaseDocTemplate(str(path), pagesize=A4, leftMargin=2.5 * cm, rightMargin=2.5 * cm)
    frame_full = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="full")
    column_width = (doc.width - 1 * cm) / 2
    frame_left = Frame(doc.leftMargin, doc.bottomMargin, column_width, doc.height, id="left")
    frame_right = Frame(
        doc.leftMargin + column_width + 1 * cm, doc.bottomMargin, column_width, doc.height, id="right"
    )
    doc.addPageTemplates(
        [
            PageTemplate(id="single", frames=[frame_full]),
            PageTemplate(id="columns", frames=[frame_left, frame_right]),
        ]
    )
    story = [
        Paragraph("Tabelle mit zwölf Zellen", styles["Heading2"]),
        Spacer(1, 0.4 * cm),
        grid,
        NextPageTemplate("columns"),
        PageBreak(),
        Paragraph("Zweispaltiger Text", styles["Heading2"]),
        Spacer(1, 0.3 * cm),
    ]
    for paragraph in COLUMN_LEFT:
        story += [Paragraph(paragraph, body), Spacer(1, 0.3 * cm)]
    story.append(FrameBreak())
    for paragraph in COLUMN_RIGHT:
        story += [Paragraph(paragraph, body), Spacer(1, 0.3 * cm)]
    doc.build(story)


def main() -> None:
    make_digital(HERE / "digital.pdf")
    make_scan(HERE / "scan.pdf")
    make_table(HERE / "table.pdf")
    make_scan(HERE / "scan_en.pdf", ENGLISH_PAGE_TEXT)
    for name in ("digital.pdf", "scan.pdf", "table.pdf", "scan_en.pdf"):
        print(name, (HERE / name).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
