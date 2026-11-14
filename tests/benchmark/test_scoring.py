"""Scoring functions of ``benchmark/run_benchmark.py`` on tiny fixtures (no LLM, no PDFs)."""

import csv
import json
import math

import pytest

import benchmark.run_benchmark as rb

REF_HEADER = "Bague;Espèce;Sexe;Age;Jour/Mois;Heure;Aile;Poids;Laisser en blanc;"


def write_ref(ref_dir, stem: str, lines: list[str]) -> None:
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / f"{stem}.csv").write_text("\n".join([REF_HEADER, *lines]) + "\n", encoding="utf-8-sig")


def write_pred(run_root, stem: str, rows: list[list[str]], meta: dict | None = None) -> None:
    pdf_stem, page = stem.rsplit("_p", 1)
    out = run_root / pdf_stem / "processed"
    out.mkdir(parents=True, exist_ok=True)
    base = out / f"p{int(page):03d}"
    with open(base.with_suffix(".csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["row_number", *rb.COLUMNS])
        w.writerows([[str(i), *r] for i, r in enumerate(rows, 1)])
    if meta is not None:
        base.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")


@pytest.fixture
def ref_dir(tmp_path, monkeypatch):
    d = tmp_path / "refs"
    monkeypatch.setattr(rb, "REF_DIR", d)
    return d


# ---------------------------------------------------------------- normalize / levenshtein / cell_score


@pytest.mark.parametrize(
    ("col", "value", "expected"),
    [
        ("species", "  Parus   Major ", "parus major"),
        ("weight_g", "12,5", "12.5"),
        ("weight_g", "18.00", "18"),
        ("wing_length_mm", "70,0", "70"),
        ("weight_g", "18.05", "18.05"),
        ("date", "16.7.", "16.7"),
        ("time", "8:30", "08:30"),
        ("time", "13:30", "13:30"),
        ("time", "8:3", "8:3"),
        ("ring_no", "A9 04 01", "a9 04 01"),
        ("species", "", ""),
    ],
)
def test_normalize(col, value, expected):
    assert rb.normalize(col, value) == expected


@pytest.mark.parametrize(
    ("a", "b", "dist"),
    [("", "", 0), ("abc", "", 3), ("", "ab", 2), ("kitten", "sitting", 3), ("same", "same", 0), ("ab", "ba", 2)],
)
def test_levenshtein(a, b, dist):
    assert rb.levenshtein(a, b) == dist
    assert rb.levenshtein(b, a) == dist


def test_cell_score_exact_columns():
    assert rb.cell_score("ring_no", "A90401", "a90401") == 1.0
    assert rb.cell_score("ring_no", "A90401", "A90402") == 0.0
    assert rb.cell_score("weight_g", "9,5", "9.5") == 1.0
    assert rb.cell_score("time", "7:30", "07:30") == 1.0


def test_cell_score_fuzzy_columns_give_partial_credit():
    assert rb.cell_score("species", "Kohlmeise", "Kohlmeis") == pytest.approx(1 - 1 / 9)
    assert rb.cell_score("species", "Kohlmeise", "kohlmeise") == 1.0
    assert rb.cell_score("additional_remark", "Brutfleck", "Brutfleek") == pytest.approx(1 - 1 / 9)
    assert rb.cell_score("species", "ab", "xyz") == 0.0  # never negative


def test_cell_score_empty_handling():
    assert rb.cell_score("species", "", "  ") is None  # both empty: not counted
    assert rb.cell_score("species", "Amsel", "") == 0.0  # missed
    assert rb.cell_score("sex", "", "m") == 0.0  # hallucinated


# ---------------------------------------------------------------- forward_fill / column_key / load_flags


def row(**values) -> dict[str, str]:
    return dict.fromkeys(rb.COLUMNS, "") | values


def test_forward_fill_carries_date_time_and_ring_prefix():
    rows = [
        row(ring_no="A90401", date="16.7.", time="07:30"),
        row(ring_no="2"),
        row(ring_no="13", time="09:00"),
        row(ring_no="B12345", date="17.7."),
        row(),
    ]
    out = rb.forward_fill(rows)
    assert [r["ring_no"] for r in out] == ["A90401", "A90402", "A90413", "B12345", ""]  # empty ring stays empty
    assert [r["date"] for r in out] == ["16.7.", "16.7.", "16.7.", "17.7.", "17.7."]
    assert [r["time"] for r in out] == ["07:30", "07:30", "09:00", "09:00", "09:00"]


def test_forward_fill_does_not_carry_other_columns():
    out = rb.forward_fill([row(species="Amsel", weight_g="90"), row()])
    assert out[1]["species"] == "" and out[1]["weight_g"] == ""


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Espèce", "species"),
        ("species", "species"),
        ("Bague No", "ring_no"),
        ("Jour/Mois", "date"),
        ("Heure", "time"),
        ("Aile", "wing_length_mm"),
        ("Alle", "wing_length_mm"),
        ("Poids", "weight_g"),
        ("♂ Sexe ♀", "sex"),
        ("Age", "age"),
        ("Laisser en blanc", "additional_remark"),
        ("weight_g", "weight_g"),
        ("something else", None),
        ("", None),
    ],
)
def test_column_key_aliases(name, key):
    assert rb.column_key(name) == key


def test_load_flags_keeps_row_bound_mappable_uncertainties():
    meta = {
        "table": {
            "uncertainties": [
                {"row_number": 2, "column": "Espèce"},
                {"row_number": None, "column": "Espèce"},
                {"row_number": 3, "column": "???"},
                {"row_number": 3, "column": "Aile"},
            ]
        }
    }
    assert rb.load_flags(meta) == ({(2, "species"), (3, "wing_length_mm")}, 4)
    assert rb.load_flags(None) == (set(), 0)
    assert rb.load_flags({"table": {}}) == (set(), 0)


# ---------------------------------------------------------------- loading


def test_load_reference_skips_empty_rows_and_forward_fills(ref_dir):
    write_ref(
        ref_dir,
        "doc_p001",
        [
            "A90401; Tannenmeise ;;Fd;16.7.;07:30;62;9;;",
            ";;;;;;;;;",
            "2;Kohlmeise;m;Fnd;;;70;18,0;;",
            "A90403;Blaumeise",  # short row: missing columns become ""
        ],
    )

    rows = rb.load_reference("doc_p001")

    assert len(rows) == 3
    assert rows[0] == row(
        ring_no="A90401", species="Tannenmeise", age="Fd", date="16.7.", time="07:30", wing_length_mm="62", weight_g="9"
    )
    assert rows[1]["ring_no"] == "A90402"
    assert (rows[1]["date"], rows[1]["time"]) == ("16.7.", "07:30")
    assert rows[2] == row(ring_no="A90403", species="Blaumeise", date="16.7.", time="07:30")


def test_load_prediction_reads_csv_and_meta(tmp_path):
    write_pred(tmp_path, "doc_p002", [["A1", " Amsel "] + [""] * 7], meta={"source": "x/y"})

    rows, meta = rb.load_prediction(tmp_path, "doc_p002")

    assert rows == [row(ring_no="A1", species="Amsel")]
    assert meta == {"source": "x/y"}
    assert rb.load_prediction(tmp_path, "doc_p003") == ([], None)


def test_pages_by_pdf_groups_reference_stems():
    assert rb.pages_by_pdf(["a_b_p001", "a_b_p003", "c_p002"]) == {"a_b": [1, 3], "c": [2]}


# ---------------------------------------------------------------- evaluate


@pytest.fixture
def scored_run(ref_dir, tmp_path):
    write_ref(
        ref_dir,
        "doc_p001",
        [
            "A90401;Tannenmeise;;Fd;16.7.;07:30;62;9;;",
            "2;Kohlmeise;m;Fnd;;;70;18,0;;",
            ";;;;;;;;;",
            "A90403;Blaumeise;;Fd;17.7.;8:00;65;11;;",
        ],
    )
    write_ref(ref_dir, "doc_p002", ["A1;Amsel;;;1.1.;10:00;;;;"])  # no prediction -> missing page
    run_root = tmp_path / "run"
    write_pred(
        run_root,
        "doc_p001",
        [
            ["A90401", "Tannenmeise", "", "Fd", "16.7", "07:30", "62", "9.0", ""],  # all correct
            ["A90402", "Kohlmeis", "m", "Fnd", "16.7.", "07:30", "70", "18", ""],  # species partial
            ["A90403", "Blaumeise", "f", "", "17.7.", "08:00", "66", "11", ""],  # sex/age/wing wrong
            [""] * 9,
        ],
        meta={
            "source": "openai/test-model",
            "estimated_cost": 0.02,
            "input_tokens": 100,
            "output_tokens": 50,
            "table": {
                "uncertainties": [
                    {"row_number": 2, "column": "Espèce"},
                    {"row_number": 3, "column": "Aile"},
                    {"row_number": None, "column": "Heure"},
                ]
            },
        },
    )
    diff_dir = tmp_path / "result" / "diff"
    return rb.evaluate("test", run_root, ["doc_p001", "doc_p002"], diff_dir=diff_dir), diff_dir


def test_evaluate_counts_and_scores_cells(scored_run):
    res, _ = scored_run

    assert res.model == "openai/test-model"
    assert res.n_pages == 1
    assert res.missing_pages == ["doc_p002"]
    assert res.cost == pytest.approx(0.02)
    assert res.tokens == 150
    assert res.flags_total == 3

    cols = res.cols
    assert (cols["ring_no"].count, cols["ring_no"].score) == (3, 3.0)
    assert cols["species"].count == 3
    assert cols["species"].score == pytest.approx(2 + 8 / 9)
    assert (cols["sex"].count, cols["sex"].score, cols["sex"].hallucinated) == (2, 1.0, 1)
    assert (cols["age"].count, cols["age"].score, cols["age"].missed) == (3, 2.0, 1)
    assert (cols["date"].score, cols["time"].score, cols["weight_g"].score) == (3.0, 3.0, 3.0)
    assert (cols["wing_length_mm"].count, cols["wing_length_mm"].score) == (3, 2.0)
    assert cols["additional_remark"].count == 0
    assert math.isnan(cols["additional_remark"].mean)

    # weights: species 3x, all others 1x
    num = 3 + 3 * (2 + 8 / 9) + 1 + 2 + 3 + 3 + 2 + 3
    den = 3 + 3 * 3 + 2 + 3 + 3 + 3 + 3 + 3
    assert res.weighted_score == pytest.approx(num / den)
    assert res.pages == {"doc_p001": pytest.approx(num / den)}
    assert res.errors == pytest.approx(1 / 9 + 1 + 1 + 1)


def test_evaluate_gives_credit_for_flagged_errors(scored_run):
    res, _ = scored_run
    species, wing = res.cols["species"], res.cols["wing_length_mm"]

    assert (species.flags, wing.flags, res.flags) == (1, 1, 2)
    assert species.flagged_errors == pytest.approx(1 / 9)
    assert wing.flagged_errors == 1.0
    assert wing.flag_score == pytest.approx(2 + rb.FLAG_CREDIT)
    assert res.flag_adjusted_score > res.weighted_score
    assert res.flag_recall == pytest.approx((1 / 9 + 1) / (1 / 9 + 3))
    assert res.flag_precision == pytest.approx((1 / 9 + 1) / 2)
    # review cost: 2 checks + flagged errors at half fix cost + unflagged errors at full fix cost
    assert res.review_cost(fix_cost=1.0, check_cost=0.1) == pytest.approx(0.2 + (1 / 9 + 1) * 0.5 + 2)


def test_evaluate_writes_diff_files(scored_run):
    _, diff_dir = scored_run

    md = (diff_dir / "doc_p001.md").read_text(encoding="utf-8")
    assert md.startswith("# doc_p001 — weighted score")
    assert "⚠️🚩 Kohlmeis → Kohlmeise" in md
    assert "❌🚩 66 → 65" in md
    assert "❌ f → ∅" in md
    assert "✅ A90401" in md
    assert not (diff_dir / "doc_p002.md").exists()

    with open(diff_dir / "errors.csv", encoding="utf-8", newline="") as f:
        errors = list(csv.DictReader(f))
    assert {(e["row"], e["column"], e["kind"], e["flagged"]) for e in errors} == {
        ("2", "species", "partial", "True"),
        ("3", "sex", "hallucinated", "False"),
        ("3", "age", "missed", "False"),
        ("3", "wing_length_mm", "wrong", "True"),
    }


def test_evaluate_without_diff_dir_writes_nothing(ref_dir, tmp_path):
    write_ref(ref_dir, "doc_p001", ["A1;Amsel;;;;;;;;"])
    write_pred(tmp_path / "run", "doc_p001", [["A1", "Amsel"] + [""] * 7])

    res = rb.evaluate("x", tmp_path / "run", ["doc_p001"])

    assert res.weighted_score == 1.0
    assert res.model == "?"  # no meta json
    assert sorted(p.name for p in tmp_path.iterdir()) == ["refs", "run"]


def test_report_writes_markdown_and_csv(scored_run, tmp_path):
    res, _ = scored_run
    out = tmp_path / "report"

    text = rb.report([res], fix_cost=0.05, check_cost=0.01, out_dir=out)

    assert (out / "report.md").read_text(encoding="utf-8") == text
    assert "| test | openai/test-model | 1 |" in text
    assert "missing predictions for** doc_p002" in text
    with open(out / "report.csv", encoding="utf-8", newline="") as f:
        (summary,) = list(csv.DictReader(f))
    assert summary["run"] == "test"
    assert float(summary["weighted_score"]) == pytest.approx(res.weighted_score)


def test_mark_cells():
    assert rb.mark("a", "a", None) == ""
    assert rb.mark("a", "a", 1.0) == "✅ a"
    assert rb.mark("ab", "a|", 0.5, flagged=True) == "⚠️🚩 a\\| → ab"
    assert rb.mark("", "x", 0.0) == "❌ x → ∅"


def test_model_dir():
    assert str(rb.model_dir("openai/gpt-x", "low")) == "openai/gpt-x/low"
    assert str(rb.model_dir("org/sub/model", "high")) == "org/sub_model/high"
