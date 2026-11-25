"""Scoring rules of ``benchmark/scoring.py`` on tiny fixtures (no LLM, no PDFs)."""

import pytest

from benchmark import scoring as sc
from benchmark.scoring import Cell, score_page

REF_HEADER = "Bague;Espèce;Sexe;Age;Jour/Mois;Heure;Aile;Poids;Laisser en blanc;"


def score(column: str, ref: str, pred: str, flagged: bool = False) -> Cell:
    return score_page([Cell(column, ref, pred, flagged)])[0]


@pytest.mark.parametrize(
    ("col", "value", "expected"),
    [
        ("species", "  Parus   Major ", "parus major"),
        ("weight_g", "12,5", "12.5"),
        ("weight_g", "18.00", "18"),
        ("weight_g", "9.", "9"),
        ("wing_length_mm", "70,0", "70"),
        ("date", "16.7.", "16.7"),
        ("age", "Fd.", "fd"),
        ("time", "8:30", "08:30"),
        ("ring_no", "A9 04 01", "a90401"),
        ("weight_g", "-", ""),
        ("date", ".", ""),
        ("species", "", ""),
    ],
)
def test_normalize(col, value, expected):
    assert sc.normalize(col, value) == expected


def test_exact_columns():
    assert score("age", "Fd", "fd").score == 1
    assert score("age", "Fd", "Fnd").score == 0
    assert score("sex", "m", "f").score == 0


def test_fuzzy_columns_use_levenshtein():
    assert score("species", "Tannenmeise", "Tannenmeise").score == 1
    assert score("species", "Tannenmeise", "Tannenmeize").score == pytest.approx(1 - 1 / 11)
    assert score("additional_remark", "Brutfleck", "Brutflek").score == pytest.approx(1 - 1 / 9)


def test_empty_cells():
    assert score("weight_g", "", "").score is None
    assert score("weight_g", "-", "").score is None  # artifact counts as empty
    missed = score("weight_g", "9", "")
    hallucinated = score("weight_g", "", "9")
    assert (missed.score, missed.kind) == (0, "missed")
    assert (hallucinated.score, hallucinated.kind) == (0, "hallucinated")


def test_ring_is_scored_per_character():
    assert score("ring_no", "A90401", "A90401").score == 1
    assert score("ring_no", "A90401", "A50401").score == pytest.approx(5 / 6)
    assert score("ring_no", "A90401", "A50407").score == pytest.approx(4 / 6)
    assert score("ring_no", "A90401", "A9040").score == pytest.approx(5 / 6)  # length differs: Levenshtein
    assert score("ring_no", "A90401", "A50401").kind == "partial"


def test_repeated_ring_substitution_is_penalised_once():
    refs = [f"A9040{i}" for i in range(1, 6)]
    cells = score_page([Cell("ring_no", r, r.replace("9", "5", 1)) for r in refs])
    assert cells[0].score == pytest.approx(5 / 6)
    for c in cells[1:]:
        assert c.score == pytest.approx(1 - sc.SYSTEMATIC_REPEAT_COST / 6)


def test_ring_substitution_below_threshold_is_not_systematic():
    cells = score_page([Cell("ring_no", "A90401", "A50401"), Cell("ring_no", "A90402", "A50402")])
    assert [c.score for c in cells] == [pytest.approx(5 / 6)] * 2


def test_systematic_substitution_does_not_hide_other_errors():
    refs = [f"A9040{i}" for i in range(1, 5)]
    preds = ["A50401", "A50402", "A50403", "A50474"]
    cells = score_page([Cell("ring_no", r, p) for r, p in zip(refs, preds, strict=True)])
    assert cells[3].score == pytest.approx(1 - (sc.SYSTEMATIC_REPEAT_COST + 1) / 6)


def test_flag_adjustment():
    assert score("age", "Fd", "Fd", flagged=False).adjusted == 1
    assert score("age", "Fd", "Fd", flagged=True).adjusted == 1 - sc.FLAG_CORRECT_PENALTY
    assert score("age", "Fd", "F", flagged=True).adjusted == sc.FLAG_WRONG_CREDIT
    partial = score("species", "Tannenmeise", "Tannenmeize", flagged=True)
    assert partial.adjusted == pytest.approx(partial.points + (1 - partial.points) * sc.FLAG_WRONG_CREDIT)
    assert score("age", "", "", flagged=True).adjusted is None


def test_forward_fill_carries_date_time_and_ring_prefix():
    rows = [
        dict.fromkeys(sc.COLUMNS, "") | {"ring_no": "A90401", "date": "16.7.", "time": "07:30", "age": "Fd"},
        dict.fromkeys(sc.COLUMNS, "") | {"ring_no": "02", "weight_g": "9"},
        dict.fromkeys(sc.COLUMNS, "") | {"ring_no": "A90403", "time": "08:00", "age": "Fd"},
    ]
    out = sc.forward_fill(rows)
    assert [r["ring_no"] for r in out] == ["A90401", "A90402", "A90403"]
    assert [r["date"] for r in out] == ["16.7."] * 3
    assert [r["time"] for r in out] == ["07:30", "07:30", "08:00"]
    assert [r["weight_g"] for r in out] == ["", "9", ""]


def test_forward_fill_skips_rows_without_a_bird():
    row = dict.fromkeys(sc.COLUMNS, "")
    rows = [
        row | {"ring_no": "90518", "age": "Fnd", "date": "22.7", "time": "08:00"},
        row | {"ring_no": "90519", "species": "Ring verloren"},
        row | {"ring_no": "90520", "age": "Fnd", "weight_g": "12"},
    ]
    out = sc.forward_fill(rows)
    assert [(r["date"], r["time"]) for r in out] == [("22.7", "08:00"), ("", ""), ("22.7", "08:00")]


def test_load_reference_keeps_positions_resolves_dittos_and_cleans(tmp_path):
    path = tmp_path / "ref.csv"
    lines = [
        REF_HEADER,
        "A90401;Tannenmeise;;Fd;16.7.;07:30;62;9;;",
        ";;;;;;;;;",  # empty row in the middle stays (rows are matched by position)
        'A90403;"""";;Fd;;;63;-;;',
        ";;;;;;;;;",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    ref = sc.load_reference(path)

    assert len(ref.rows) == 3  # trailing empty rows dropped
    assert ref.rows[1]["species"] == ""
    assert ref.rows[2]["species"] == "Tannenmeise"
    assert ref.rows[2]["weight_g"] == ""
    assert ref.rows[2]["date"] == "16.7."
    assert ref.dittos == {(2, "species")}


@pytest.mark.parametrize(
    ("name", "key"),
    [("Espèce", "species"), ("Bague No", "ring_no"), ("Alle", "wing_length_mm"), ("weight_g", "weight_g"), ("?", None)],
)
def test_column_key_aliases(name, key):
    assert sc.column_key(name) == key
