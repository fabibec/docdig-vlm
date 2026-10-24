from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from backend.src.pipeline.models.pipeline_parts import PipelineStep, PipelineTask

SplitMode = Literal["table", "row", "col"]


class TableExtractor(PipelineStep):
    def setup(self):
        return super().setup()

    def get_grid_masks(gray: np.ndarray):
        """Extracts horizontal lines, vertical lines, and ONLY joints belonging to the main table."""
        h_img, w_img = gray.shape[:2]
        thresh = cv2.adaptiveThreshold(
            ~gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, -2
        )

        # Zero out 4 pixels at the extreme edge
        thresh[:4, :] = 0
        thresh[h_img - 4 :, :] = 0
        thresh[:, :4] = 0
        thresh[:, w_img - 4 :] = 0

        thresh_thick = cv2.dilate(thresh, np.ones((3, 3), np.uint8), iterations=1)
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (w_img // 30, 1))
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, h_img // 30))

        h_lines = cv2.morphologyEx(thresh_thick, cv2.MORPH_OPEN, h_kernel)
        v_lines = cv2.morphologyEx(thresh_thick, cv2.MORPH_OPEN, v_kernel)

        # Build the connected wireframe of the table and keep ONLY the largest connected component!
        # This automatically deletes any isolated lines/text
        grid_wireframe = cv2.add(
            cv2.dilate(h_lines, np.ones((5, 5), np.uint8), iterations=2),
            cv2.dilate(v_lines, np.ones((5, 9), np.uint8), iterations=2),
        )
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(grid_wireframe)
        if num_labels > 1:
            # Label 0 is background; find the label with the largest pixel area
            largest_label = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
            main_table_mask = (labels == largest_label).astype(np.uint8) * 255
            # Mask out any h_lines or v_lines that are NOT attached to the main table!
            h_lines = cv2.bitwise_and(h_lines, main_table_mask)
            v_lines = cv2.bitwise_and(v_lines, main_table_mask)

        # Compute joints strictly from the cleaned main table lines
        h_dil = cv2.dilate(h_lines, np.ones((5, 5), np.uint8), iterations=2)
        v_dil = cv2.dilate(v_lines, np.ones((5, 9), np.uint8), iterations=2)
        joints = cv2.bitwise_and(h_dil, v_dil)

        return h_lines, v_lines, joints

    def compute_skew_angle(h_lines: np.ndarray, v_lines: np.ndarray) -> float:
        """Computes exact median table tilt angle directly from line contours."""
        h_img, w_img = h_lines.shape[:2]
        angles = []

        cnts_h, _ = cv2.findContours(h_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        for cnt in cnts_h:
            if cv2.boundingRect(cnt)[2] > w_img // 6:
                vx, vy, _, _ = cv2.fitLine(cnt, cv2.DIST_L2, 0, 0.01, 0.01)
                angle = float(np.degrees(np.arctan2(vy[0], vx[0])))
                if -10.0 < angle < 10.0:
                    angles.append(angle)

        cnts_v, _ = cv2.findContours(v_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        for cnt in cnts_v:
            if cv2.boundingRect(cnt)[3] > h_img // 6:
                vx, vy, _, _ = cv2.fitLine(cnt, cv2.DIST_L2, 0, 0.01, 0.01)
                angle = float(np.degrees(np.arctan2(vy[0], vx[0])))
                dev = angle - 90.0 if angle > 0 else angle + 90.0
                if -10.0 < dev < 10.0:
                    angles.append(dev)

        return float(np.median(angles)) if angles else 0.0

    def group_cell_spans(
        grid_lines: list[int], n: int, outer_start: int, outer_end: int
    ) -> list[tuple[int, int, int, int]]:
        """
        Groups neighbouring cells into chunks of ``n``.

        ``grid_lines`` are the pixel positions of the grid lines (rows or columns).
        Cell i lies between grid_lines[i] and grid_lines[i + 1]. The first chunk
        starts at ``outer_start`` and the last chunk ends at ``outer_end`` so the
        outer padding of the table is kept.

        Returns a list of (first_cell, last_cell, start_px, end_px), with 1-based
        inclusive cell numbers.
        """
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")

        cell_count = len(grid_lines) - 1
        spans = []
        for first in range(0, cell_count, n):
            last = min(first + n, cell_count)  # exclusive index
            start = outer_start if first == 0 else grid_lines[first]
            end = outer_end if last == cell_count else grid_lines[last]
            spans.append((first + 1, last, start, end))
        return spans

    def span_name(stem: str, prefix: str, first: int, last: int) -> str:
        if first == last:
            return f"{stem}_{prefix}{first:02d}.jpg"
        return f"{stem}_{prefix}{first:02d}-{last:02d}.jpg"

    def extract_printed_table(
        img_path: Path,
        output_dir: Path,
        pad_top: int = 8,
        pad_sides: int = 8,
        split: SplitMode = "table",
        n: int = 1,
        outlines: bool = True,
    ):
        """
        Detects the printed table on a scan and writes crops to disk.

        Args:
            img_path: Path to the page image (e.g. ``data/output/img/scan_page_0.jpg``).
            output_dir: Output directory (e.g. ``data/output/table``).
            pad_top: Pixels kept above the first row line.
            pad_sides: Pixels kept left/right of the outer column lines.
            split: What to crop out of the table.
                ``"table"`` writes the whole table as one image (default).
                ``"row"``   writes horizontal strips, one per ``n`` rows.
                ``"col"``   writes vertical strips, one per ``n`` columns.
            n: Number of consecutive rows (``split="row"``) or columns
                (``split="col"``) per output image. ``n=1`` writes every single
                row/column separately.
            outlines: Draws detected tables outlines into the original image and stores
                the result under output_dir / "outlines"

        Raises:
            FileNotFoundError: If the image cannot be read.
            ValueError: If ``split`` is unknown or ``n`` is smaller than 1.
        """
        if split not in ("table", "row", "col"):
            raise ValueError(f"split must be 'table', 'row' or 'col', got {split!r}")
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")

        img_path = Path(img_path)
        img = cv2.imread(str(img_path))
        if img is None:
            raise FileNotFoundError(f"Could not load image at {img_path}")

        output_dir.mkdir(parents=True, exist_ok=True)

        if outlines:
            outlines_dir = output_dir / "outlines"
            outlines_dir.mkdir(parents=True, exist_ok=True)

        h_img, w_img = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Deskew
        h_lines, v_lines, _ = TableExtractor.get_grid_masks(gray)
        skew_angle = TableExtractor.compute_skew_angle(h_lines, v_lines)

        if abs(skew_angle) > 0.05:
            center = (w_img // 2, h_img // 2)
            M_rot = cv2.getRotationMatrix2D(center, skew_angle, 1.0)
            img = cv2.warpAffine(
                img,
                M_rot,
                (w_img, h_img + 25),
                flags=cv2.INTER_CUBIC,
                borderValue=(255, 255, 255),
            )
            h_img, w_img = img.shape[:2]
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Find exact bounds using discrete joint centroids
        h_lines, v_lines, joints = TableExtractor.get_grid_masks(gray)

        # Extract each individual green dot's (x, y) center using connectedComponentsWithStats
        _num_j, _, _, centroids = cv2.connectedComponentsWithStats(joints)
        joint_pts = centroids[1:]  # Skip background label 0

        # Group joint centers by Y-coordinate (within 12px vertically = same horizontal table line)
        joint_pts_sorted_y = joint_pts[np.argsort(joint_pts[:, 1])]
        y_diffs = np.diff(joint_pts_sorted_y[:, 1])
        row_groups = np.split(joint_pts_sorted_y, np.where(y_diffs > 12)[0] + 1)

        # ONLY keep horizontal lines that have at least 4 distinct green dots!
        valid_row_centers = [int(np.mean(g[:, 1])) for g in row_groups if len(g) >= 4]
        row_height = (
            int(np.median(np.diff(valid_row_centers)))
            if len(valid_row_centers) > 5
            else 35
        )

        # Group joint centers by X-coordinate and require at least 5 dots vertically per column line
        joint_pts_sorted_x = joint_pts[np.argsort(joint_pts[:, 0])]
        x_diffs = np.diff(joint_pts_sorted_x[:, 0])
        col_groups = np.split(joint_pts_sorted_x, np.where(x_diffs > 12)[0] + 1)
        valid_col_centers = [int(np.mean(g[:, 0])) for g in col_groups if len(g) >= 5]

        x_min, x_max = valid_col_centers[0], valid_col_centers[-1]
        y_min = valid_row_centers[0]
        y_max = valid_row_centers[-1] + 8

        # Safety check: if vertical lines extend below the last 4-dot row, include that bottom row
        v_in_table = v_lines[y_min:, x_min:x_max]
        v_ys = np.where(v_in_table > 0)[0]
        if len(v_ys):
            max_v_y = y_min + int(np.percentile(v_ys, 99.8))
            if max_v_y > y_max + (row_height * 0.4):
                y_max = max_v_y + int(row_height * 0.2)

        x1 = max(0, x_min - pad_sides)
        y1 = max(0, y_min - pad_top)
        x2 = min(w_img, x_max + pad_sides)
        y2 = min(h_img, y_max)

        if outlines:
            outlines_vis = img.copy()
            outlines_vis[joints > 0] = [0, 255, 0]
            cv2.rectangle(outlines_vis, (x1, y1), (x2, y2), (0, 0, 255), 4)
            cv2.imwrite(str(outlines_dir / f"{img_path.stem}.jpg"), outlines_vis)

        if split == "table":
            out_path = output_dir / f"{img_path.stem}.jpg"
            cv2.imwrite(str(out_path), img[y1:y2, x1:x2])

        if split == "row":
            # Horizontal grid lines; the bottom bound is added if it lies clearly below the last line
            row_bounds = list(valid_row_centers)
            if y2 > row_bounds[-1] + row_height // 2:
                row_bounds.append(y2)
            out_dir = output_dir / "rows"
            for first, last, ys, ye in TableExtractor.group_cell_spans(
                row_bounds, n, y1, y2
            ):
                out_path = out_dir / TableExtractor.span_name(
                    img_path.stem, "row", first, last
                )
                out_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(out_path), img[ys:ye, x1:x2])

        if split == "col":
            col_bounds = list(valid_col_centers)
            out_dir = output_dir / "cols"
            for first, last, xs, xe in TableExtractor.group_cell_spans(
                col_bounds, n, x1, x2
            ):
                out_path = out_dir / TableExtractor.span_name(
                    img_path.stem, "col", first, last
                )
                out_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(out_path), img[y1:y2, xs:xe])

    def execute(self, task: PipelineTask):
        input_dir = task.last_step_output_directory
        output_dir = task.output_directory / "table"
        task.last_step_output_directory = output_dir

        for image in sorted(p for p in input_dir.iterdir() if p.is_file()):
            TableExtractor.extract_printed_table(image, output_dir)
