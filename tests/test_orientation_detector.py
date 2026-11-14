"""Unit and integration tests for TableOrientationDetector (OP11).

Tests cover:
1. Normal upright scan -> detected as upright, raw image and geometry unchanged.
2. 90° CW rotated scan -> detected as 90° CW, raw rotated 90° CCW, restored to upright (~50 rows, 11 cols).
3. 270° CW rotated scan -> detected as 270° CW, raw rotated 90° CW, restored to upright (~50 rows, 11 cols).
4. 180° upside-down scan -> detected as 180°, raw rotated 180°, restored to upright (~50 rows, 11 cols).
5. Edge cases: non-table/empty bounds, missing file, corrupted JSON.
6. Full pipeline run: build_pipeline().run(task).
7. Grid visualization flag (draw_rows=True).
8. Real rotated PDF integration test (scan_new_rotated.pdf).
9. Idempotency on repeated execution.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2

from backend.pipeline import build_pipeline
from backend.pipeline.models.pipeline_parts import DocResult, PipelineTask
from backend.pipeline.steps import ImgExtractor, LlmExtractor, TableExtractor, TableOrientationDetector

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestTableOrientationDetector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Prepare base raw test image once for the test suite."""
        cls.test_dir = tempfile.TemporaryDirectory()
        cls.base_path = Path(cls.test_dir.name)

        cached_raw = PROJECT_ROOT / "benchmark" / "cache" / "scan_1972_sample" / "raw" / "p001.jpg"
        if cached_raw.exists():
            cls.raw_img = cv2.imread(str(cached_raw))
        else:
            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            init_task = PipelineTask(input_pdf=pdf_path, output_directory=cls.base_path / "init", pages=[1])
            ImgExtractor().execute(init_task)
            cls.raw_img = cv2.imread(str(cls.base_path / "init" / "raw" / "p001.jpg"))

        assert cls.raw_img is not None, "Failed to load sample image for test suite"

    @classmethod
    def tearDownClass(cls):
        cls.test_dir.cleanup()

    def _setup_task(self, tmp_path: Path, rotation_code: int | None = None) -> PipelineTask:
        """Helper to create a task with optional pre-rotation and initial table extraction."""
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_file = raw_dir / "p001.jpg"

        img = cv2.rotate(self.raw_img, rotation_code) if rotation_code is not None else self.raw_img.copy()

        cv2.imwrite(str(raw_file), img)

        pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
        task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

        # Step 1: Run TableExtractor to create initial cropped/ geometry
        TableExtractor().execute(task)
        return task

    def test_upright_page_unchanged(self):
        """Normal upright page: detected as upright, no rotation, outputs unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task = self._setup_task(tmp_path, rotation_code=None)

            raw_path = task.step_dir("raw") / "p001.jpg"
            geom_path = task.step_dir("cropped") / "p001.geom.json"

            initial_raw_bytes = raw_path.read_bytes()
            initial_geom = json.loads(geom_path.read_text(encoding="utf-8"))

            # Upright table pre-conditions
            self.assertEqual(len(initial_geom["row_bounds"]), 50)
            self.assertEqual(len(initial_geom["col_bounds"]), 12)  # 11 columns
            self.assertGreater(initial_geom["height"], initial_geom["width"])

            # Execute orientation detector
            detector = TableOrientationDetector()
            detector.execute(task)

            # Assert outputs are bit-for-bit unchanged
            self.assertEqual(raw_path.read_bytes(), initial_raw_bytes)
            final_geom = json.loads(geom_path.read_text(encoding="utf-8"))
            self.assertEqual(final_geom, initial_geom)

    def test_90_clockwise_rotated_page_restored(self):
        """90° CW rotated page: detected as sideways 90° CW, rotated 90° CCW, restored to upright."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task = self._setup_task(tmp_path, rotation_code=cv2.ROTATE_90_CLOCKWISE)

            geom_path = task.step_dir("cropped") / "p001.geom.json"
            raw_path = task.step_dir("raw") / "p001.jpg"

            # Pre-condition: sideways table
            pre_geom = json.loads(geom_path.read_text(encoding="utf-8"))
            self.assertGreater(pre_geom["width"], pre_geom["height"])
            self.assertGreater(len(pre_geom["col_bounds"]) - 1, len(pre_geom["row_bounds"]))

            # Execute orientation detector
            detector = TableOrientationDetector()
            detector.execute(task)

            # Assert raw image is restored to portrait
            restored_raw = cv2.imread(str(raw_path))
            h_raw, w_raw = restored_raw.shape[:2]
            self.assertGreater(h_raw, w_raw, "Raw image should be restored to portrait")

            # Assert geometry restored to upright bird ringing form (50 rows, 11 cols)
            post_geom = json.loads(geom_path.read_text(encoding="utf-8"))
            self.assertGreater(post_geom["height"], post_geom["width"], "Table crop should be portrait")
            self.assertEqual(len(post_geom["row_bounds"]), 50, "Should have 50 data rows")
            self.assertEqual(len(post_geom["col_bounds"]), 12, "Should have 12 bounds (11 cols)")
            self.assertEqual(post_geom["width"], 1507)
            self.assertEqual(post_geom["height"], 2178)

    def test_270_clockwise_rotated_page_restored(self):
        """270° CW rotated page: detected as sideways 270° CW, rotated 90° CW, restored to upright."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task = self._setup_task(tmp_path, rotation_code=cv2.ROTATE_90_COUNTERCLOCKWISE)

            geom_path = task.step_dir("cropped") / "p001.geom.json"
            raw_path = task.step_dir("raw") / "p001.jpg"

            # Pre-condition: sideways table
            pre_geom = json.loads(geom_path.read_text(encoding="utf-8"))
            self.assertGreater(pre_geom["width"], pre_geom["height"])
            self.assertGreater(len(pre_geom["col_bounds"]) - 1, len(pre_geom["row_bounds"]))

            # Execute orientation detector
            detector = TableOrientationDetector()
            detector.execute(task)

            # Assert raw image is restored to portrait
            restored_raw = cv2.imread(str(raw_path))
            h_raw, w_raw = restored_raw.shape[:2]
            self.assertGreater(h_raw, w_raw, "Raw image should be restored to portrait")

            # Assert geometry restored to upright bird ringing form (50 rows, 11 cols)
            post_geom = json.loads(geom_path.read_text(encoding="utf-8"))
            self.assertGreater(post_geom["height"], post_geom["width"], "Table crop should be portrait")
            self.assertEqual(len(post_geom["row_bounds"]), 50, "Should have 50 data rows")
            self.assertEqual(len(post_geom["col_bounds"]), 12, "Should have 12 bounds (11 cols)")
            self.assertEqual(post_geom["width"], 1507)
            self.assertEqual(post_geom["height"], 2178)

    def test_180_upside_down_rotated_page_restored(self):
        """180° upside-down rotated page: detected as 180°, rotated 180°, restored to upright."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task = self._setup_task(tmp_path, rotation_code=cv2.ROTATE_180)

            geom_path = task.step_dir("cropped") / "p001.geom.json"
            raw_path = task.step_dir("raw") / "p001.jpg"

            # Pre-condition: upside-down table (portrait aspect, but wide species column on right)
            pre_geom = json.loads(geom_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(pre_geom["height"], pre_geom["width"])
            self.assertEqual(len(pre_geom["row_bounds"]), 50)
            self.assertEqual(len(pre_geom["col_bounds"]), 12)
            pre_col_widths = [
                pre_geom["col_bounds"][i + 1] - pre_geom["col_bounds"][i]
                for i in range(len(pre_geom["col_bounds"]) - 1)
            ]
            # In 180° rotated form, wide species column (~365 px) is on the right side
            pre_max_col_idx = pre_col_widths.index(max(pre_col_widths))
            self.assertGreaterEqual(pre_max_col_idx, len(pre_col_widths) / 2)

            # Execute orientation detector
            detector = TableOrientationDetector()
            detector.execute(task)

            # Assert raw image is restored to upright
            restored_raw = cv2.imread(str(raw_path))
            h_raw, w_raw = restored_raw.shape[:2]
            self.assertGreater(h_raw, w_raw, "Raw image should be portrait")

            # Assert geometry restored upright: 50 rows, 11 cols, wide species column on the left
            post_geom = json.loads(geom_path.read_text(encoding="utf-8"))
            self.assertGreater(post_geom["height"], post_geom["width"], "Table crop should be portrait")
            self.assertEqual(len(post_geom["row_bounds"]), 50, "Should have 50 data rows")
            self.assertEqual(len(post_geom["col_bounds"]), 12, "Should have 12 bounds (11 cols)")
            self.assertEqual(post_geom["width"], 1507)
            self.assertEqual(post_geom["height"], 2178)

            post_col_widths = [
                post_geom["col_bounds"][i + 1] - post_geom["col_bounds"][i]
                for i in range(len(post_geom["col_bounds"]) - 1)
            ]
            post_max_col_idx = post_col_widths.index(max(post_col_widths))
            self.assertLess(post_max_col_idx, len(post_col_widths) / 2, "Wide species column should be on the left")
            self.assertEqual(post_max_col_idx, 1, "Species column is index 1")
            self.assertEqual(post_col_widths[1], 365, "Species column width should be 365 px")

    def test_idempotency_on_repeated_runs(self):
        """Running orientation detector multiple times does not over-rotate an already upright page."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task = self._setup_task(tmp_path, rotation_code=cv2.ROTATE_90_CLOCKWISE)

            detector = TableOrientationDetector()
            # First run: detects 90° CW and restores upright
            detector.execute(task)

            raw_path = task.step_dir("raw") / "p001.jpg"
            geom_path = task.step_dir("cropped") / "p001.geom.json"
            first_raw_bytes = raw_path.read_bytes()
            first_geom = json.loads(geom_path.read_text(encoding="utf-8"))

            # Second run: should be a complete no-op
            detector.execute(task)
            self.assertEqual(raw_path.read_bytes(), first_raw_bytes)
            second_geom = json.loads(geom_path.read_text(encoding="utf-8"))
            self.assertEqual(first_geom, second_geom)

    def test_180_idempotency_on_repeated_runs(self):
        """Repeated runs on 180° rotated page restore once and do not re-flip."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task = self._setup_task(tmp_path, rotation_code=cv2.ROTATE_180)

            detector = TableOrientationDetector()
            # First run: detects 180° and restores upright
            detector.execute(task)

            raw_path = task.step_dir("raw") / "p001.jpg"
            geom_path = task.step_dir("cropped") / "p001.geom.json"
            first_raw_bytes = raw_path.read_bytes()
            first_geom = json.loads(geom_path.read_text(encoding="utf-8"))

            # Second run: must be a no-op
            detector.execute(task)
            self.assertEqual(raw_path.read_bytes(), first_raw_bytes)
            second_geom = json.loads(geom_path.read_text(encoding="utf-8"))
            self.assertEqual(first_geom, second_geom)

    def test_draw_rows_generates_grid(self):
        """When draw_rows=True, re-extraction must generate grid visualization."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task = self._setup_task(tmp_path, rotation_code=cv2.ROTATE_90_CLOCKWISE)

            detector = TableOrientationDetector(draw_rows=True)
            detector.execute(task)

            grid_img = task.step_dir("cropped") / "grid" / "p001.jpg"
            self.assertTrue(grid_img.exists(), "cropped/grid/p001.jpg should be generated when draw_rows=True")

    def test_robustness_missing_geom_or_raw(self):
        """Missing geometry file or raw file should be gracefully skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

            detector = TableOrientationDetector()
            # No files exist: must finish cleanly without raising exceptions
            detector.execute(task)

    def test_robustness_corrupted_json(self):
        """Corrupted JSON in geom file should be handled gracefully without crashing."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cropped_dir = tmp_path / "cropped"
            cropped_dir.mkdir(parents=True)
            (cropped_dir / "p001.geom.json").write_text("NOT_VALID_JSON{", encoding="utf-8")

            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)
            cv2.imwrite(str(raw_dir / "p001.jpg"), self.raw_img)

            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

            detector = TableOrientationDetector()
            detector.execute(task)

    def test_robustness_empty_bounds(self):
        """Empty or degenerate bounds should be safely skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cropped_dir = tmp_path / "cropped"
            cropped_dir.mkdir(parents=True)
            empty_geom = {
                "width": 2000,
                "height": 1000,
                "skew_angle": 0.0,
                "header": [0, 10],
                "row_bounds": [],
                "col_bounds": [],
            }
            (cropped_dir / "p001.geom.json").write_text(json.dumps(empty_geom), encoding="utf-8")

            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)
            cv2.imwrite(str(raw_dir / "p001.jpg"), self.raw_img)

            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

            detector = TableOrientationDetector()
            detector.execute(task)

    def test_full_pipeline_run(self):
        """Full pipeline run: build_pipeline().run(task) with mocked LLM step."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

            # Pre-seed raw with 90° CW rotated image to test detector in full pipeline
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)
            cv2.imwrite(str(raw_dir / "p001.jpg"), cv2.rotate(self.raw_img, cv2.ROTATE_90_CLOCKWISE))

            pipeline = build_pipeline()

            # Verify TableOrientationDetector is in pipeline right after TableExtractor
            step_types = [type(s) for s in pipeline.steps]
            self.assertIn(TableOrientationDetector, step_types)
            table_extractor_idx = step_types.index(TableExtractor)
            orientation_detector_idx = step_types.index(TableOrientationDetector)
            self.assertEqual(orientation_detector_idx, table_extractor_idx + 1)

            # Mock LlmExtractor.execute so no API key or network is needed
            with patch.object(LlmExtractor, "execute", return_value=None):
                result = pipeline.run(task)

            # Verify geometry restored upright in cropped/
            geom_path = task.step_dir("cropped") / "p001.geom.json"
            geom = json.loads(geom_path.read_text(encoding="utf-8"))
            self.assertEqual(len(geom["row_bounds"]), 50)
            self.assertEqual(len(geom["col_bounds"]), 12)
            self.assertEqual(geom["width"], 1507)
            self.assertEqual(geom["height"], 2178)
            self.assertIsInstance(result, DocResult)

    def test_pipeline_run_cropped_only(self):
        """Selective pipeline run with only=['cropped'] executes orientation detection."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

            # Seed raw with 270° CW rotated image
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)
            cv2.imwrite(str(raw_dir / "p001.jpg"), cv2.rotate(self.raw_img, cv2.ROTATE_90_COUNTERCLOCKWISE))

            pipeline = build_pipeline()
            pipeline.run(task, only=["cropped"])

            geom_path = task.step_dir("cropped") / "p001.geom.json"
            geom = json.loads(geom_path.read_text(encoding="utf-8"))
            self.assertEqual(len(geom["row_bounds"]), 50)
            self.assertEqual(len(geom["col_bounds"]), 12)
            self.assertGreater(geom["height"], geom["width"])

    def test_real_rotated_pdf_integration(self):
        """End-to-end integration test with real data/input/scan_new_rotated.pdf."""
        rotated_pdf = PROJECT_ROOT / "data" / "input" / "scan_new_rotated.pdf"
        if not rotated_pdf.exists():
            self.skipTest("scan_new_rotated.pdf not available")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task = PipelineTask(input_pdf=rotated_pdf, output_directory=tmp_path, pages=[1])

            pipeline = build_pipeline()
            pipeline.run(task, only=["raw", "cropped"])

            geom_path = task.step_dir("cropped") / "p001.geom.json"
            self.assertTrue(geom_path.exists())
            geom = json.loads(geom_path.read_text(encoding="utf-8"))

            # Real document restored upright: height > width, rows > cols
            self.assertGreater(geom["height"], geom["width"])
            self.assertGreater(len(geom["row_bounds"]), len(geom["col_bounds"]) - 1)
            self.assertEqual(len(geom["row_bounds"]), 74)
            self.assertEqual(len(geom["col_bounds"]) - 1, 27)


if __name__ == "__main__":
    unittest.main()
