"""Synthetic test inputs: printed bird ringing forms drawn with OpenCV and PDFs made with PIL."""

import shutil
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Pages 1-2 are the former scan_1972_sample.pdf.
SAMPLE_PDF = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_CdB_1_20231125160539.pdf"
INPUT_DIR = PROJECT_ROOT / "data" / "input"

HAS_PDFTOPPM = shutil.which("pdftoppm") is not None
requires_pdftoppm = pytest.mark.skipif(not HAS_PDFTOPPM, reason="pdftoppm (poppler-utils) not installed")

# Column widths of the synthetic form: 11 printed columns, the wide species column second (like the real form).
FORM_COL_WIDTHS = [80, 360] + [80] * 9
FORM_LEFT, FORM_TOP, FORM_HEADER_H = 100, 150, 110


def draw_form(
    rows: int = 20,
    row_h: int = 70,
    col_widths: list[int] = FORM_COL_WIDTHS,
    size: tuple[int, int] = (1500, 2000),
    thickness: int = 3,
) -> tuple[np.ndarray, list[int], list[int]]:
    """A white page with a printed table: header row + ``rows`` data rows, ``len(col_widths) + 1`` vertical lines.

    Returns ``(bgr_image, xs, ys)`` with the x of every vertical line and the y of every horizontal line
    (top border, header bottom, then the bottom of each data row) in page pixels.
    """
    w, h = size
    img = np.full((h, w, 3), 255, np.uint8)
    xs = [FORM_LEFT]
    for cw in col_widths:
        xs.append(xs[-1] + cw)
    header_bottom = FORM_TOP + FORM_HEADER_H
    ys = [FORM_TOP, header_bottom] + [header_bottom + row_h * (i + 1) for i in range(rows)]
    for y in ys:
        cv2.line(img, (xs[0], y), (xs[-1], y), (0, 0, 0), thickness)
    for x in xs:
        cv2.line(img, (x, ys[0]), (x, ys[-1]), (0, 0, 0), thickness)
    return img, xs, ys


def write_pdf(path: Path, pages: list[np.ndarray], dpi: int = 200) -> Path:
    """Write BGR images as a multi-page PDF. At ``dpi`` = the ImgExtractor DPI, pixel sizes round-trip."""
    images = [Image.fromarray(cv2.cvtColor(p, cv2.COLOR_BGR2RGB)) for p in pages]
    images[0].save(path, "PDF", resolution=dpi, save_all=True, append_images=images[1:])
    return path


def col_widths(geom: dict) -> list[int]:
    bounds = geom["col_bounds"]
    return [b - a for a, b in pairwise(bounds)]


def read_image(path: Path) -> np.ndarray:
    """``cv2.imread`` that fails loudly instead of returning None for a missing or broken file."""
    img = cv2.imread(str(path))
    assert img is not None, f"cannot read image {path}"
    return img
