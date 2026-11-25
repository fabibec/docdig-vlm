"""Collect raw benchmark data: run the LLM (+ post-processing) per model and store what it read, unscored.

Per run folder (``<provider>/<model>/<effort>/``): ``cells.csv`` (one line per cell: reference, prediction,
flag, ditto marker), ``pages.csv`` (cost and tokens per page) and the LLM output ``<pdf>/processed/``.
Scores are computed later by ``report.py``, so scoring rules can change without new LLM runs.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import cast, get_args

from backend.pipeline.directories import DATA_DIR, PROJECT_ROOT
from backend.pipeline.settings import ReasoningEffort
from backend.pipeline.steps.llm_postprocessor import postprocess_rows
from benchmark.scoring import COLUMNS, column_key, load_reference

REF_DIR = DATA_DIR / "dataset" / "output"
PDF_DIR = DATA_DIR / "dataset" / "pdfs"
CROP_CACHE = PROJECT_ROOT / "benchmark" / "cache"

DITTO = "🔁"
CELL_FIELDS = ["run", "page", "row", "column", "reference", "predicted", "flagged", "ref_ditto", "pred_ditto"]
PAGE_FIELDS = ["run", "model", "effort", "page", "status", "cost", "input_tokens", "output_tokens", "flags"]


def pages_by_pdf(refs: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for stem in refs:
        pdf_stem, page = stem.rsplit("_p", 1)
        out.setdefault(pdf_stem, []).append(int(page))
    return out


def prepare_crops(refs: list[str], shared_root: Path) -> None:
    """Run image + table steps once per PDF (only the referenced pages), reused by all models."""
    from backend.pipeline import PipelineTask
    from backend.pipeline.steps import ImgExtractor, TableExtractor

    todo = []
    for pdf_stem, pages in pages_by_pdf(refs).items():
        task = PipelineTask(input_pdf=PDF_DIR / f"{pdf_stem}.pdf", output_directory=shared_root / pdf_stem, pages=pages)
        if set(pages) - set(task.page_nums("cropped")):
            todo.append(task)
    if not todo:
        return
    steps = [ImgExtractor(), TableExtractor()]
    for s in steps:
        s.setup()
    try:
        for task in todo:
            print(f"[crop] {task.input_pdf.name} pages {task.pages}")
            task.output_directory.mkdir(parents=True, exist_ok=True)
            for s in steps:
                s.execute(task)
    finally:
        for s in steps:
            s.close()


def run_model(model: str, effort: str, refs: list[str], shared_root: Path, run_root: Path) -> None:
    """Run the LLM step + post-processing for one (model, effort) on the shared crops."""
    from backend.pipeline import PipelineTask
    from backend.pipeline.steps import LlmExtractor, LlmPostprocessor

    ext = LlmExtractor()
    ext.setup()
    ext.model = model
    ext.provider_id, ext.model_ref = model.split("/", 1)
    if effort not in get_args(ReasoningEffort):
        raise SystemExit(f"unknown reasoning effort {effort!r} for {model}, choose from {get_args(ReasoningEffort)}")
    ext.reasoning_effort = cast(ReasoningEffort, effort)
    post = LlmPostprocessor()
    try:
        for pdf_stem, pages in pages_by_pdf(refs).items():
            out = run_root / pdf_stem
            src = shared_root / pdf_stem / "cropped"
            if not src.is_dir():
                print(f"[skip] no crops for {pdf_stem}")
                continue
            # share crops via symlink so the LLM step finds <out>/cropped
            out.mkdir(parents=True, exist_ok=True)
            link = out / "cropped"
            if not link.exists():
                link.symlink_to(src.resolve(), target_is_directory=True)
            print(f"[llm] {model}:{effort} {pdf_stem} pages {pages}")
            task = PipelineTask(input_pdf=PDF_DIR / f"{pdf_stem}.pdf", output_directory=out, pages=pages)
            ext.execute(task)
            post.execute(task)
    finally:
        ext.close()


def load_prediction(run_root: Path, stem: str) -> tuple[list[dict[str, str]], dict | None]:
    """Rows (by position) and the raw ExtractionResult JSON of one page, ([], None) if missing."""
    pdf_stem, page = stem.rsplit("_p", 1)
    base = run_root / pdf_stem / "processed" / f"p{int(page):03d}"
    if not base.with_suffix(".json").exists():
        return [], None
    meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    rows = [{c: (r.get(c) or "").strip() for c in COLUMNS} for r in (meta.get("table") or {}).get("rows") or []]
    return rows, meta


def row_positions(meta: dict) -> dict[int, int]:
    """LLM ``row_number`` -> 0-based position (flags and fixes refer to row numbers)."""
    rows = (meta.get("table") or {}).get("rows") or []
    return {r.get("row_number"): i for i, r in enumerate(rows) if r.get("row_number") is not None}


def flagged_cells(meta: dict) -> tuple[set[tuple[int, str]], int]:
    """(position, column) cells the LLM flagged as uncertain, plus the total number of uncertainties."""
    pos = row_positions(meta)
    unc = (meta.get("table") or {}).get("uncertainties") or []
    cells = set()
    for u in unc:
        key = column_key(u.get("column") or "")
        n = u.get("row_number")
        if n and key:
            cells.add((pos.get(n, n - 1), key))
    return cells, len(unc)


def ditto_cells(meta: dict) -> set[tuple[int, str]]:
    pos = row_positions(meta)
    return {
        (pos.get(f["row_number"], f["row_number"] - 1), f["column"])
        for f in meta.get("postprocessed") or []
        if f.get("rule", "ditto") == "ditto"
    }


def collect(label: str, model: str, effort: str, run_root: Path, refs: list[str]) -> tuple[list[dict], list[dict]]:
    """Cell lines and page lines for one run. Cells empty on both sides are left out."""
    cells, pages = [], []
    for stem in refs:
        pred_rows, meta = load_prediction(run_root, stem)
        if meta is None:
            pages.append({"run": label, "model": model, "effort": effort, "page": stem, "status": "missing"})
            continue
        ref = load_reference(REF_DIR / f"{stem}.csv")
        flagged, n_flags = flagged_cells(meta)
        # outputs from before the post-processing step (e.g. --run on old folders): resolve here, files untouched
        fixes = postprocess_rows(pred_rows)
        dittos = ditto_cells(meta) | {(i, c) for i, c, _, _, rule in fixes if rule == "ditto"}
        pages.append(
            {
                "run": label,
                "model": meta.get("source") or model,
                "effort": effort,
                "page": stem,
                "status": "ok",
                "cost": meta.get("estimated_cost") or 0.0,
                "input_tokens": meta.get("input_tokens") or 0,
                "output_tokens": meta.get("output_tokens") or 0,
                "flags": n_flags,
            }
        )
        for i in range(max(len(ref.rows), len(pred_rows))):
            r = ref.rows[i] if i < len(ref.rows) else {}
            p = pred_rows[i] if i < len(pred_rows) else {}
            for c in COLUMNS:
                rv, pv = r.get(c, ""), p.get(c, "")
                if not rv and not pv and (i, c) not in flagged:  # flags kept: the reference may change later
                    continue
                cells.append(
                    {
                        "run": label,
                        "page": stem,
                        "row": i + 1,
                        "column": c,
                        "reference": rv,
                        "predicted": pv,
                        "flagged": int((i, c) in flagged),
                        "ref_ditto": DITTO if (i, c) in ref.dittos else "",
                        "pred_ditto": DITTO if (i, c) in dittos else "",
                    }
                )
    return cells, pages


def write_csv(path: Path, fields: list[str], lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(lines)


def model_dir(model: str, effort: str) -> Path:
    """``provider/model:effort`` -> ``provider/model/effort`` (relative folder of one run)."""
    provider, name = model.split("/", 1)
    return Path(provider) / name.replace("/", "_") / effort


def run_benchmark(out: Path, refs: list[str], models: list[tuple[str, str]], external: list[tuple[str, Path]]) -> None:
    """Run and collect every model into ``out/<provider>/<model>/<effort>/``, external outputs into
    ``out/external/<label>/``. Each folder gets ``cells.csv`` + ``pages.csv``; model folders also keep the
    LLM output (``<pdf>/processed/``), written as it arrives so a crash doesn't lose paid calls."""
    if models:
        prepare_crops(refs, CROP_CACHE)
    for model, effort in models:
        run_dir = out / model_dir(model, effort)
        try:
            run_model(model, effort, refs, CROP_CACHE, run_dir)
        finally:
            for link in run_dir.glob("*/cropped"):  # shared crops, only needed by the LLM step
                if link.is_symlink():
                    link.unlink()
        cells, pages = collect(f"{model}:{effort}", model, effort, run_dir, refs)
        write_csv(run_dir / "cells.csv", CELL_FIELDS, cells)
        write_csv(run_dir / "pages.csv", PAGE_FIELDS, pages)
    for label, root in external:
        cells, pages = collect(label, "", "", root, refs)
        write_csv(out / "external" / label / "cells.csv", CELL_FIELDS, cells)
        write_csv(out / "external" / label / "pages.csv", PAGE_FIELDS, pages)
