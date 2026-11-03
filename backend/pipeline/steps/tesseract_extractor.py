from __future__ import annotations

import shutil
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from tabulate import tabulate

from backend.pipeline.models.pipeline_parts import PipelineStep, PipelineTask, page_stem
from backend.pipeline.models.results import PageGeometry


@dataclass(frozen=True)
class FieldSpec:
    """One semantic column on the 74-box Swiss Vogelwarte sheet (1-based box span; 75 = 'Leer lassen')."""

    name: str
    boxes: tuple[int, int]
    whitelist: str


# Columns of the upright 74-box form (scan_new), used to assign per-box character whitelists.
VOGELWARTE_74_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("ring_no", (1, 8), "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    FieldSpec("species_no", (9, 12), "0123456789"),
    FieldSpec("species", (13, 18), "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    FieldSpec("sex", (19, 19), "012"),
    FieldSpec("age", (20, 20), "0123456"),
    FieldSpec("n", (21, 21), "0123456789AB"),
    FieldSpec("st", (22, 22), "012"),
    FieldSpec("date", (23, 26), "0123456789"),
    FieldSpec("time", (27, 28), "0123456789"),
    FieldSpec("kt", (29, 30), "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    FieldSpec("coords", (31, 38), "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    FieldSpec("alt", (39, 41), "0123456789"),
    FieldSpec("place", (42, 61), "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    FieldSpec("feather_mm", (62, 65), "0123456789"),
    FieldSpec("weight_g", (66, 69), "0123456789"),
    FieldSpec("wing_mm", (70, 72), "0123456789"),
    FieldSpec("p", (73, 74), "0123456789"),
    FieldSpec("remark", (75, 75), "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
)

BOX_WHITELISTS: dict[int, str] = {
    box: spec.whitelist for spec in VOGELWARTE_74_FIELDS for box in range(spec.boxes[0], spec.boxes[1] + 1)
}


class TesseractExtractor(PipelineStep):
    """Experimental cell-by-cell OCR on ``cropped/`` -> ``tesseract/pNNN.md`` using pytesseract."""

    name = "tesseract"

    def __init__(
        self,
        lang: str = "eng",
        border_erase_px: int = 3,
        min_ink_ratio: float = 0.02,
        save_cells: bool = False,
    ):
        self.lang = lang
        self.border_erase_px = border_erase_px
        self.min_ink_ratio = min_ink_ratio
        self.save_cells = save_cells

    def setup(self) -> None:
        if shutil.which(pytesseract.pytesseract.tesseract_cmd) is None:
            raise RuntimeError(
                "tesseract binary not found on PATH. Install it first (e.g. `sudo dnf install tesseract` "
                "or `sudo apt install tesseract-ocr`)."
            )

    def _preprocess_cell(self, cell_bgr: np.ndarray, include_black: bool = False) -> tuple[np.ndarray, float]:
        """Extract ink mask from a single cell crop, returning (padded black-on-white image, ink_ratio)."""
        if cell_bgr.ndim == 3:
            b_ch, _g_ch, r_ch = cv2.split(cell_bgr)
            # Blue ballpoint ink has B > R, while neutral gray grid lines and column shading have B ≈ R.
            blue_score = np.clip(b_ch.astype(np.int16) - r_ch.astype(np.int16), 0, 255).astype(np.uint8)
            ink_mask = (blue_score > 10).astype(np.uint8) * 255
        else:
            ink_mask = np.zeros_like(cell_bgr)

        if include_black or np.count_nonzero(ink_mask) == 0:
            gray = cv2.cvtColor(cell_bgr, cv2.COLOR_BGR2GRAY) if cell_bgr.ndim == 3 else cell_bgr
            bg = float(np.percentile(gray, 85))
            dark_mask = ((bg - gray) > 35).astype(np.uint8) * 255
            b = self.border_erase_px
            dark_mask[:b, :] = 0
            dark_mask[-b:, :] = 0
            dark_mask[:, :b] = 0
            dark_mask[:, -b:] = 0
            ink_mask = cv2.bitwise_or(ink_mask, dark_mask)

        ink_ratio = float(np.count_nonzero(ink_mask)) / float(ink_mask.size) if ink_mask.size else 0.0
        if ink_ratio >= self.min_ink_ratio and (pts := cv2.findNonZero(ink_mask)) is not None:
            bx, by, bw, bh = cv2.boundingRect(pts)
            ink_mask = ink_mask[by : by + bh, bx : bx + bw]

        clean = 255 - ink_mask
        padded = cv2.copyMakeBorder(clean, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
        return padded, ink_ratio

    def _ocr_cell(
        self,
        cell_bgr: np.ndarray,
        whitelist: str = "",
        single_char: bool = False,
        include_black: bool = False,
    ) -> str:
        prep, ink_ratio = self._preprocess_cell(cell_bgr, include_black=include_black)
        if ink_ratio < self.min_ink_ratio:
            return ""

        psms = (10, 8) if single_char else (7, 6)
        for psm in psms:
            config = f"--oem 3 --psm {psm}"
            if whitelist:
                config += f" -c tessedit_char_whitelist={whitelist}"
            text = "".join(pytesseract.image_to_string(prep, lang=self.lang, config=config).split())
            if text:
                return text[0] if single_char else text
        return ""

    def extract_page(
        self,
        img_path: Path,
        geom: PageGeometry,
        cells_dir: Path | None = None,
    ) -> list[dict[str, str]]:
        img = cv2.imread(str(img_path))
        if img is None:
            raise FileNotFoundError(f"Could not load image at {img_path}")

        rows: list[dict[str, str]] = []
        is_74box = len(geom.col_bounds) - 1 == 75 and len(geom.row_bounds) > 1
        if is_74box:
            if len(geom.row_bounds) >= 26:
                # Full sheet: header is column titles, row_bounds[0] is the '1..74' row.
                data_row_bounds = geom.row_bounds[1:]
            elif len(geom.row_bounds) == 25:
                # Text header cut off: header is the '1..74' row, row_bounds are the 25 bird rows.
                data_row_bounds = geom.row_bounds
            else:
                # Both text header and '1..74' row cut off: header is bird row 1.
                data_row_bounds = [geom.header, *geom.row_bounds]
        else:
            data_row_bounds = geom.row_bounds

        for row_idx, (y0, y1) in enumerate(data_row_bounds, 1):
            y1_ext = min(img.shape[0], y1 + 3) if is_74box else y1
            row_data: dict[str, str] = {"row": str(row_idx)}
            for col_idx, (x0, x1) in enumerate(pairwise(geom.col_bounds), 1):
                cell = img[y0:y1_ext, x0:x1]
                whitelist = BOX_WHITELISTS.get(col_idx, "") if is_74box else ""
                single_char = is_74box and col_idx <= 74
                include_black = not is_74box or col_idx == 8

                if cells_dir is not None:
                    cells_dir.mkdir(parents=True, exist_ok=True)
                    prep, _ = self._preprocess_cell(cell, include_black=include_black)
                    cv2.imwrite(str(cells_dir / f"r{row_idx:02d}_c{col_idx:02d}.jpg"), prep)

                row_data[str(col_idx)] = self._ocr_cell(
                    cell,
                    whitelist=whitelist,
                    single_char=single_char,
                    include_black=include_black,
                )
            rows.append(row_data)
        return rows

    def execute(self, task: PipelineTask) -> None:
        cropped_dir = task.step_dir("cropped")
        out_dir = task.step_dir(self.name)
        out_dir.mkdir(parents=True, exist_ok=True)

        nums = task.page_nums("cropped")
        for i, num in enumerate(nums, 1):
            stem = page_stem(num)
            geom = PageGeometry.model_validate_json((cropped_dir / f"{stem}.geom.json").read_text("utf-8"))
            cells_dir = (out_dir / "cells" / stem) if self.save_cells else None
            rows = self.extract_page(cropped_dir / f"{stem}.jpg", geom, cells_dir=cells_dir)
            md = tabulate(rows, headers="keys", tablefmt="pipe", disable_numparse=True)
            (out_dir / f"{stem}.md").write_text(md, encoding="utf-8")
            task.report(self.name, i, len(nums))
