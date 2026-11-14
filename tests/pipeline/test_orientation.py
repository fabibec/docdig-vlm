"""TableOrientationDetector: sideways (90°/270°) and upside-down (180°) pages are rotated upright on disk.

Most tests use a synthetic form drawn with OpenCV (no poppler needed). The ``real scan`` tests render the
1972 sample form and the rotated PDFs in ``data/input`` and need ``pdftoppm``.
"""

import json
import logging
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from backend.pipeline import build_pipeline
from backend.pipeline.models.pipeline_parts import DocResult, PipelineTask
from backend.pipeline.models.results import PageGeometry
from backend.pipeline.steps import LlmExtractor, TableExtractor, TableOrientationDetector
from tests.pipeline.helpers import INPUT_DIR, col_widths, draw_form, requires_pdftoppm, write_pdf

LOGGER = "backend.pipeline.steps.table_orientation_detector"

ROTATIONS = {
    "90cw": cv2.ROTATE_90_CLOCKWISE,
    "180": cv2.ROTATE_180,
    "270cw": cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def seed_raw(task: PipelineTask, pages: dict[int, np.ndarray]) -> None:
    raw = task.step_dir("raw")
    raw.mkdir(parents=True, exist_ok=True)
    for num, img in pages.items():
        cv2.imwrite(str(raw / f"p{num:03d}.jpg"), img)


def read_geom(task: PipelineTask, num: int = 1) -> dict:
    return json.loads((task.step_dir("cropped") / f"p{num:03d}.geom.json").read_text(encoding="utf-8"))


def write_geom(task: PipelineTask, geom: PageGeometry, num: int = 1) -> None:
    cropped = task.step_dir("cropped")
    cropped.mkdir(parents=True, exist_ok=True)
    (cropped / f"p{num:03d}.geom.json").write_text(geom.model_dump_json(), encoding="utf-8")


def rotated(img: np.ndarray, name: str | None) -> np.ndarray:
    return img.copy() if name is None else cv2.rotate(img, ROTATIONS[name])


def assert_same_table(geom: dict, expected: dict, tol: int = 2) -> None:
    """Same table shape: size, row count and column widths (within ``tol`` px)."""
    assert abs(geom["width"] - expected["width"]) <= tol
    assert abs(geom["height"] - expected["height"]) <= tol
    assert len(geom["row_bounds"]) == len(expected["row_bounds"])
    assert len(geom["col_bounds"]) == len(expected["col_bounds"])
    assert all(abs(a - b) <= tol for a, b in zip(col_widths(geom), col_widths(expected), strict=True))


@pytest.fixture(scope="module")
def form() -> np.ndarray:
    img, _, _ = draw_form(rows=20)
    return img


@pytest.fixture
def upright_geom(form, make_task) -> dict:
    """Geometry the TableExtractor produces for the upright synthetic form."""
    task = make_task(name="reference")
    seed_raw(task, {1: form})
    TableExtractor().execute(task)
    return read_geom(task)


# ---------------------------------------------------------------- synthetic form


def test_synthetic_form_is_portrait_with_wide_second_column(upright_geom):
    assert upright_geom["height"] > upright_geom["width"]
    assert len(upright_geom["row_bounds"]) == 20
    assert len(upright_geom["col_bounds"]) == 12
    widths = col_widths(upright_geom)
    assert widths.index(max(widths)) == 1


def test_upright_page_is_left_untouched(form, make_task, caplog):
    task = make_task()
    seed_raw(task, {1: form})
    TableExtractor().execute(task)
    raw = task.step_dir("raw") / "p001.jpg"
    crop = task.step_dir("cropped") / "p001.jpg"
    before = raw.read_bytes(), crop.read_bytes(), read_geom(task)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        TableOrientationDetector().execute(task)

    assert (raw.read_bytes(), crop.read_bytes(), read_geom(task)) == before
    assert any("p001 is upright" in m for m in caplog.messages)


@pytest.mark.parametrize("rotation", ROTATIONS)
def test_rotated_page_is_restored_upright(rotation, form, upright_geom, make_task, caplog):
    task = make_task()
    seed_raw(task, {1: rotated(form, rotation)})
    TableExtractor().execute(task)
    pre = read_geom(task)
    if rotation == "180":
        widths = col_widths(pre)
        assert widths.index(max(widths)) >= len(widths) / 2, "upside down: wide column on the right"
    else:
        assert pre["width"] > pre["height"], "sideways: landscape crop"
        assert len(pre["col_bounds"]) - 1 > len(pre["row_bounds"])

    with caplog.at_level(logging.INFO, logger=LOGGER):
        TableOrientationDetector().execute(task)

    assert any("Detected rotated table on page 1" in m for m in caplog.messages)
    h, w = cv2.imread(str(task.step_dir("raw") / "p001.jpg")).shape[:2]
    assert h > w, "raw page is rewritten upright"
    crop_h, crop_w = cv2.imread(str(task.step_dir("cropped") / "p001.jpg")).shape[:2]
    post = read_geom(task)
    assert (crop_w, crop_h) == (post["width"], post["height"]), "crop is regenerated with the geometry"
    assert_same_table(post, upright_geom)


@pytest.mark.parametrize("rotation", ["90cw", "180", "270cw"])
def test_second_run_is_a_no_op(rotation, form, make_task):
    task = make_task()
    seed_raw(task, {1: rotated(form, rotation)})
    TableExtractor().execute(task)
    detector = TableOrientationDetector()
    detector.execute(task)
    raw = task.step_dir("raw") / "p001.jpg"
    crop = task.step_dir("cropped") / "p001.jpg"
    first = raw.read_bytes(), crop.read_bytes(), read_geom(task)

    detector.execute(task)

    assert (raw.read_bytes(), crop.read_bytes(), read_geom(task)) == first


def test_draw_rows_writes_grid_image_on_re_extraction(form, make_task):
    task = make_task()
    seed_raw(task, {1: rotated(form, "90cw")})
    TableExtractor().execute(task)
    assert not (task.step_dir("cropped") / "grid").exists()

    TableOrientationDetector(draw_rows=True).execute(task)

    assert (task.step_dir("cropped") / "grid" / "p001.jpg").is_file()


def test_multipage_mixed_orientations_only_rotated_pages_change(form, upright_geom, make_task):
    task = make_task(pages=[1, 2, 3, 4])
    seed_raw(
        task,
        {1: form, 2: rotated(form, "90cw"), 3: rotated(form, "180"), 4: rotated(form, "270cw")},
    )
    TableExtractor().execute(task)
    p1_raw = task.step_dir("raw") / "p001.jpg"
    p1_before = p1_raw.read_bytes(), read_geom(task, 1)
    rotated_before = {n: (task.step_dir("raw") / f"p{n:03d}.jpg").read_bytes() for n in (2, 3, 4)}

    TableOrientationDetector().execute(task)

    assert (p1_raw.read_bytes(), read_geom(task, 1)) == p1_before
    for num in (2, 3, 4):
        assert (task.step_dir("raw") / f"p{num:03d}.jpg").read_bytes() != rotated_before[num]
        assert_same_table(read_geom(task, num), upright_geom)


def test_page_filter_limits_which_pages_are_rotated(form, make_task):
    task = make_task(pages=[1])
    seed_raw(task, {1: form, 2: rotated(form, "90cw")})
    TableExtractor().execute(PipelineTask(input_pdf=task.input_pdf, output_directory=task.output_directory))
    p2_raw = task.step_dir("raw") / "p002.jpg"
    p2_before = p2_raw.read_bytes()

    TableOrientationDetector().execute(task)

    assert p2_raw.read_bytes() == p2_before
    assert read_geom(task, 2)["width"] > read_geom(task, 2)["height"]


def test_pipeline_run_only_cropped_includes_orientation_fix(form, upright_geom, make_task):
    pipeline = build_pipeline()
    names = [type(s) for s in pipeline.steps]
    assert names.index(TableOrientationDetector) == names.index(TableExtractor) + 1

    task = make_task()
    seed_raw(task, {1: rotated(form, "270cw")})
    result = pipeline.run(task, only=["cropped"])

    assert isinstance(result, DocResult)
    assert result.pages == []  # nothing processed yet
    assert_same_table(read_geom(task), upright_geom)


# ---------------------------------------------------------------- robustness


def test_no_artifacts_at_all_is_a_no_op(make_task):
    task = make_task(pages=[1])
    TableOrientationDetector().execute(task)
    assert not task.output_directory.exists()


def test_missing_geometry_is_skipped(form, make_task, caplog):
    task = make_task()
    seed_raw(task, {1: form})
    task.step_dir("cropped").mkdir()
    raw = task.step_dir("raw") / "p001.jpg"
    before = raw.read_bytes()

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        TableOrientationDetector().execute(task)

    assert any("missing geom or raw file" in m for m in caplog.messages)
    assert raw.read_bytes() == before


def test_missing_raw_image_is_skipped(form, make_task, caplog):
    task = make_task()
    write_geom(task, PageGeometry(width=2000, height=1000, header=(0, 20), row_bounds=[(20, 50)], col_bounds=[0, 1, 2]))
    cv2.imwrite(str(task.step_dir("cropped") / "p001.jpg"), form)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        TableOrientationDetector().execute(task)

    assert any("missing geom or raw file" in m for m in caplog.messages)
    assert not task.step_dir("raw").exists()


def test_corrupt_geometry_json_is_skipped_with_warning(form, make_task, caplog):
    task = make_task()
    seed_raw(task, {1: form})
    task.step_dir("cropped").mkdir()
    (task.step_dir("cropped") / "p001.geom.json").write_text("NOT_VALID_JSON{", encoding="utf-8")
    before = (task.step_dir("raw") / "p001.jpg").read_bytes()

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        TableOrientationDetector().execute(task)

    assert any("invalid geometry JSON" in m for m in caplog.messages)
    assert (task.step_dir("raw") / "p001.jpg").read_bytes() == before


@pytest.mark.parametrize(
    ("row_bounds", "col_bounds"),
    [([], [0, 100, 200, 300]), ([(20, 50), (50, 80)], []), ([], [])],
    ids=["no-rows", "no-cols", "nothing"],
)
def test_empty_bounds_are_skipped(row_bounds, col_bounds, form, make_task, caplog):
    task = make_task()
    seed_raw(task, {1: form})
    write_geom(
        task, PageGeometry(width=2000, height=1000, header=(0, 20), row_bounds=row_bounds, col_bounds=col_bounds)
    )
    before = (task.step_dir("raw") / "p001.jpg").read_bytes()

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        TableOrientationDetector().execute(task)

    assert any("empty row or column bounds" in m for m in caplog.messages)
    assert (task.step_dir("raw") / "p001.jpg").read_bytes() == before


def test_single_cell_table_counts_as_upright(form, make_task, caplog):
    task = make_task()
    seed_raw(task, {1: form})
    write_geom(task, PageGeometry(width=500, height=1000, header=(0, 20), row_bounds=[(20, 100)], col_bounds=[0, 500]))

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        TableOrientationDetector().execute(task)

    assert any("p001 is upright" in m for m in caplog.messages)


def test_unreadable_raw_image_is_skipped_with_warning(make_task, caplog):
    task = make_task()
    task.step_dir("raw").mkdir(parents=True)
    (task.step_dir("raw") / "p001.jpg").write_bytes(b"")
    # sideways geometry, so the detector tries to rotate the raw image
    write_geom(
        task, PageGeometry(width=2000, height=1000, header=(0, 20), row_bounds=[(20, 50)], col_bounds=[0, 1, 2, 3])
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        TableOrientationDetector().execute(task)

    assert any("Could not read raw image" in m for m in caplog.messages)
    assert (task.step_dir("raw") / "p001.jpg").read_bytes() == b""


def test_header_in_lower_half_triggers_180_rotation(form, make_task, caplog):
    task = make_task()
    seed_raw(task, {1: form})
    # uniform columns, so only the header position says "upside down"
    write_geom(
        task,
        PageGeometry(
            width=300,
            height=2000,
            header=(1200, 1250),
            row_bounds=[(100, 200), (200, 300), (300, 400)],
            col_bounds=[0, 100, 200, 300],
        ),
    )
    raw = task.step_dir("raw") / "p001.jpg"

    with (
        patch.object(TableExtractor, "extract_printed_table") as extract,
        caplog.at_level(logging.INFO, logger=LOGGER),
    ):
        TableOrientationDetector().execute(task)

    assert any("180° upside-down" in m for m in caplog.messages)
    extract.assert_called_once()
    assert extract.call_args.args[0] == raw
    assert np.array_equal(cv2.imread(str(raw)).shape, form.shape)  # 180° keeps the portrait size


# ---------------------------------------------------------------- real scans (need pdftoppm)

REAL_UPRIGHT = {"width": 1507, "height": 2178, "rows": 50, "species_width": 365}


@requires_pdftoppm
@pytest.mark.parametrize("rotation", [None, *ROTATIONS])
def test_real_scan_is_restored_upright(rotation, sample_scan, make_task):
    task = make_task(pages=[1])
    seed_raw(task, {1: rotated(sample_scan, rotation)})
    TableExtractor().execute(task)

    TableOrientationDetector().execute(task)

    geom = read_geom(task)
    assert abs(geom["width"] - REAL_UPRIGHT["width"]) <= 3
    assert abs(geom["height"] - REAL_UPRIGHT["height"]) <= 3
    assert len(geom["row_bounds"]) == REAL_UPRIGHT["rows"]
    assert len(geom["col_bounds"]) == 12
    widths = col_widths(geom)
    assert widths.index(max(widths)) == 1
    assert abs(widths[1] - REAL_UPRIGHT["species_width"]) <= 3
    h, w = cv2.imread(str(task.step_dir("raw") / "p001.jpg")).shape[:2]
    assert h > w


@requires_pdftoppm
def test_full_pipeline_on_rotated_pdf_restores_upright(form, upright_geom, tmp_path):
    pdf = write_pdf(tmp_path / "sideways.pdf", [rotated(form, "90cw"), form])
    task = PipelineTask(input_pdf=pdf, output_directory=tmp_path / "out")

    with patch.object(LlmExtractor, "execute", return_value=None):
        result = build_pipeline().run(task)

    assert isinstance(result, DocResult)
    for num in (1, 2):
        assert_same_table(read_geom(task, num), upright_geom, tol=4)


@requires_pdftoppm
def test_real_rotated_pdf_matches_upright_original(tmp_path):
    def geometry(name: str) -> dict:
        task = PipelineTask(input_pdf=INPUT_DIR / name, output_directory=tmp_path / name, pages=[1])
        build_pipeline().run(task, only=["raw", "cropped"])
        return read_geom(task)

    upright = geometry("scan_new.pdf")
    restored = geometry("scan_new_rotated.pdf")

    assert restored["height"] > restored["width"]
    assert len(restored["row_bounds"]) == 74
    assert len(restored["col_bounds"]) - 1 == 27
    assert_same_table(restored, upright, tol=3)


@requires_pdftoppm
def test_real_rotated_pdf_without_header_is_restored_upright(tmp_path):
    task = PipelineTask(input_pdf=INPUT_DIR / "scan_new_rotated_no_header.pdf", output_directory=tmp_path, pages=[1])
    build_pipeline().run(task, only=["raw", "cropped"])

    geom = read_geom(task)
    assert geom["height"] > geom["width"]
    assert len(geom["row_bounds"]) == 74
    h, w = cv2.imread(str(task.step_dir("raw") / "p001.jpg")).shape[:2]
    assert h > w
