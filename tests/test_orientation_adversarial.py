"""Adversarial stress tests for TableOrientationDetector (Milestone M1 empirical challenge).

This suite stresses corner cases, failure modes, idempotency, benchmark invariance,
and multi-page documents with mixed orientations.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2

from backend.pipeline.models.pipeline_parts import PipelineTask
from backend.pipeline.models.results import PageGeometry
from backend.pipeline.steps import ImgExtractor, TableExtractor, TableOrientationDetector

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestOrientationDetectorAdversarial(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Prepare base raw test image once for the suite."""
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

        assert cls.raw_img is not None, "Failed to load sample raw image"

    @classmethod
    def tearDownClass(cls):
        cls.test_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Corner Case Stress Testing
    # -------------------------------------------------------------------------

    def test_missing_geom_file_does_not_crash(self):
        """When cropped/p001.geom.json is missing, execute() must log and skip without crashing."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)
            raw_file = raw_dir / "p001.jpg"
            cv2.imwrite(str(raw_file), self.raw_img)

            # cropped dir exists but has no geom.json
            cropped_dir = tmp_path / "cropped"
            cropped_dir.mkdir(parents=True)

            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

            detector = TableOrientationDetector()
            with self.assertLogs("backend.pipeline.steps.table_orientation_detector", level="DEBUG") as log_capture:
                detector.execute(task)

            # Verify it skipped safely
            self.assertTrue(
                any("missing geom or raw file" in msg for msg in log_capture.output),
                f"Expected skip log message, got: {log_capture.output}",
            )
            # Verify raw image was not modified
            restored = cv2.imread(str(raw_file))
            self.assertEqual(restored.shape, self.raw_img.shape)

    def test_empty_row_bounds_does_not_crash(self):
        """When geom.json has row_bounds = [], execute() must skip without crashing."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)
            raw_file = raw_dir / "p001.jpg"
            cv2.imwrite(str(raw_file), self.raw_img)

            cropped_dir = tmp_path / "cropped"
            cropped_dir.mkdir(parents=True)
            cv2.imwrite(str(cropped_dir / "p001.jpg"), self.raw_img)

            geom = PageGeometry(
                width=2000,
                height=1000,
                skew_angle=0.0,
                header=(0, 20),
                row_bounds=[],  # empty
                col_bounds=[0, 100, 200, 300],
            )
            (cropped_dir / "p001.geom.json").write_text(geom.model_dump_json(), encoding="utf-8")

            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

            detector = TableOrientationDetector()
            with self.assertLogs("backend.pipeline.steps.table_orientation_detector", level="DEBUG") as log_capture:
                detector.execute(task)

            self.assertTrue(
                any("empty row or column bounds" in msg for msg in log_capture.output),
                f"Expected empty bounds log, got: {log_capture.output}",
            )

    def test_empty_col_bounds_does_not_crash(self):
        """When geom.json has col_bounds = [], execute() must skip without crashing."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)
            raw_file = raw_dir / "p001.jpg"
            cv2.imwrite(str(raw_file), self.raw_img)

            cropped_dir = tmp_path / "cropped"
            cropped_dir.mkdir(parents=True)
            cv2.imwrite(str(cropped_dir / "p001.jpg"), self.raw_img)

            geom = PageGeometry(
                width=2000,
                height=1000,
                skew_angle=0.0,
                header=(0, 20),
                row_bounds=[(20, 50), (50, 80)],
                col_bounds=[],  # empty
            )
            (cropped_dir / "p001.geom.json").write_text(geom.model_dump_json(), encoding="utf-8")

            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

            detector = TableOrientationDetector()
            with self.assertLogs("backend.pipeline.steps.table_orientation_detector", level="DEBUG") as log_capture:
                detector.execute(task)

            self.assertTrue(
                any("empty row or column bounds" in msg for msg in log_capture.output),
                f"Expected empty bounds log, got: {log_capture.output}",
            )

    def test_single_row_and_single_cell_behavior(self):
        """Test behavior when table has only 1 row or a single cell."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)
            raw_file = raw_dir / "p001.jpg"
            cv2.imwrite(str(raw_file), self.raw_img)

            cropped_dir = tmp_path / "cropped"
            cropped_dir.mkdir(parents=True)
            cv2.imwrite(str(cropped_dir / "p001.jpg"), self.raw_img)

            # Case A: 1 row, 1 cell: col_bounds has 2 entries -> num_cols = 1, num_rows = 1
            # num_cols > num_rows is False (1 > 1 is False). Should be skipped as upright.
            geom_single_cell = PageGeometry(
                width=500,
                height=1000,
                skew_angle=0.0,
                header=(0, 20),
                row_bounds=[(20, 100)],
                col_bounds=[0, 500],
            )
            (cropped_dir / "p001.geom.json").write_text(geom_single_cell.model_dump_json(), encoding="utf-8")

            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

            detector = TableOrientationDetector()
            with self.assertLogs("backend.pipeline.steps.table_orientation_detector", level="DEBUG") as log_capture:
                detector.execute(task)

            self.assertTrue(
                any("Page p001 is upright" in msg for msg in log_capture.output),
                f"Expected upright detection, got: {log_capture.output}",
            )

    def test_missing_or_unreadable_raw_image(self):
        """When raw image is missing or corrupted/unreadable (0 bytes), execute() skips safely."""
        # Case A: raw image missing completely
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cropped_dir = tmp_path / "cropped"
            cropped_dir.mkdir(parents=True)
            cv2.imwrite(str(cropped_dir / "p001.jpg"), self.raw_img)

            geom = PageGeometry(
                width=2000,
                height=1000,
                skew_angle=0.0,
                header=(0, 20),
                row_bounds=[(20, 50)],
                col_bounds=[0, 100, 200],
            )
            (cropped_dir / "p001.geom.json").write_text(geom.model_dump_json(), encoding="utf-8")

            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

            detector = TableOrientationDetector()
            with self.assertLogs("backend.pipeline.steps.table_orientation_detector", level="DEBUG") as log_capture:
                detector.execute(task)

            self.assertTrue(any("missing geom or raw file" in msg for msg in log_capture.output))

        # Case B: raw image exists but is 0 bytes (corrupted / unreadable)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)
            (raw_dir / "p001.jpg").write_bytes(b"")  # 0 bytes

            cropped_dir = tmp_path / "cropped"
            cropped_dir.mkdir(parents=True)
            cv2.imwrite(str(cropped_dir / "p001.jpg"), self.raw_img)

            # Create sideways geom so detector attempts to read raw image
            geom = PageGeometry(
                width=2000,
                height=1000,
                skew_angle=0.0,
                header=(0, 20),
                row_bounds=[(20, 50)],
                col_bounds=[0, 100, 200, 300],  # 3 cols > 1 row
            )
            (cropped_dir / "p001.geom.json").write_text(geom.model_dump_json(), encoding="utf-8")

            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

            detector = TableOrientationDetector()
            with self.assertLogs("backend.pipeline.steps.table_orientation_detector", level="WARNING") as log_capture:
                detector.execute(task)

            self.assertTrue(
                any("Could not read raw image" in msg for msg in log_capture.output),
                f"Expected unreadable warning, got: {log_capture.output}",
            )

    def test_idempotency_consecutive_runs(self):
        """Executing TableOrientationDetector twice consecutively must NOT double-rotate or alter the upright page."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)
            raw_path = raw_dir / "p001.jpg"

            # Start with 90° CW sideways image
            rotated_90_cw = cv2.rotate(self.raw_img, cv2.ROTATE_90_CLOCKWISE)
            cv2.imwrite(str(raw_path), rotated_90_cw)

            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

            # Initial table extraction
            TableExtractor().execute(task)

            detector = TableOrientationDetector()

            # First run: detects sideways and rotates upright
            detector.execute(task)

            # Record post-run-1 state
            raw_bytes_run1 = raw_path.read_bytes()
            geom_run1 = json.loads((task.step_dir("cropped") / "p001.geom.json").read_text(encoding="utf-8"))
            cropped_bytes_run1 = (task.step_dir("cropped") / "p001.jpg").read_bytes()

            self.assertGreater(geom_run1["height"], geom_run1["width"])
            self.assertEqual(len(geom_run1["row_bounds"]), 50)
            self.assertEqual(len(geom_run1["col_bounds"]) - 1, 11)

            # Second consecutive run
            detector.execute(task)

            # Record post-run-2 state
            raw_bytes_run2 = raw_path.read_bytes()
            geom_run2 = json.loads((task.step_dir("cropped") / "p001.geom.json").read_text(encoding="utf-8"))
            cropped_bytes_run2 = (task.step_dir("cropped") / "p001.jpg").read_bytes()

            # Verify complete idempotency: exactly identical bytes and geometry
            self.assertEqual(raw_bytes_run1, raw_bytes_run2, "Raw image was modified on second run!")
            self.assertEqual(cropped_bytes_run1, cropped_bytes_run2, "Cropped image was modified on second run!")
            self.assertEqual(geom_run1, geom_run2, "Geometry was modified on second run!")

    # -------------------------------------------------------------------------
    # 2. Benchmark Invariance Check
    # -------------------------------------------------------------------------

    def test_benchmark_file_sha256_invariance(self):
        """benchmark/run_benchmark.py must have the exact unaltered SHA256 checksum."""
        bench_file = PROJECT_ROOT / "benchmark" / "run_benchmark.py"
        self.assertTrue(bench_file.exists())

        hasher = hashlib.sha256()
        hasher.update(bench_file.read_bytes())
        file_sha256 = hasher.hexdigest()

        expected_sha256 = "56fa702815cb4cbd3a82309f3453896078cfd3cc3a1edf0d979168c8d2f3d09e"
        self.assertEqual(
            file_sha256,
            expected_sha256,
            f"benchmark/run_benchmark.py was modified! Expected {expected_sha256}, got {file_sha256}",
        )

    def test_benchmark_does_not_trigger_table_orientation_detector(self):
        """Running prepare_crops in benchmark must NOT invoke TableOrientationDetector."""
        from benchmark.run_benchmark import prepare_crops

        with tempfile.TemporaryDirectory() as tmp:
            shared_root = Path(tmp)
            refs = ["scan_1972_sample_p001"]

            # Spy on TableOrientationDetector.execute
            with patch.object(
                TableOrientationDetector, "execute", side_effect=RuntimeError("TableOrientationDetector was invoked!")
            ) as mock_detector:
                # Run prepare_crops
                prepare_crops(refs, shared_root)

                # Verify detector was never called
                mock_detector.assert_not_called()

            # Verify that crops were prepared by ImgExtractor + TableExtractor
            cropped_file = shared_root / "scan_1972_sample" / "cropped" / "p001.jpg"
            geom_file = shared_root / "scan_1972_sample" / "cropped" / "p001.geom.json"
            self.assertTrue(cropped_file.exists())
            self.assertTrue(geom_file.exists())

    # -------------------------------------------------------------------------
    # 3. Multi-Page Document Challenge
    # -------------------------------------------------------------------------

    def test_multipage_mixed_orientation_selective_flip(self):
        """On a multi-page document where page 1 is upright and page 2 is sideways,
        only page 2 must be rotated; page 1 must remain untouched.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)

            p1_raw = raw_dir / "p001.jpg"
            p2_raw = raw_dir / "p002.jpg"

            # Page 1: Upright portrait
            cv2.imwrite(str(p1_raw), self.raw_img)

            # Page 2: Sideways 90° CW landscape
            rotated_p2 = cv2.rotate(self.raw_img, cv2.ROTATE_90_CLOCKWISE)
            cv2.imwrite(str(p2_raw), rotated_p2)

            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1, 2])

            # Initial table extraction for both pages
            TableExtractor().execute(task)

            # Check pre-conditions
            p1_geom_pre = json.loads((tmp_path / "cropped" / "p001.geom.json").read_text(encoding="utf-8"))
            p2_geom_pre = json.loads((tmp_path / "cropped" / "p002.geom.json").read_text(encoding="utf-8"))
            p1_raw_bytes_pre = p1_raw.read_bytes()
            p2_raw_bytes_pre = p2_raw.read_bytes()

            self.assertGreater(p1_geom_pre["height"], p1_geom_pre["width"], "Page 1 must start portrait")
            self.assertGreater(p2_geom_pre["width"], p2_geom_pre["height"], "Page 2 must start landscape")

            # Execute TableOrientationDetector
            detector = TableOrientationDetector()
            detector.execute(task)

            # Check post-conditions
            p1_geom_post = json.loads((tmp_path / "cropped" / "p001.geom.json").read_text(encoding="utf-8"))
            p2_geom_post = json.loads((tmp_path / "cropped" / "p002.geom.json").read_text(encoding="utf-8"))
            p1_raw_bytes_post = p1_raw.read_bytes()
            p2_raw_bytes_post = p2_raw.read_bytes()

            # Page 1 MUST be completely untouched
            self.assertEqual(p1_raw_bytes_post, p1_raw_bytes_pre, "Page 1 raw image was modified!")
            self.assertEqual(p1_geom_post, p1_geom_pre, "Page 1 geometry was modified!")

            # Page 2 MUST be rotated and restored to upright portrait
            self.assertNotEqual(p2_raw_bytes_post, p2_raw_bytes_pre, "Page 2 raw image was NOT rotated!")
            self.assertGreater(p2_geom_post["height"], p2_geom_post["width"], "Page 2 table crop must now be portrait")
            self.assertEqual(len(p2_geom_post["row_bounds"]), 50, "Page 2 must have 50 rows")
            self.assertEqual(len(p2_geom_post["col_bounds"]) - 1, 11, "Page 2 must have 11 cols")

            # Raw image p002 dimensions must now be portrait
            p2_img_post = cv2.imread(str(p2_raw))
            h2, w2 = p2_img_post.shape[:2]
            self.assertGreater(h2, w2, "Page 2 raw image must be portrait")

    def test_multipage_both_sideways_different_directions(self):
        """Page 1 is 90° CW and Page 2 is 270° CW. Both must be restored upright independently."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)

            p1_raw = raw_dir / "p001.jpg"
            p2_raw = raw_dir / "p002.jpg"

            # Page 1: 90° CW
            cv2.imwrite(str(p1_raw), cv2.rotate(self.raw_img, cv2.ROTATE_90_CLOCKWISE))
            # Page 2: 270° CW (90° CCW)
            cv2.imwrite(str(p2_raw), cv2.rotate(self.raw_img, cv2.ROTATE_90_COUNTERCLOCKWISE))

            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1, 2])

            TableExtractor().execute(task)

            detector = TableOrientationDetector()
            detector.execute(task)

            # Both pages must be restored to portrait with 50 rows and 11 cols
            for stem in ["p001", "p002"]:
                geom = json.loads((tmp_path / "cropped" / f"{stem}.geom.json").read_text(encoding="utf-8"))
                self.assertGreater(geom["height"], geom["width"], f"{stem} must be portrait")
                self.assertEqual(len(geom["row_bounds"]), 50, f"{stem} must have 50 rows")
                self.assertEqual(len(geom["col_bounds"]) - 1, 11, f"{stem} must have 11 cols")

                raw_img = cv2.imread(str(raw_dir / f"{stem}.jpg"))
                h, w = raw_img.shape[:2]
                self.assertGreater(h, w, f"{stem} raw image must be portrait")

    def test_180_upside_down_bottom_header_trigger(self):
        """When columns are symmetric or uniform, a bottom-half header triggers 180° rotation."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)
            raw_path = raw_dir / "p001.jpg"
            cv2.imwrite(str(raw_path), self.raw_img)

            cropped_dir = tmp_path / "cropped"
            cropped_dir.mkdir(parents=True)

            # Uniform columns (each 100px), but header located in bottom half (y=1200 of height=2000)
            geom = PageGeometry(
                width=300,
                height=2000,
                skew_angle=0.0,
                header=(1200, 1250),
                row_bounds=[(100, 200), (200, 300), (300, 400)],
                col_bounds=[0, 100, 200, 300],
            )
            (cropped_dir / "p001.geom.json").write_text(geom.model_dump_json(), encoding="utf-8")

            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1])

            detector = TableOrientationDetector()
            with (
                patch.object(TableExtractor, "extract_printed_table") as mock_extract,
                self.assertLogs("backend.pipeline.steps.table_orientation_detector", level="INFO") as log_capture,
            ):
                detector.execute(task)

            self.assertTrue(
                any("180° upside-down" in msg for msg in log_capture.output),
                f"Expected 180° upside-down log, got: {log_capture.output}",
            )
            mock_extract.assert_called_once()

    def test_multipage_three_pages_mixed_90_180_270(self):
        """Document with 3 pages in 90° CW, 180°, and 270° CW orientations are all restored upright."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True)

            p1_raw = raw_dir / "p001.jpg"
            p2_raw = raw_dir / "p002.jpg"
            p3_raw = raw_dir / "p003.jpg"

            # Page 1: 90° CW
            cv2.imwrite(str(p1_raw), cv2.rotate(self.raw_img, cv2.ROTATE_90_CLOCKWISE))
            # Page 2: 180° upside-down
            cv2.imwrite(str(p2_raw), cv2.rotate(self.raw_img, cv2.ROTATE_180))
            # Page 3: 270° CW (90° CCW)
            cv2.imwrite(str(p3_raw), cv2.rotate(self.raw_img, cv2.ROTATE_90_COUNTERCLOCKWISE))

            pdf_path = PROJECT_ROOT / "data" / "dataset" / "pdfs" / "scan_1972_sample.pdf"
            task = PipelineTask(input_pdf=pdf_path, output_directory=tmp_path, pages=[1, 2, 3])

            TableExtractor().execute(task)

            detector = TableOrientationDetector()
            detector.execute(task)

            for stem in ["p001", "p002", "p003"]:
                geom = json.loads((tmp_path / "cropped" / f"{stem}.geom.json").read_text(encoding="utf-8"))
                self.assertGreater(geom["height"], geom["width"], f"{stem} must be portrait")
                self.assertEqual(len(geom["row_bounds"]), 50, f"{stem} must have 50 rows")
                self.assertEqual(len(geom["col_bounds"]) - 1, 11, f"{stem} must have 11 cols")

                # Wide species column must be on the left (index 1, width 365)
                col_widths = [geom["col_bounds"][i + 1] - geom["col_bounds"][i] for i in range(11)]
                self.assertEqual(col_widths.index(max(col_widths)), 1, f"{stem} wide column must be index 1")
                self.assertEqual(col_widths[1], 365, f"{stem} species column must be 365 px")

                raw_img = cv2.imread(str(raw_dir / f"{stem}.jpg"))
                h, w = raw_img.shape[:2]
                self.assertGreater(h, w, f"{stem} raw image must be portrait")


if __name__ == "__main__":
    unittest.main()
