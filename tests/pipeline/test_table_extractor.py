"""TableExtractor: printed grid -> cropped table image + geometry in cropped-image pixels."""

import json
from itertools import pairwise

import cv2
import numpy as np
import pytest

from backend.pipeline.models.results import PageGeometry
from backend.pipeline.steps import TableExtractor
from tests.pipeline.helpers import FORM_COL_WIDTHS, draw_form

TOL = 5  # px: line centres are averaged over a few pixels of (dilated) line thickness


def close(a: int, b: int, tol: int = TOL) -> bool:
    return abs(a - b) <= tol


@pytest.fixture
def form_page(tmp_path):
    img, xs, ys = draw_form(rows=12, row_h=60)
    raw = tmp_path / "raw"
    raw.mkdir()
    cv2.imwrite(str(raw / "p001.jpg"), img)
    return raw / "p001.jpg", xs, ys


def test_geometry_matches_drawn_grid(form_page, tmp_path):
    img_path, xs, ys = form_page
    out = tmp_path / "cropped"

    geom = TableExtractor.extract_printed_table(img_path, out)

    # crop origin = outer grid lines minus the default 8 px padding
    x0, y0 = xs[0] - 8, ys[0] - 8
    assert len(geom.col_bounds) == 12
    assert all(close(c, x - x0) for c, x in zip(geom.col_bounds, xs, strict=True))
    assert close(geom.header[0], ys[0] - y0) and close(geom.header[1], ys[1] - y0)
    assert len(geom.row_bounds) == 12
    for (r0, r1), (top, bottom) in zip(geom.row_bounds, pairwise(ys[1:]), strict=True):
        assert close(r0, top - y0) and close(r1, bottom - y0)
    assert geom.skew_angle == 0.0
    assert close(geom.width, xs[-1] - xs[0] + 16)


def test_writes_crop_geometry_and_outline(form_page, tmp_path):
    img_path, _, _ = form_page
    out = tmp_path / "cropped"

    geom = TableExtractor.extract_printed_table(img_path, out)

    crop = cv2.imread(str(out / "p001.jpg"))
    assert crop.shape[:2] == (geom.height, geom.width)
    on_disk = PageGeometry.model_validate_json((out / "p001.geom.json").read_text(encoding="utf-8"))
    assert on_disk == geom
    assert (out / "outlines" / "p001.jpg").is_file()
    assert not (out / "grid").exists()


def test_draw_rows_writes_grid_visualisation(form_page, tmp_path):
    img_path, _, _ = form_page
    TableExtractor.extract_printed_table(img_path, tmp_path / "cropped", draw_rows=True)
    assert (tmp_path / "cropped" / "grid" / "p001.jpg").is_file()


def test_column_widths_follow_the_printed_form(form_page, tmp_path):
    img_path, _, _ = form_page
    geom = TableExtractor.extract_printed_table(img_path, tmp_path / "cropped")
    widths = [b - a for a, b in pairwise(geom.col_bounds)]
    assert all(close(w, e) for w, e in zip(widths, FORM_COL_WIDTHS, strict=True))


def test_underlined_handwriting_is_not_a_row_line(tmp_path):
    img, xs, ys = draw_form(rows=12, row_h=60)
    # an "underline" 25 px below a row line, crossing every column (=> enough joints to look like a row line)
    y = ys[5] + 25
    cv2.line(img, (xs[0], y), (xs[-1], y), (0, 0, 0), 3)
    cv2.imwrite(str(tmp_path / "p001.jpg"), img)

    geom = TableExtractor.extract_printed_table(tmp_path / "p001.jpg", tmp_path / "cropped")

    assert len(geom.row_bounds) == 12
    heights = [b - a for a, b in geom.row_bounds]
    assert all(close(h, 60) for h in heights)


def test_underline_would_split_a_row_without_spurious_line_filter(tmp_path, monkeypatch):
    """Control for the test above: the underline really is detected as a line."""
    img, xs, ys = draw_form(rows=12, row_h=60)
    y = ys[5] + 25
    cv2.line(img, (xs[0], y), (xs[-1], y), (0, 0, 0), 3)
    cv2.imwrite(str(tmp_path / "p001.jpg"), img)
    monkeypatch.setattr(TableExtractor, "drop_spurious_lines", lambda lines, row_height: lines)

    geom = TableExtractor.extract_printed_table(tmp_path / "p001.jpg", tmp_path / "cropped")

    assert len(geom.row_bounds) == 13


def test_execute_processes_raw_pages_and_reports_progress(tmp_path, make_task):
    task = make_task()
    raw = task.step_dir("raw")
    raw.mkdir(parents=True)
    img, _, _ = draw_form(rows=10, row_h=60)
    for num in (1, 2):
        cv2.imwrite(str(raw / f"p{num:03d}.jpg"), img)
    (raw / "notes.jpg").write_bytes(b"ignored: not a page name")
    calls = []
    task.on_progress = lambda *a: calls.append(a)

    TableExtractor().execute(task)

    cropped = task.step_dir("cropped")
    assert sorted(p.name for p in cropped.glob("*.geom.json")) == ["p001.geom.json", "p002.geom.json"]
    assert calls == [("cropped", 1, 2), ("cropped", 2, 2)]
    assert json.loads((cropped / "p002.geom.json").read_text())["col_bounds"]


def test_execute_respects_page_filter(tmp_path, make_task):
    task = make_task(pages=[2])
    raw = task.step_dir("raw")
    raw.mkdir(parents=True)
    img, _, _ = draw_form(rows=10, row_h=60)
    for num in (1, 2):
        cv2.imwrite(str(raw / f"p{num:03d}.jpg"), img)

    TableExtractor().execute(task)

    assert [p.name for p in task.step_dir("cropped").glob("*.geom.json")] == ["p002.geom.json"]


def test_blank_page_raises_and_writes_no_geometry(tmp_path):
    """Current behaviour: a page without a table fails (the API marks the doc as error)."""
    cv2.imwrite(str(tmp_path / "p001.jpg"), np.full((2000, 1400, 3), 255, np.uint8))

    with pytest.raises(IndexError):
        TableExtractor.extract_printed_table(tmp_path / "p001.jpg", tmp_path / "cropped")

    assert not (tmp_path / "cropped" / "p001.geom.json").exists()


def test_missing_image_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        TableExtractor.extract_printed_table(tmp_path / "nope.jpg", tmp_path / "cropped")


@pytest.mark.parametrize(("kwargs", "match"), [({"split": "cells"}, "split"), ({"n": 0}, "n must be")])
def test_invalid_split_arguments_raise(kwargs, match, form_page, tmp_path):
    img_path, _, _ = form_page
    with pytest.raises(ValueError, match=match):
        TableExtractor.extract_printed_table(img_path, tmp_path / "cropped", **kwargs)


def test_split_rows_writes_one_strip_per_n_rows(form_page, tmp_path):
    img_path, _, _ = form_page
    out = tmp_path / "cropped"
    TableExtractor.extract_printed_table(img_path, out, split="row", n=5)
    # 13 cells (header + 12 rows) in chunks of 5
    assert sorted(p.name for p in (out / "rows").iterdir()) == [
        "p001_row01-05.jpg",
        "p001_row06-10.jpg",
        "p001_row11-13.jpg",
    ]


# ---------------------------------------------------------------- helper functions


class TestDropSpuriousLines:
    def test_regular_lines_are_kept(self):
        lines = [0, 50, 100, 150, 200]
        assert TableExtractor.drop_spurious_lines(lines, 50) == lines

    def test_line_closer_than_threshold_is_dropped(self):
        # 120 is 20 px after 100: < 0.75 * 50
        assert TableExtractor.drop_spurious_lines([0, 50, 100, 120, 150, 200], 50) == [0, 50, 100, 150, 200]

    def test_exactly_at_threshold_is_kept(self):
        assert TableExtractor.drop_spurious_lines([0, 40, 80], 40, min_ratio=0.75) == [0, 40, 80]
        assert TableExtractor.drop_spurious_lines([0, 40, 70], 40) == [0, 40, 70]  # 30 == 0.75 * 40

    def test_conflict_keeps_the_line_matching_the_row_height_better(self):
        # 70 conflicts with 60; 60 is exactly one row height after 0 -> 70 is dropped
        assert TableExtractor.drop_spurious_lines([0, 60, 70, 120], 60) == [0, 60, 120]
        # 125 conflicts with 110; 125 is 65 after 60 (closer to 60 than 110's 50) -> it replaces 110
        assert TableExtractor.drop_spurious_lines([0, 60, 110, 125], 60) == [0, 60, 125]

    def test_first_conflict_without_history_keeps_first_line(self):
        assert TableExtractor.drop_spurious_lines([0, 10, 60], 60) == [0, 60]

    def test_empty_input(self):
        assert TableExtractor.drop_spurious_lines([], 35) == []


class TestGroupCellSpans:
    def test_groups_cells_and_keeps_outer_padding(self):
        assert TableExtractor.group_cell_spans([10, 20, 30, 40, 50], 2, 0, 60) == [(1, 2, 0, 30), (3, 4, 30, 60)]

    def test_last_chunk_may_be_shorter(self):
        assert TableExtractor.group_cell_spans([10, 20, 30, 40], 2, 5, 45) == [(1, 2, 5, 30), (3, 3, 30, 45)]

    def test_n_below_one_raises(self):
        with pytest.raises(ValueError):
            TableExtractor.group_cell_spans([0, 1], 0, 0, 1)


def test_span_name():
    assert TableExtractor.span_name("p001", "row", 3, 3) == "p001_row03.jpg"
    assert TableExtractor.span_name("p001", "col", 1, 4) == "p001_col01-04.jpg"
