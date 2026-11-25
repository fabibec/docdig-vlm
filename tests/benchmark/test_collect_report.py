"""Collecting raw cells from pipeline outputs and building the report from them (no LLM, no PDFs)."""

import csv
import json
import math
import shutil
from unittest.mock import patch

import pytest

from benchmark import collect, report
from benchmark.__main__ import main
from benchmark.scoring import COLUMNS

REF_HEADER = "Bague;Espèce;Sexe;Age;Jour/Mois;Heure;Aile;Poids;Laisser en blanc;"
STEM = "scan_x_p001"


@pytest.fixture
def ref_dir(tmp_path, monkeypatch):
    d = tmp_path / "refs"
    d.mkdir()
    monkeypatch.setattr(collect, "REF_DIR", d)
    (d / f"{STEM}.csv").write_text(
        "\n".join(
            [
                REF_HEADER,
                "A90401;Tannenmeise;;Fd;16.7.;07:30;62;9;;",
                'A90402;"""";;Fd;;;65;9.5;;',
                "A90403;Fitis;m;Fnd;;;69;20;Brutfleck;",
            ]
        )
        + "\n",
        encoding="utf-8-sig",
    )
    return d


def write_pred(root, rows: list[dict], uncertainties=(), postprocessed=(), cost=0.02) -> None:
    out = root / "scan_x" / "processed"
    out.mkdir(parents=True, exist_ok=True)
    full = [dict.fromkeys(COLUMNS, "") | {"row_number": i} | r for i, r in enumerate(rows, 1)]
    meta = {
        "source": "fake/model",
        "table": {"rows": full, "uncertainties": list(uncertainties), "page_confidence": 1},
        "input_tokens": 100,
        "output_tokens": 50,
        "estimated_cost": cost,
        "postprocessed": list(postprocessed),
    }
    (out / "p001.json").write_text(json.dumps(meta), encoding="utf-8")


GOOD = [
    {
        "ring_no": "A90401",
        "species": "Tannenmeise",
        "age": "Fd",
        "date": "16.7.",
        "time": "07:30",
        "wing_length_mm": "62",
        "weight_g": "9",
    },
    {
        "ring_no": "A90402",
        "species": "Tannenmeise",
        "age": "Fd",
        "date": "16.7.",
        "time": "07:30",
        "wing_length_mm": "65",
        "weight_g": "9.5",
    },
    {
        "ring_no": "A90403",
        "species": "Fitis",
        "sex": "m",
        "age": "Fnd",
        "date": "16.7.",
        "time": "07:30",
        "wing_length_mm": "69",
        "weight_g": "20",
        "additional_remark": "Brutfleck",
    },
]


def test_collect_writes_raw_cells_with_flags_and_dittos(ref_dir, tmp_path):
    pred = [dict(r) for r in GOOD]
    pred[2]["age"] = "Fd"  # wrong
    pred[0]["sex"] = "f"  # hallucinated
    write_pred(
        tmp_path / "pred",
        pred,
        uncertainties=[{"row_number": 3, "column": "Age", "value_read": "Fd", "reason": "?", "confidence": 0.3}],
        postprocessed=[{"row_number": 2, "column": "species", "original": "ll", "value": "Tannenmeise"}],
    )

    cells, pages = collect.collect("fake", "fake/model", "low", tmp_path / "pred", [STEM, "scan_x_p002"])

    assert [p["status"] for p in pages] == ["ok", "missing"]
    assert pages[0]["cost"] == 0.02
    by = {(c["row"], c["column"]): c for c in cells}
    assert (2, "sex") not in by  # empty on both sides -> left out
    assert by[1, "sex"]["predicted"] == "f"
    assert by[3, "age"]["flagged"] == 1
    assert by[2, "species"]["ref_ditto"] == collect.DITTO
    assert by[2, "species"]["pred_ditto"] == collect.DITTO
    assert by[2, "species"]["reference"] == "Tannenmeise"


def test_collect_resolves_dittos_of_old_outputs_in_memory(ref_dir, tmp_path):
    pred = [dict(r) for r in GOOD]
    pred[1]["species"] = "ll"  # output from before the post-processing step
    pred[2]["age"] = "ad"
    write_pred(tmp_path / "pred", pred)
    before = (tmp_path / "pred" / "scan_x" / "processed" / "p001.json").read_text("utf-8")

    cells, _ = collect.collect("old", "", "", tmp_path / "pred", [STEM])

    cell = next(c for c in cells if (c["row"], c["column"]) == (2, "species"))
    assert (cell["predicted"], cell["pred_ditto"]) == ("Tannenmeise", collect.DITTO)
    age = next(c for c in cells if (c["row"], c["column"]) == (3, "age"))
    assert (age["predicted"], age["pred_ditto"]) == ("Fnd", "")  # converted, but not a ditto mark
    assert (tmp_path / "pred" / "scan_x" / "processed" / "p001.json").read_text("utf-8") == before


@pytest.fixture
def results(ref_dir, tmp_path):
    """A results folder with a perfect run and a run with one flagged and one unflagged error."""
    out = tmp_path / "results"
    write_pred(tmp_path / "good", GOOD, cost=0.01)
    bad = [dict(r) for r in GOOD]
    bad[1]["age"] = "F"
    bad[2]["weight_g"] = "2"
    write_pred(
        tmp_path / "bad",
        bad,
        uncertainties=[
            {"row_number": 2, "column": "Age", "value_read": "F", "reason": "?", "confidence": 0.3},
            {"row_number": 1, "column": "Poids", "value_read": "9", "reason": "?", "confidence": 0.3},
        ],
        cost=0.04,
    )
    collect.run_benchmark(out, [STEM], [], [("good", tmp_path / "good"), ("bad", tmp_path / "bad")])
    return out


def test_report_metrics(results):
    runs = {r.label: r for r in report.load([results])}
    good, bad = report.summary(runs["good"]), report.summary(runs["bad"])

    assert good["score"] == 1 and good["errors"] == 0
    assert good["cost_per_100_pages"] == pytest.approx(1.0)
    assert math.isnan(good["flag_precision"])  # no flags at all

    assert bad["errors"] == 2
    assert bad["F"] == 2
    assert bad["flag_precision"] == 0.5 and bad["flag_recall"] == 0.5
    assert bad["flag_f2"] == pytest.approx(0.5)
    n = bad["cells"]
    assert bad["error_pct"] == pytest.approx(200 / n)
    assert bad["cost_per_100_correct_cells"] == pytest.approx(0.04 / (n - 2) * 100)
    assert bad["flag_adjusted_score"] > bad["score"] - 0.25 / n  # +0.5 on a wrong cell, -0.25 on a correct one

    age = report.column_stats(runs["bad"], "age")
    assert (age["errors"], age["flag_recall"]) == (1, 1)


def test_manual_price_is_used_when_cost_is_zero(ref_dir, tmp_path, monkeypatch):
    from benchmark import prices

    monkeypatch.setitem(prices.PRICES, "fake/model", (1.0, 2.0))
    write_pred(tmp_path / "new", GOOD, cost=0.0)  # 100 input + 50 output tokens
    write_pred(tmp_path / "known", GOOD, cost=0.5)
    out = tmp_path / "res"
    collect.run_benchmark(out, [STEM], [], [("new", tmp_path / "new"), ("known", tmp_path / "known")])

    runs = {r.label: r for r in report.load([out])}
    assert runs["new"].cost == pytest.approx((100 * 1.0 + 50 * 2.0) / 1e6)
    assert runs["known"].cost == 0.5  # a recorded cost always wins


def test_report_filter_and_hard_cells(results):
    runs = report.load([results], only=["bad"])
    assert [r.label for r in runs] == ["bad"]

    both = report.load([results])
    assert report.hard_cells(both, 1.0) == []  # nothing is wrong in every run
    hard = report.hard_cells(both, 0.5)
    assert [(h["row"], h["column"], h["wrong"], h["runs"]) for h in hard] == [(2, "age", 1, 2), (3, "weight_g", 1, 2)]
    assert hard[0]["predicted"] == "bad: F"


def test_report_command_writes_files(results, tmp_path):
    out = tmp_path / "rep"
    main(["report", str(results), "--cells", "--min-share", "0.5", "--out", str(out)])

    for name in ("report.md", "summary.csv", "columns.csv", "columns.png", "hard_cells.csv", "cells.png"):
        assert (out / name).is_file(), name
    text = (out / "report.md").read_text("utf-8")
    assert "| good |" in text and "| bad |" in text
    with open(out / "summary.csv", encoding="utf-8", newline="") as f:
        assert [r["run"] for r in csv.DictReader(f)] == ["bad", "good"]  # folder order


def test_page_diff_marks_every_cell(results):
    runs = {r.label: r for r in report.load([results])}
    report.write_report(list(runs.values()), results)

    text = (results / "external" / "bad" / "diff" / f"{STEM}.md").read_text("utf-8")
    rows = {line.split(" | ")[0]: line for line in text.splitlines() if line.startswith("| ") and line[2].isdigit()}
    assert "❌🚩 F → Fd" in rows["| 2"]  # flagged error
    assert "✅🚩 9 |" in rows["| 1"]  # false alarm
    assert "❌ 2 → 20" in rows["| 3"]  # unflagged error
    assert "✅🔁 Tannenmeise" in rows["| 2"]  # reference species was a ditto mark
    assert "Score " in text and "(1 on correct cells)" in text
    assert (results / "external" / "good" / "diff" / f"{STEM}.md").is_file()


def test_same_label_in_two_folders_is_kept_apart(results, tmp_path):
    copy = tmp_path / "again"
    shutil.copytree(results, copy)
    labels = [r.label for r in report.load([results, copy])]
    assert labels == ["bad", "good", "bad [again]", "good [again]"]


def test_run_command_collects_models_into_a_temporary_folder(ref_dir, tmp_path):
    def fake_run_model(model, effort, refs, shared_root, run_root):
        write_pred(run_root, GOOD)

    out = tmp_path / "res"
    with (
        patch.object(collect, "prepare_crops") as crops,
        patch.object(collect, "run_model", side_effect=fake_run_model),
    ):
        main(["run", "--refs", STEM, "--model", "fake/model:low", "--out", str(out)])

    crops.assert_called_once()
    assert sorted(p.name for p in out.iterdir()) == ["columns.csv", "columns.png", "fake", "report.md", "summary.csv"]
    run_dir = out / "fake" / "model" / "low"
    assert sorted(p.name for p in run_dir.iterdir()) == ["cells.csv", "diff", "pages.csv", "scan_x"]
    assert (run_dir / "scan_x" / "processed" / "p001.json").is_file()  # LLM output is kept
    assert (run_dir / "diff" / f"{STEM}.md").is_file()
    assert [r.label for r in report.load([out])] == ["fake/model:low"]

    with pytest.raises(SystemExit, match="not empty"):
        main(["run", "--refs", STEM, "--model", "fake/model:low", "--out", str(out)])


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="pdftoppm (poppler-utils) not installed")
def test_prepare_crops_crops_referenced_pages_once(tmp_path):
    from backend.pipeline.steps import ImgExtractor

    stem = "scan_1972_CdB_1_20231125160539_p001"
    collect.prepare_crops([stem], tmp_path)
    pdf_dir = tmp_path / stem.rsplit("_p", 1)[0]
    assert (pdf_dir / "cropped" / "p001.geom.json").is_file()
    assert sorted(p.name for p in (pdf_dir / "raw").iterdir()) == ["p001.jpg"]

    with patch.object(ImgExtractor, "execute", side_effect=AssertionError("re-rendered")):
        collect.prepare_crops([stem], tmp_path)


def test_report_uses_the_current_reference(results, ref_dir):
    before = {r.label: report.summary(r) for r in report.load([results])}
    assert before["good"]["errors"] == 0

    path = ref_dir / f"{STEM}.csv"
    path.write_text(path.read_text("utf-8-sig").replace(";69;20;", ";69;21;"), encoding="utf-8-sig")

    after = {r.label: report.summary(r) for r in report.load([results])}
    assert after["good"]["errors"] == 1  # scored against the edited reference, no new collect needed
