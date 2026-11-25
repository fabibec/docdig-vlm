"""Unit tests of backend/api/flags.py: LLM uncertainties + plausibility rules."""

import pytest

from backend.api.enums import ColumnKey, FlagSource, Severity
from backend.api.flags import HEADER_KEYS, column_for, make_flags, row_values
from backend.pipeline.models.llm_responses import PageTable, TableRow, Uncertainty
from backend.pipeline.models.results import PageGeometry, PageResult


def _row(n: int = 1, /, **values: str) -> TableRow:
    return TableRow(row_number=n, **({c.value: "" for c in ColumnKey} | values))


def _page(rows=(), uncertainties=()) -> PageResult:
    geom = PageGeometry(width=100, height=100, header=(0, 10), row_bounds=[], col_bounds=[])
    table = PageTable(rows=list(rows), uncertainties=list(uncertainties), page_confidence=0.9)
    return PageResult(num=1, geometry=geom, table=table)


def _codes(**values: str) -> list[tuple[str, str]]:
    return [(f["code"], f["column"].value) for f in make_flags(_page([_row(**values)]))]


# ---------- rules ----------


@pytest.mark.parametrize("weight", ["2", "2000", "12.5", "12,5", " 30 "])
def test_weight_in_range_is_not_flagged(weight):
    assert _codes(weight_g=weight) == []


@pytest.mark.parametrize("weight", ["1.9", "2000.1", "0", "-5"])
def test_weight_out_of_range(weight):
    (flag,) = make_flags(_page([_row(weight_g=weight)]))
    assert flag["code"] == "weight_out_of_range"
    assert flag["column"] is ColumnKey.weight_g
    assert flag["source"] is FlagSource.rule
    assert flag["severity"] is Severity.error
    assert flag["params"] == {"value": weight, "min": 2, "max": 2000}
    assert flag["row_idx"] == 1
    assert "outside 2–2000" in flag["message"]


@pytest.mark.parametrize(("wing", "flagged"), [("20", False), ("700", False), ("19", True), ("701", True)])
def test_wing_range_is_20_to_700(wing, flagged):
    assert _codes(wing_length_mm=wing) == ([("wing_out_of_range", "wing_length_mm")] if flagged else [])


def test_non_numeric_measurements_are_not_a_number():
    flags = make_flags(_page([_row(weight_g="12g", wing_length_mm="abc")]))
    assert {(f["code"], f["column"]) for f in flags} == {
        ("not_a_number", ColumnKey.weight_g),
        ("not_a_number", ColumnKey.wing_length_mm),
    }
    assert all(f["severity"] is Severity.error for f in flags)
    assert {f["params"]["value"] for f in flags} == {"12g", "abc"}


@pytest.mark.parametrize("time", ["8:30", "08:30", "23:59", "0:00"])
def test_valid_times_are_not_flagged(time):
    assert _codes(time=time) == []


@pytest.mark.parametrize("time", ["24:00", "8:60", "830", "8h30", "8:3", "08:30:00"])
def test_bad_time_format(time):
    (flag,) = make_flags(_page([_row(time=time)]))
    assert flag["code"] == "time_format"
    assert flag["column"] is ColumnKey.time
    assert flag["severity"] is Severity.warning
    assert flag["params"] == {"value": time}


def test_empty_cells_and_empty_rows_are_ignored():
    assert make_flags(_page([_row(1), _row(2, species="Parus major")])) == []


def test_rule_flags_use_the_row_number():
    flags = make_flags(_page([_row(1, weight_g="5"), _row(7, weight_g="x")]))
    assert [f["row_idx"] for f in flags] == [7]


# ---------- LLM uncertainties ----------


def test_uncertainty_becomes_llm_flag():
    u = Uncertainty(
        row_number=3, column="Espèce", value_read="Prus", reason="unclear", best_guess="Parus", confidence=0.4
    )
    (flag,) = make_flags(_page(uncertainties=[u]))
    assert flag == {
        "row_idx": 3,
        "column": ColumnKey.species,
        "source": FlagSource.llm,
        "severity": Severity.warning,
        "message": "unclear",
        "suggestion": "Parus",
        "confidence": 0.4,
        "params": {"value": "Prus"},
    }


def test_uncertainty_without_row_is_skipped():
    u = Uncertainty(column="Espèce", value_read="?", reason="page-wide", confidence=0.1)
    assert make_flags(_page(uncertainties=[u])) == []


def test_llm_and_rule_flags_are_combined():
    u = Uncertainty(row_number=1, column="Poids", value_read="x", reason="smudged", confidence=0.2)
    flags = make_flags(_page([_row(weight_g="x")], [u]))
    assert [f["source"] for f in flags] == [FlagSource.llm, FlagSource.rule]


@pytest.mark.parametrize(
    ("header", "key"),
    [
        ("Bague No", ColumnKey.ring_no),
        ("Ring number", ColumnKey.ring_no),
        ("Espèce", ColumnKey.species),
        ("♂ Sexe ♀", ColumnKey.sex),
        ("Age", ColumnKey.age),
        ("Jour Mois", ColumnKey.date),
        ("Heure", ColumnKey.time),
        ("Alle", ColumnKey.wing_length_mm),
        ("Aile", ColumnKey.wing_length_mm),
        ("POIDS", ColumnKey.weight_g),
        ("laisser en blanc", ColumnKey.additional_remark),
        ("wing_length_mm", ColumnKey.wing_length_mm),  # exact column keys work too
        ("additional_remark", ColumnKey.additional_remark),
        ("Unknown column", None),
    ],
)
def test_column_for_maps_printed_headers(header, key):
    assert column_for(header) is key


def test_every_column_has_a_header_mapping():
    assert {key for _, key in HEADER_KEYS} == set(ColumnKey)


def test_row_values_strips_and_covers_all_columns():
    values = row_values(_row(species="  Parus  "))
    assert set(values) == {c.value for c in ColumnKey}
    assert values["species"] == "Parus"
    assert values["sex"] == ""
