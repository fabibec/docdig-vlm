"""ImgExtractor: PDF pages -> raw/pNNN.jpg (needs pdftoppm)."""

import numpy as np
import pytest

from backend.pipeline.models.pipeline_parts import PipelineTask
from backend.pipeline.steps import ImgExtractor
from tests.pipeline.helpers import read_image, requires_pdftoppm, write_pdf

pytestmark = requires_pdftoppm


@pytest.fixture
def two_page_pdf(tmp_path):
    portrait = np.full((600, 400, 3), 255, np.uint8)
    landscape = np.full((300, 500, 3), 0, np.uint8)
    return write_pdf(tmp_path / "two.pdf", [portrait, landscape])


def test_every_page_is_rendered_as_jpg(two_page_pdf, tmp_path):
    calls = []
    task = PipelineTask(
        input_pdf=two_page_pdf, output_directory=tmp_path / "out", on_progress=lambda *a: calls.append(a)
    )

    ImgExtractor().execute(task)

    raw = task.step_dir("raw")
    assert sorted(p.name for p in raw.iterdir()) == ["p001.jpg", "p002.jpg"]
    assert task.page_nums("raw") == [1, 2]
    assert calls == [("raw", 1, 2), ("raw", 2, 2)]
    # rendered at the step DPI (200), same as the PDF resolution -> pixel sizes round-trip
    assert read_image(raw / "p001.jpg").shape[:2] == (600, 400)
    p2 = read_image(raw / "p002.jpg")
    assert p2.shape[:2] == (300, 500)
    assert p2.mean() < 30  # the dark page stays dark


def test_page_filter_renders_only_selected_pages(two_page_pdf, tmp_path):
    task = PipelineTask(input_pdf=two_page_pdf, output_directory=tmp_path / "out", pages=[2])

    ImgExtractor().execute(task)

    assert [p.name for p in task.step_dir("raw").iterdir()] == ["p002.jpg"]


def test_dpi_scales_the_rendered_page(two_page_pdf, tmp_path):
    task = PipelineTask(input_pdf=two_page_pdf, output_directory=tmp_path / "out", pages=[1])

    ImgExtractor(dpi=100).execute(task)

    assert read_image(task.step_dir("raw") / "p001.jpg").shape[:2] == (300, 200)


def test_page_filter_outside_document_writes_nothing(two_page_pdf, tmp_path):
    task = PipelineTask(input_pdf=two_page_pdf, output_directory=tmp_path / "out", pages=[5])

    ImgExtractor().execute(task)

    assert list(task.step_dir("raw").iterdir()) == []
