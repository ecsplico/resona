"""Turn scanned pages upright before Docling sees them.

Docling 2.129 runs layout, OCR and reading order on the page as scanned; an upside-down
or quarter-turned page yields reversed line order and garbled OCR. Each page is rendered
at :data:`OSD_DPI`, Tesseract's orientation detection (``--psm 0``, ``osd`` traineddata)
names the clockwise turn that makes it upright, and the page is turned before conversion:
PDF pages through their ``/Rotate`` entry (text layer and vector content stay intact),
raster images by rewriting the pixels into a lossless TIFF.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps, ImageSequence

# 200 dpi read every page of the eval corpus and the test fixtures in all four orientations;
# at 150 dpi small print (eval/corpus/small_print_scan.pdf) confused the two quarter turns.
OSD_DPI = 200
# Tesseract's "Orientation confidence" for the winning orientation. Pages with text read
# 3.4-19.9 at 200 dpi; the wrong quarter turns of small print at 150 dpi read below 0.2.
MIN_CONFIDENCE = 2.0

_TURNS = frozenset({90, 180, 270})


@dataclass(frozen=True)
class Rotation:
    """One page turned ``degrees`` clockwise to make it upright (1-based page number)."""

    page: int
    degrees: int


def detect_rotation(image: Image.Image, *, tesseract: str, workdir: Path) -> int:
    """Clockwise degrees (0, 90, 180, 270) that make ``image`` upright; 0 when unsure.

    Tesseract refuses pages with too little text ("Too few characters"): those stay as they are.
    """
    png = workdir / "osd.png"
    image.save(png, format="PNG")
    completed = subprocess.run(
        [tesseract, str(png), "-", "--psm", "0", "--dpi", str(OSD_DPI)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        return 0
    fields = dict(line.split(":", 1) for line in completed.stdout.splitlines() if ":" in line)
    try:
        degrees = int(fields["Rotate"].strip())
        confidence = float(fields["Orientation confidence"].strip())
    except (KeyError, ValueError):
        return 0
    return degrees if degrees in _TURNS and confidence >= MIN_CONFIDENCE else 0


def upright(
    path: Path, *, first_page: int, last_page: int, tesseract: str, workdir: Path
) -> tuple[Path, list[Rotation]]:
    """Return the file to convert and the pages turned within ``first_page``..``last_page``.

    Without a turned page the input path itself is returned and nothing is written.
    """
    if path.suffix.lower() == ".pdf":
        return _upright_pdf(path, first_page, last_page, tesseract, workdir)
    return _upright_image(path, first_page, last_page, tesseract, workdir)


def _upright_pdf(
    path: Path, first_page: int, last_page: int, tesseract: str, workdir: Path
) -> tuple[Path, list[Rotation]]:
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError:
        # Unreadable (damaged, encrypted): Docling reports the conversion failure itself.
        return path, []
    try:
        rotations: list[Rotation] = []
        for index in range(first_page - 1, min(last_page, len(pdf))):
            page = pdf[index]
            # Rendering applies the page's current /Rotate, so the detected turn adds to it.
            image = page.render(scale=OSD_DPI / 72, grayscale=True).to_pil()
            degrees = detect_rotation(image, tesseract=tesseract, workdir=workdir)
            if degrees:
                page.set_rotation((page.get_rotation() + degrees) % 360)
                rotations.append(Rotation(page=index + 1, degrees=degrees))
        if not rotations:
            return path, []
        out = workdir / "upright.pdf"
        pdf.save(out)
        return out, rotations
    finally:
        pdf.close()


def _upright_image(
    path: Path, first_page: int, last_page: int, tesseract: str, workdir: Path
) -> tuple[Path, list[Rotation]]:
    with Image.open(path) as source:
        dpi = source.info.get("dpi")
        # Docling applies the EXIF orientation itself; detection must see the same upright pixels.
        frames = [ImageOps.exif_transpose(frame.copy()) for frame in ImageSequence.Iterator(source)]
    rotations: list[Rotation] = []
    for index, frame in enumerate(frames):
        if not first_page <= index + 1 <= last_page:
            continue
        degrees = detect_rotation(frame.convert("L"), tesseract=tesseract, workdir=workdir)
        if degrees:
            # PIL turns counterclockwise.
            frames[index] = frame.rotate(-degrees, expand=True)
            rotations.append(Rotation(page=index + 1, degrees=degrees))
    if not rotations:
        return path, []
    if dpi is not None and rotations[0].page == 1 and rotations[0].degrees != 180:
        dpi = (dpi[1], dpi[0])
    out = workdir / "upright.tiff"
    extra = {"dpi": dpi} if dpi is not None else {}
    frames[0].save(out, format="TIFF", save_all=True, append_images=frames[1:], compression="tiff_lzw", **extra)
    return out, rotations
