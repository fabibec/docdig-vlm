"""``python -m backend.pipeline``: argument parsing and end-to-end runs without the LLM step."""

import argparse
import json

import pytest

from backend.pipeline.__main__ import _steps, main
from tests.pipeline.helpers import draw_form, requires_pdftoppm, write_pdf


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("raw", ["raw"]),
        ("raw,cropped,processed", ["raw", "cropped", "processed"]),
        (" raw , cropped ,", ["raw", "cropped"]),
        ("cropped,ocr,tesseract", ["cropped", "ocr", "tesseract"]),
    ],
)
def test_steps_parses_comma_separated_list(value, expected):
    assert _steps(value) == expected


def test_unknown_step_is_rejected():
    with pytest.raises(argparse.ArgumentTypeError, match="unknown step"):
        _steps("raw,llm")


def test_unknown_step_on_command_line_exits_with_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["run", str(tmp_path), "--steps", "bogus"])
    assert exit_info.value.code == 2
    assert "unknown step" in capsys.readouterr().err


def test_folder_without_pdfs_returns_1(tmp_path, capsys):
    (tmp_path / "notes.txt").write_text("not a pdf")
    assert main(["run", str(tmp_path), "-o", str(tmp_path / "out")]) == 1
    assert "no PDFs found" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


@pytest.fixture
def form_pdf(tmp_path):
    img, _, _ = draw_form(rows=12, row_h=60)
    (tmp_path / "in").mkdir()
    return write_pdf(tmp_path / "in" / "form.pdf", [img, img])


@requires_pdftoppm
def test_run_raw_step_renders_pages(form_pdf, tmp_path, capsys):
    out = tmp_path / "out"

    assert main(["run", str(form_pdf), "--steps", "raw", "-o", str(out)]) == 0

    assert sorted(p.name for p in (out / "form" / "raw").iterdir()) == ["p001.jpg", "p002.jpg"]
    assert not (out / "form" / "cropped").exists()
    stdout = capsys.readouterr().out
    assert "[      raw] page 2/2" in stdout
    assert "0 page(s) complete" in stdout


@requires_pdftoppm
def test_run_folder_with_page_filter_and_draw_rows(form_pdf, tmp_path):
    out = tmp_path / "out"

    code = main(["run", str(form_pdf.parent), "--steps", "raw,cropped", "--page", "2", "--draw-rows", "-o", str(out)])

    assert code == 0
    doc = out / "form"
    assert [p.name for p in (doc / "raw").iterdir()] == ["p002.jpg"]
    assert (doc / "cropped" / "p002.jpg").is_file()
    assert (doc / "cropped" / "grid" / "p002.jpg").is_file()
    geom = json.loads((doc / "cropped" / "p002.geom.json").read_text())
    assert len(geom["row_bounds"]) == 12
    assert len(geom["col_bounds"]) == 12
