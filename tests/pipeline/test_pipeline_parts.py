"""Pipeline, PipelineTask, collect_result and build_pipeline()."""

import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

import backend.pipeline.steps as steps_module
from backend.pipeline import STEP_NAMES, build_pipeline
from backend.pipeline.models.llm_responses import ExtractionResult, PageTable, TableRow
from backend.pipeline.models.pipeline_parts import Pipeline, PipelineStep, PipelineTask, collect_result, page_stem
from backend.pipeline.models.results import DocResult, PageGeometry
from backend.pipeline.steps import (
    ImgExtractor,
    LlmExtractor,
    LlmPostprocessor,
    TableExtractor,
    TableOrientationDetector,
)
from tests.pipeline.helpers import PROJECT_ROOT


def touch(path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------- PipelineTask


def test_page_stem_is_zero_padded():
    assert page_stem(1) == "p001"
    assert page_stem(42) == "p042"
    assert page_stem(1234) == "p1234"


def test_step_dir_is_below_output_directory(make_task, tmp_path):
    task = make_task()
    assert task.step_dir("cropped") == tmp_path / "doc" / "cropped"


def test_input_pdf_must_exist(tmp_path):
    with pytest.raises(ValidationError):
        PipelineTask(input_pdf=tmp_path / "missing.pdf", output_directory=tmp_path)


def test_page_nums_lists_pages_sorted_and_ignores_other_files(make_task):
    task = make_task()
    raw = task.step_dir("raw")
    for name in ("p010.jpg", "p002.jpg", "p001.jpg", "p01.jpg", "x001.jpg", "p001.png", "p003.geom.json"):
        touch(raw / name)

    assert task.page_nums("raw") == [1, 2, 10]
    assert task.page_nums("raw", ".png") == [1]
    assert task.page_nums("raw", ".geom.json") == [3]


def test_page_nums_respects_page_filter(make_task):
    task = make_task(pages=[2, 3, 7])
    for n in (1, 2, 3):
        touch(task.step_dir("raw") / f"{page_stem(n)}.jpg")
    assert task.page_nums("raw") == [2, 3]


def test_page_nums_of_missing_step_is_empty(make_task):
    assert make_task().page_nums("processed", ".json") == []


def test_report_calls_progress_callback_only_if_set(make_task):
    calls = []
    make_task(on_progress=lambda *a: calls.append(a)).report("raw", 1, 3)
    make_task().report("raw", 2, 3)  # no callback: no error
    assert calls == [("raw", 1, 3)]


def test_progress_callback_is_not_serialised(make_task):
    task = make_task(on_progress=lambda *a: None)
    assert "on_progress" not in task.model_dump()


# ---------------------------------------------------------------- collect_result


def geometry(rows: int = 2) -> PageGeometry:
    return PageGeometry(
        width=100,
        height=200,
        header=(0, 20),
        row_bounds=[(20 + 30 * i, 50 + 30 * i) for i in range(rows)],
        col_bounds=list(range(0, 120, 10)),
    )


def extraction(cost: float, species: str = "Parus major") -> ExtractionResult:
    row = TableRow(
        row_number=1,
        ring_no="A1",
        species=species,
        sex="",
        age="",
        date="",
        time="",
        wing_length_mm="",
        weight_g="",
        additional_remark="",
    )
    return ExtractionResult(
        source="test/model",
        table=PageTable(rows=[row], page_confidence=0.9),
        input_tokens=10,
        output_tokens=5,
        estimated_cost=cost,
    )


def write_page(task: PipelineTask, num: int, geom: bool = True, processed: float | None = 0.01) -> None:
    if geom:
        touch(task.step_dir("cropped") / f"{page_stem(num)}.geom.json", geometry(num).model_dump_json())
    if processed is not None:
        touch(task.step_dir("processed") / f"{page_stem(num)}.json", extraction(processed).model_dump_json())


def test_collect_result_reads_geometry_table_and_cost(make_task):
    task = make_task()
    write_page(task, 1, processed=0.0123)
    write_page(task, 2, processed=0.5)

    result = collect_result(task)

    assert isinstance(result, DocResult)
    assert [p.num for p in result.pages] == [1, 2]
    p1 = result.pages[0]
    assert p1.geometry == geometry(1)
    assert p1.table.rows[0].species == "Parus major"
    assert p1.estimated_cost == 0.0123
    assert result.pages[1].estimated_cost == 0.5
    assert len(result.pages[1].geometry.row_bounds) == 2


def test_collect_result_skips_incomplete_pages(make_task):
    task = make_task()
    write_page(task, 1)
    write_page(task, 2, geom=False)  # processed but no geometry
    write_page(task, 3, processed=None)  # cropped but not processed

    assert [p.num for p in collect_result(task).pages] == [1]


def test_collect_result_respects_page_filter(make_task):
    task = make_task(pages=[2])
    write_page(task, 1)
    write_page(task, 2)

    assert [p.num for p in collect_result(task).pages] == [2]


def test_collect_result_of_empty_run_has_no_pages(make_task):
    assert collect_result(make_task()).pages == []


# ---------------------------------------------------------------- Pipeline


class Recorder(PipelineStep):
    name = "recorder"

    def __init__(self, log: list):
        self.log = log

    def setup(self):
        self.log.append(("setup", self.name))

    def close(self):
        self.log.append(("close", self.name))

    def execute(self, task):
        self.log.append(("execute", self.name))
        task.report(self.name, 1, 1)


def recorder(name: str, log: list) -> Recorder:
    """A Recorder whose step ``name`` (a ClassVar on real steps) is ``name``."""
    return type(f"Recorder_{name}", (Recorder,), {"name": name})(log)


@pytest.fixture
def recorded():
    log: list = []
    pipeline = Pipeline()
    for name in ("raw", "cropped", "processed"):
        pipeline.add_step(recorder(name, log))
    return pipeline, log


def test_run_executes_steps_in_order_and_reports_progress(recorded, make_task):
    pipeline, log = recorded
    progress = []
    task = make_task(on_progress=lambda *a: progress.append(a))

    result = pipeline.run(task)

    assert log == [("execute", "raw"), ("execute", "cropped"), ("execute", "processed")]
    assert progress == [("raw", 1, 1), ("cropped", 1, 1), ("processed", 1, 1)]
    assert task.output_directory.is_dir()
    assert result == DocResult(pages=[])


def test_run_only_executes_named_steps(recorded, make_task):
    pipeline, log = recorded
    pipeline.run(make_task(), only=["processed", "raw"])
    assert log == [("execute", "raw"), ("execute", "processed")]  # pipeline order, not `only` order


def test_run_returns_collected_result(make_task):
    task = make_task()

    class Writer(PipelineStep):
        name = "processed"

        def execute(self, task):
            write_page(task, 1, processed=0.25)

    result = Pipeline().add_step(Writer()).run(task)
    assert [(p.num, p.estimated_cost) for p in result.pages] == [(1, 0.25)]


def test_run_from_task_is_an_alias_of_run():
    assert Pipeline.run_from_task is Pipeline.run


def test_setup_and_close_respect_only(recorded):
    pipeline, log = recorded
    assert pipeline.setup(only=["cropped"]) is pipeline
    pipeline.close(only=["cropped"])
    assert log == [("setup", "cropped"), ("close", "cropped")]


def test_context_manager_sets_up_and_closes_all_steps(recorded):
    pipeline, log = recorded
    with pipeline as p:
        assert p is pipeline
        log.append("body")
    assert log == [
        ("setup", "raw"),
        ("setup", "cropped"),
        ("setup", "processed"),
        "body",
        ("close", "raw"),
        ("close", "cropped"),
        ("close", "processed"),
    ]


def test_context_manager_closes_on_error(recorded):
    pipeline, log = recorded
    with pytest.raises(RuntimeError), pipeline:
        raise RuntimeError("boom")
    assert log[-1] == ("close", "processed")


def test_add_step_is_chainable():
    p = Pipeline()
    step = recorder("raw", [])
    assert p.add_step(step) is p
    assert p.steps == [step]


# ---------------------------------------------------------------- build_pipeline


def test_default_pipeline_composition():
    pipeline = build_pipeline()
    assert [type(s) for s in pipeline.steps] == [
        ImgExtractor,
        TableExtractor,
        TableOrientationDetector,
        LlmExtractor,
        LlmPostprocessor,
    ]
    assert [s.name for s in pipeline.steps] == ["raw", "cropped", "cropped", "processed", "processed"]
    assert {s.name for s in pipeline.steps} == set(STEP_NAMES)


def test_draw_rows_is_passed_to_table_steps():
    table, orientation = build_pipeline(draw_rows=True).steps[1:3]
    default_table = build_pipeline().steps[1]
    assert isinstance(table, TableExtractor) and isinstance(orientation, TableOrientationDetector)
    assert isinstance(default_table, TableExtractor)
    assert table.draw_rows is True
    assert orientation.draw_rows is True
    assert default_table.draw_rows is False


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({"ocr": True}, ["FakeOcr"]),
        ({"tesseract": True}, ["FakeTesseract"]),
        ({"ocr": True, "tesseract": True}, ["FakeOcr", "FakeTesseract"]),
    ],
)
def test_ocr_flags_replace_the_llm_step(flags, expected, monkeypatch):
    # stand-ins, so the heavy optional OCR modules are never imported
    for attr, cls_name, step_name in (
        ("OcrExtractor", "FakeOcr", "ocr"),
        ("TesseractExtractor", "FakeTesseract", "tesseract"),
    ):
        fake = type(cls_name, (PipelineStep,), {"name": step_name, "execute": lambda self, task: None})
        monkeypatch.setattr(steps_module, attr, fake, raising=False)

    pipeline = build_pipeline(**flags)

    names = [type(s).__name__ for s in pipeline.steps]
    assert names[:3] == ["ImgExtractor", "TableExtractor", "TableOrientationDetector"]
    assert names[3:] == expected
    assert "LlmExtractor" not in names
    assert "LlmPostprocessor" not in names


def test_steps_package_imports_ocr_modules_lazily():
    code = (
        "import sys, backend.pipeline.steps as s;"
        "assert 'backend.pipeline.steps.ocr_extractor' not in sys.modules;"
        "assert 'backend.pipeline.steps.tesseract_extractor' not in sys.modules;"
        "\ntry:\n    s.DoesNotExist\nexcept AttributeError:\n    pass\nelse:\n    raise SystemExit(1)"
    )
    env = {**os.environ, "LLM_BASE_URL": "http://127.0.0.1:9", "LLM_API_KEY": "test-dummy"}
    subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, env=env, check=True, timeout=60)
