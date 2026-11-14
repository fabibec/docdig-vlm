"""Pipeline step: detect sideways (90°/270°) or upside-down (180°) tables and rotate raw/cropped artifacts upright."""

from __future__ import annotations

import logging
from typing import ClassVar

import cv2

from backend.pipeline.models.pipeline_parts import PipelineStep, PipelineTask, page_stem
from backend.pipeline.models.results import PageGeometry
from backend.pipeline.steps.table_extractor import TableExtractor

log = logging.getLogger(__name__)


class TableOrientationDetector(PipelineStep):
    """Detects sideways (90°/270°) or upside-down (180°) tables and flips earlier step outputs upright."""

    # Using "cropped" ensures that selective pipeline runs (e.g. --steps cropped)
    # execute orientation detection as part of the cropping phase, and avoids
    # mismatches with ArtifactState in the API service.
    name: ClassVar[str] = "cropped"

    def __init__(self, draw_rows: bool = False):
        self.draw_rows = draw_rows

    def setup(self) -> None:
        """Lifecycle setup hook (called once per process)."""
        super().setup()

    def close(self) -> None:
        """Lifecycle teardown hook (called once per process)."""
        super().close()

    def execute(self, task: PipelineTask) -> None:
        """Inspect cropped geometry; if sideways, rotate raw image and re-extract table crops."""
        raw_dir = task.step_dir("raw")
        cropped_dir = task.step_dir("cropped")

        # Discover pages to process; check cropped directory first, fallback to raw
        nums = task.page_nums("cropped")
        if not nums:
            nums = task.page_nums("raw")

        for num in nums:
            stem = page_stem(num)
            geom_path = cropped_dir / f"{stem}.geom.json"
            raw_path = raw_dir / f"{stem}.jpg"

            ctx = {"pdf": task.input_pdf.name, "step": self.name, "page": num}

            if not geom_path.exists() or not raw_path.exists():
                log.debug("Skipping orientation check for %s: missing geom or raw file", stem, extra=ctx)
                continue

            try:
                geom = PageGeometry.model_validate_json(geom_path.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("Skipping orientation check for %s: invalid geometry JSON (%s)", stem, e, extra=ctx)
                continue

            num_rows = len(geom.row_bounds)
            num_cols = len(geom.col_bounds) - 1 if len(geom.col_bounds) > 0 else 0

            # Edge cases: non-table or empty bounds
            if num_rows == 0 or num_cols <= 0:
                log.debug("Skipping orientation check for %s: empty row or column bounds", stem, extra=ctx)
                continue

            rot_code: int | None = None
            angle_desc: str = ""
            angle: int = 0  # correction applied, degrees clockwise

            # Case 1: Sideways table (more columns than rows and width > height)
            if (num_cols > num_rows) and (geom.width > geom.height):
                row_heights = [ye - ys for ys, ye in geom.row_bounds]
                if not row_heights:
                    continue

                max_idx = row_heights.index(max(row_heights))
                if max_idx < len(row_heights) / 2:
                    # 90° clockwise: wide column is near the top -> rotate 90° CCW
                    rot_code = cv2.ROTATE_90_COUNTERCLOCKWISE
                    angle = 270
                    angle_desc = "90° clockwise (rotating 90° counter-clockwise)"
                else:
                    # 270° clockwise (90° CCW): wide column is near the bottom -> rotate 90° CW
                    rot_code = cv2.ROTATE_90_CLOCKWISE
                    angle = 90
                    angle_desc = "270° clockwise (rotating 90° clockwise)"

            # Case 2: Upside-down table (portrait aspect ratio, rows >= cols, inverted columns or header)
            elif (geom.height >= geom.width) and (num_rows >= num_cols):
                col_widths = [geom.col_bounds[i + 1] - geom.col_bounds[i] for i in range(len(geom.col_bounds) - 1)]
                if not col_widths:
                    continue

                max_col_idx = col_widths.index(max(col_widths))
                has_reversed_cols = (len(col_widths) > 1) and (max_col_idx >= len(col_widths) / 2)
                has_bottom_header = (
                    geom.header is not None and len(geom.header) > 0 and geom.header[0] >= geom.height / 2
                )

                if has_reversed_cols or has_bottom_header:
                    rot_code = cv2.ROTATE_180
                    angle = 180
                    angle_desc = "180° upside-down (rotating 180°)"

            if rot_code is None:
                log.debug(
                    "Page %s is upright (rows=%d, cols=%d, %dx%d)",
                    stem,
                    num_rows,
                    num_cols,
                    geom.width,
                    geom.height,
                    extra={**ctx, "rows": num_rows, "cols": num_cols},
                )
                continue

            log.info("Detected rotated table on page %d (%s): %s", num, stem, angle_desc, extra={**ctx, "angle": angle})

            raw_img = cv2.imread(str(raw_path))
            if raw_img is None:
                log.warning("Could not read raw image at %s for rotation", raw_path, extra=ctx)
                continue

            # Rotate and overwrite raw image on disk
            rotated_img = cv2.rotate(raw_img, rot_code)
            cv2.imwrite(str(raw_path), rotated_img)

            # Re-extract table using TableExtractor to regenerate upright cropped outputs
            TableExtractor.extract_printed_table(
                raw_path,
                cropped_dir,
                draw_rows=self.draw_rows,
            )
            log.info("Successfully restored upright table on page %d (%s)", num, stem, extra={**ctx, "angle": angle})
