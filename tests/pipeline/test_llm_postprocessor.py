"""LlmPostprocessor: ditto marks left by the LLM are resolved in processed/pNNN.json (+ .md/.csv)."""

import csv

import pytest

from backend.pipeline.models.llm_responses import ExtractionResult, PageTable, TableRow
from backend.pipeline.models.pipeline_parts import collect_result, page_stem
from backend.pipeline.steps import LlmPostprocessor
from backend.pipeline.steps.llm_extractor import write_result
from backend.pipeline.steps.llm_postprocessor import (
    clear_carryover,
    convert_age_codes,
    is_ditto,
    postprocess_rows,
    resolve_dittos,
)


def row(n: int, **values) -> TableRow:
    base = dict.fromkeys(TableRow.model_fields, "") | {"row_number": n}
    return TableRow.model_validate(base | values)


def write_page(task, num: int, rows: list[TableRow]) -> None:
    out = task.step_dir("processed")
    out.mkdir(parents=True, exist_ok=True)
    result = ExtractionResult(
        source="fake/model",
        table=PageTable(rows=rows, page_confidence=1.0),
        input_tokens=1,
        output_tokens=1,
        estimated_cost=0.01,
    )
    write_result(out, num, result)


def read_page(task, num: int) -> ExtractionResult:
    return ExtractionResult.model_validate_json(
        (task.step_dir("processed") / f"{page_stem(num)}.json").read_text("utf-8")
    )


@pytest.mark.parametrize(
    ("column", "value", "expected"),
    [
        ("species", "ll", True),
        ("species", "I I", True),
        ("species", '"', True),
        ("species", "〃", True),
        ("species", "u", True),
        ("species", "dito", True),
        ("species", "1", True),
        ("species", "11", True),
        ("species", "Tannenmeise", False),
        ("species", "Ll. sp", False),
        ("species", "", False),
        ("age", "ll", True),
        ("age", "u", True),
        ("age", '"', True),
        ("age", "1", False),  # age codes can be digits
        ("age", "Fd", False),
    ],
)
def test_is_ditto(column, value, expected):
    assert is_ditto(value, column) is expected


def test_species_and_age_are_resolved_by_default():
    rows = [
        {"species": "Fitis", "age": "Fd", "weight_g": "9"},
        {"species": "ll", "age": "u", "weight_g": "11"},
        {"species": "Fitis", "age": "1", "weight_g": "11"},
    ]
    assert resolve_dittos(rows) == [(1, "species", "ll", "Fitis"), (1, "age", "u", "Fd")]
    assert rows[1] == {"species": "Fitis", "age": "Fd", "weight_g": "11"}
    assert rows[2]["age"] == "1"


def test_resolve_dittos_uses_nearest_non_empty_value_above():
    rows = [{"species": v} for v in ["Tannenmeise", "ll", "", '"', "Fitis", "II"]]
    fixes = resolve_dittos(rows)

    assert [r["species"] for r in rows] == ["Tannenmeise", "Tannenmeise", "", "Tannenmeise", "Fitis", "Fitis"]
    assert fixes == [
        (1, "species", "ll", "Tannenmeise"),
        (3, "species", '"', "Tannenmeise"),
        (5, "species", "II", "Fitis"),
    ]


def test_resolve_dittos_keeps_mark_without_value_above():
    rows = [{"species": "ll"}, {"species": "Fitis"}]
    assert resolve_dittos(rows, ["species"]) == []
    assert rows[0]["species"] == "ll"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("dj", "Fd"), ("DJ", "Fd"), ("dj.", "Fd"), ("ad", "Fnd"), ("Ad.", "Fnd"), ("Fd", "Fd"), ("adj", "adj"), ("", "")],
)
def test_convert_age_codes(value, expected):
    rows = [{"age": value}]
    fixes = convert_age_codes(rows)
    assert rows[0]["age"] == expected
    assert fixes == ([(0, "age", value, expected)] if value != expected else [])


def test_clear_carryover_in_rows_without_a_bird():
    rows = [
        {"species": "Heckenbraunelle", "age": "Fnd", "date": "22.7.", "time": "08:00"},
        {"species": "Ring verloren", "age": "", "date": "22.7.", "time": "08:00"},  # carried by the LLM
        {"species": "", "age": "", "date": "22.7.", "time": "08:00"},  # empty row
        {"species": "Ring fehlt", "age": "", "date": "23.7.", "time": "09:00"},  # written: differs from above
        {"species": "Birkenzeisig", "age": "Fnd", "date": "23.7.", "time": "09:00"},  # bird: stays
    ]
    fixes = clear_carryover(rows)

    assert [(r["date"], r["time"]) for r in rows] == [
        ("22.7.", "08:00"),
        ("", ""),
        ("", ""),
        ("23.7.", "09:00"),
        ("23.7.", "09:00"),
    ]
    assert fixes == [
        (1, "date", "22.7.", ""),
        (2, "date", "22.7.", ""),
        (1, "time", "08:00", ""),
        (2, "time", "08:00", ""),
    ]


def test_postprocess_rows_applies_all_rules():
    rows = [{"species": "Fitis", "age": "dj"}, {"species": "ll", "age": "Fnd"}]
    assert postprocess_rows(rows) == [(1, "species", "ll", "Fitis", "ditto"), (0, "age", "dj", "Fd", "age")]


def test_step_rewrites_json_md_and_csv(make_task):
    task = make_task()
    write_page(task, 1, [row(1, species="Tannenmeise", age="Fd"), row(2, species="ll", age='"'), row(3, age="ad")])
    write_page(task, 2, [row(1, species="Fitis")])
    before_p2 = (task.step_dir("processed") / "p002.json").read_text("utf-8")

    LlmPostprocessor().execute(task)

    result = read_page(task, 1)
    assert [r.species for r in result.table.rows] == ["Tannenmeise", "Tannenmeise", ""]
    assert [r.age for r in result.table.rows] == ["Fd", "Fd", "Fnd"]
    assert [(f.row_number, f.column, f.original, f.value, f.rule) for f in result.postprocessed] == [
        (2, "species", "ll", "Tannenmeise", "ditto"),
        (2, "age", '"', "Fd", "ditto"),
        (3, "age", "ad", "Fnd", "age"),
    ]
    assert result.estimated_cost == 0.01  # rest of the result is kept

    with open(task.step_dir("processed") / "p001.csv", encoding="utf-8", newline="") as f:
        assert [r["species"] for r in csv.DictReader(f)] == ["Tannenmeise", "Tannenmeise", ""]
    assert "| ll" not in (task.step_dir("processed") / "p001.md").read_text("utf-8")
    # untouched page is not rewritten
    assert (task.step_dir("processed") / "p002.json").read_text("utf-8") == before_p2


def test_step_is_idempotent_and_respects_page_filter(make_task):
    task = make_task()
    write_page(task, 1, [row(1, species="Fitis"), row(2, species="ll")])
    write_page(task, 2, [row(1, species="Fitis"), row(2, species="ll")])

    LlmPostprocessor().execute(make_task(pages=[1]))
    LlmPostprocessor().execute(make_task(pages=[1]))

    assert len(read_page(task, 1).postprocessed) == 1
    assert read_page(task, 2).table.rows[1].species == "ll"


def test_old_results_without_postprocessed_field_still_load(make_task):
    task = make_task()
    write_page(task, 1, [row(1, species="Fitis")])
    path = task.step_dir("processed") / "p001.json"
    path.write_text(path.read_text("utf-8").replace('"postprocessed": []', '"_": 0'), encoding="utf-8")

    assert read_page(task, 1).postprocessed == []
    assert collect_result(task).pages == []  # no geometry -> skipped, but no validation error
