"""Benchmark pipeline CSV outputs against hand-made reference CSVs.

Reference: ``data/dataset/output/<pdf_stem>_pNNN.csv`` (``;``-separated, only filled rows).
Prediction: ``<run_root>/<pdf_stem>/processed/pNNN.csv`` (+ ``pNNN.json`` for cost).
PDFs:      ``data/dataset/pdfs/<pdf_stem>.pdf``

Usage (run the LLM step for each model/effort, then score; MODELS below is the default list):
    uv run -m benchmark.run_benchmark \
        --refs scan_1972_sample_p001 scan_1972_sample_p002 \
        --model openai/gpt-6-luna:low --model openai/gpt-6-luna:high

Score existing outputs only:
    uv run -m benchmark.run_benchmark --run luna=data/output --fix-cost 0.05

Image/crop steps run once per PDF (cached in ``benchmark/cache``). Every invocation writes to
``benchmark/results/<YYYY-MM-DD_HH-MM-SS>/``:
    report.md, report.csv                         overall comparison
    <provider>/<model>/<effort>/<pdf>/processed/  raw LLM outputs
    <provider>/<model>/<effort>/diff/<page>.md    marked tables (✅ / ⚠️ / ❌ predicted → reference)
    <provider>/<model>/<effort>/diff/errors.csv   one line per wrong cell
"""

import argparse
import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from backend.pipeline.directories import DATA_DIR, PROJECT_ROOT

REF_DIR = DATA_DIR / "dataset" / "output"
PDF_DIR = DATA_DIR / "dataset" / "pdfs"
BENCH_DIR = PROJECT_ROOT / "benchmark"
RESULTS_DIR = BENCH_DIR / "results"
CROP_CACHE = BENCH_DIR / "cache"

# (model name, reasoning effort) pairs benchmarked when no --model/--run is given.
MODELS: list[tuple[str, str]] = [
    ("openai/gpt-6-luna", "low"),
    ("openai/gpt-6-luna", "medium"),
]

# Reference columns in order (by position) -> pipeline keys.
COLUMNS = [
    "ring_no",
    "species",
    "sex",
    "age",
    "date",
    "time",
    "wing_length_mm",
    "weight_g",
    "additional_remark",
]
FUZZY_COLUMNS = {"species", "additional_remark"}  # partial credit via Levenshtein
WEIGHTS = dict.fromkeys(COLUMNS, 1.0) | {"species": 3.0}
FORWARD_FILL = ("date", "time")  # ring prefix handled separately
N_ROWS = 50

# Credit for a wrong cell the LLM flagged as uncertain: it gets FLAG_CREDIT of the missing score
# (flag-adjusted score) and its human fix cost shrinks by the same factor (no searching needed).
FLAG_CREDIT = 0.5
# Substrings (casefolded) to map the free-text `column` of an uncertainty to a column key.
COLUMN_ALIASES = {
    "ring_no": ("bague", "ring"),
    "species": ("esp", "species", "art"),
    "sex": ("sex", "geschlecht"),
    "age": ("age", "alter"),
    "date": ("jour", "mois", "date", "datum"),
    "time": ("heure", "time", "zeit"),
    "wing_length_mm": ("aile", "alle", "wing", "flügel"),
    "weight_g": ("poids", "weight", "gewicht"),
    "additional_remark": ("blanc", "remark", "bemerk"),
}


# loading


def load_reference(stem: str) -> list[dict[str, str]]:
    with open(REF_DIR / f"{stem}.csv", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)  # header
        rows = [dict(zip(COLUMNS, (c.strip() for c in r), strict=False)) for r in reader if any(c.strip() for c in r)]
    rows = [{c: r.get(c, "") for c in COLUMNS} for r in rows]
    return forward_fill(rows)


def forward_fill(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Mirror the pipeline's carry-over rules: date/time carry down, ring prefix carries down."""
    last = dict.fromkeys(FORWARD_FILL, "")
    last_ring = ""
    for r in rows:
        for c in FORWARD_FILL:
            if r[c]:
                last[c] = r[c]
            else:
                r[c] = last[c]
        ring = r["ring_no"]
        if ring and last_ring and len(ring) < len(last_ring):
            # shorter entry only overwrites the trailing part of the previous ring
            r["ring_no"] = last_ring[: len(last_ring) - len(ring)] + ring
        if r["ring_no"]:
            last_ring = r["ring_no"]
    return rows


def load_prediction(run_root: Path, stem: str) -> tuple[list[dict[str, str]], dict | None]:
    pdf_stem, page = stem.rsplit("_p", 1)
    base = run_root / pdf_stem / "processed" / f"p{int(page):03d}"
    rows: list[dict[str, str]] = []
    csv_path = base.with_suffix(".csv")
    if csv_path.exists():
        with open(csv_path, encoding="utf-8", newline="") as f:
            rows = [{c: (r.get(c) or "").strip() for c in COLUMNS} for r in csv.DictReader(f)]
    meta = None
    if base.with_suffix(".json").exists():
        meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    return rows, meta


# ---------------------------------------------------------------- scoring


def normalize(col: str, v: str) -> str:
    v = re.sub(r"\s+", " ", v.strip()).casefold()
    if col in ("wing_length_mm", "weight_g"):
        v = v.replace(",", ".")
        if re.fullmatch(r"\d+\.0+", v):
            v = v.split(".")[0]
    if col == "date":
        v = v.rstrip(".")
    if col == "time" and re.fullmatch(r"\d:\d\d", v):
        v = "0" + v
    return v


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cell_score(col: str, ref: str, pred: str) -> float | None:
    """1 = correct, 0 = wrong, None = both empty (not counted)."""
    ref, pred = normalize(col, ref), normalize(col, pred)
    if not ref and not pred:
        return None
    if not ref or not pred:
        return 0.0
    if col in FUZZY_COLUMNS:
        return max(0.0, 1 - levenshtein(ref, pred) / max(len(ref), len(pred)))
    return float(ref == pred)


@dataclass
class ColStats:
    score: float = 0.0
    flag_score: float = 0.0  # score with credit for flagged errors
    count: int = 0
    hallucinated: int = 0
    missed: int = 0
    errors: float = 0.0  # sum of (1 - score)
    flagged_errors: float = 0.0  # part of `errors` the LLM flagged as uncertain
    flags: int = 0  # flags on counted cells (incl. false alarms)

    @property
    def mean(self) -> float:
        return self.score / self.count if self.count else float("nan")

    @property
    def flag_mean(self) -> float:
        return self.flag_score / self.count if self.count else float("nan")


@dataclass
class RunResult:
    label: str
    model: str = "?"
    cols: dict[str, ColStats] = field(default_factory=lambda: {c: ColStats() for c in COLUMNS})
    pages: dict[str, float] = field(default_factory=dict)
    cost: float = 0.0
    tokens: int = 0
    n_pages: int = 0
    missing_pages: list[str] = field(default_factory=list)
    flags_total: int = 0  # all uncertainties the LLM reported (incl. unmappable ones)

    def _weighted(self, attr: str) -> float:
        num = sum(WEIGHTS[c] * getattr(s, attr) for c, s in self.cols.items())
        den = sum(WEIGHTS[c] * s.count for c, s in self.cols.items())
        return num / den if den else float("nan")

    @property
    def weighted_score(self) -> float:
        return self._weighted("score")

    @property
    def flag_adjusted_score(self) -> float:
        return self._weighted("flag_score")

    @property
    def macro_score(self) -> float:
        vals = [s.mean for s in self.cols.values() if s.count]
        return sum(vals) / len(vals) if vals else float("nan")

    @property
    def errors(self) -> float:
        """Expected number of cells a human has to fix (fractional for fuzzy columns)."""
        return sum(s.errors for s in self.cols.values())

    @property
    def flagged_errors(self) -> float:
        return sum(s.flagged_errors for s in self.cols.values())

    @property
    def flags(self) -> int:
        return sum(s.flags for s in self.cols.values())

    @property
    def flag_recall(self) -> float:
        """Share of errors the LLM flagged."""
        return self.flagged_errors / self.errors if self.errors else float("nan")

    @property
    def flag_precision(self) -> float:
        """Share of flags that were on an actual error."""
        return self.flagged_errors / self.flags if self.flags else float("nan")

    @property
    def correct(self) -> float:
        return sum(s.score for s in self.cols.values())

    def review_cost(self, fix_cost: float, check_cost: float) -> float:
        """Human cost: every flag is checked; flagged errors are cheaper to fix (no searching)."""
        unflagged = self.errors - self.flagged_errors
        return self.flags * check_cost + self.flagged_errors * fix_cost * (1 - FLAG_CREDIT) + unflagged * fix_cost


def column_key(name: str) -> str | None:
    """Map the free-text column of an uncertainty (e.g. 'Espèce', 'species') to a column key."""
    n = name.casefold()
    for key, aliases in COLUMN_ALIASES.items():
        if n == key or any(a in n for a in aliases):
            return key
    return None


def load_flags(meta: dict | None) -> tuple[set[tuple[int, str]], int]:
    """(row, column) cells the LLM flagged as uncertain, plus total number of uncertainties."""
    if not meta:
        return set(), 0
    unc = (meta.get("table") or {}).get("uncertainties") or []
    cells = set()
    for u in unc:
        key = column_key(u.get("column") or "")
        if u.get("row_number") and key:
            cells.add((int(u["row_number"]), key))
    return cells, len(unc)


def evaluate(label: str, run_root: Path, refs: list[str], diff_dir: Path | None = None) -> RunResult:
    """Score all reference pages. If ``diff_dir`` is set, write marked-up diffs for human review."""
    res = RunResult(label)
    errors: list[dict] = []
    for stem in refs:
        ref_rows = load_reference(stem)
        pred_rows, meta = load_prediction(run_root, stem)
        if not pred_rows:
            res.missing_pages.append(stem)
            continue
        if meta:
            res.model = meta.get("source", res.model)
            res.cost += meta.get("estimated_cost") or 0.0
            res.tokens += (meta.get("input_tokens") or 0) + (meta.get("output_tokens") or 0)
        res.n_pages += 1
        flagged, n_flags = load_flags(meta)
        res.flags_total += n_flags
        empty = dict.fromkeys(COLUMNS, "")
        p_num = p_den = 0.0
        grid: list[list[str]] = []
        for i in range(max(N_ROWS, len(ref_rows), len(pred_rows))):
            ref = ref_rows[i] if i < len(ref_rows) else empty
            pred = pred_rows[i] if i < len(pred_rows) else empty
            line = [str(i + 1)]
            for c in COLUMNS:
                s = cell_score(c, ref[c], pred[c])
                is_flagged = (i + 1, c) in flagged
                line.append(mark(ref[c], pred[c], s, is_flagged))
                if s is None:
                    continue
                st = res.cols[c]
                st.score += s
                st.count += 1
                st.errors += 1 - s
                if is_flagged:
                    st.flags += 1
                    st.flagged_errors += 1 - s
                    st.flag_score += s + (1 - s) * FLAG_CREDIT
                else:
                    st.flag_score += s
                kind = "ok"
                if not normalize(c, ref[c]):
                    st.hallucinated += 1
                    kind = "hallucinated"
                elif not normalize(c, pred[c]):
                    st.missed += 1
                    kind = "missed"
                elif s < 1:
                    kind = "partial" if s > 0 else "wrong"
                if kind != "ok" or is_flagged:
                    errors.append(
                        {
                            "page": stem,
                            "row": i + 1,
                            "column": c,
                            "kind": kind,
                            "flagged": is_flagged,
                            "reference": ref[c],
                            "predicted": pred[c],
                            "score": round(s, 3),
                        }
                    )
                p_num += WEIGHTS[c] * s
                p_den += WEIGHTS[c]
            grid.append(line)
        res.pages[stem] = p_num / p_den if p_den else float("nan")
        if diff_dir:
            write_page_diff(diff_dir / f"{stem}.md", stem, grid, res.pages[stem])
    if diff_dir:
        diff_dir.mkdir(parents=True, exist_ok=True)
        with open(diff_dir / "errors.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f, fieldnames=["page", "row", "column", "kind", "flagged", "reference", "predicted", "score"]
            )
            w.writeheader()
            w.writerows(errors)
    return res


def mark(ref: str, pred: str, s: float | None, flagged: bool = False) -> str:
    """Cell markup: ✅ correct, ⚠️ partial, ❌ wrong (pred → ref), 🚩 = LLM flagged it, blank = both empty."""

    def esc(v):
        return v.replace("|", "\\|") or "∅"

    if s is None:
        return ""
    flag = "🚩" if flagged else ""
    if s == 1:
        return f"✅{flag} {esc(pred)}"
    if s > 0:
        return f"⚠️{flag} {esc(pred)} → {esc(ref)}"
    return f"❌{flag} {esc(pred)} → {esc(ref)}"


def write_page_diff(path: Path, stem: str, grid: list[list[str]], score: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = ["row", *COLUMNS]
    lines = [
        f"# {stem} — weighted score {score:.3f}",
        "",
        "✅ correct · ⚠️ partial (predicted → reference) · ❌ wrong (predicted → reference) · "
        "🚩 flagged as uncertain by the LLM · ∅ empty",
        "",
        "| " + " | ".join(head) + " |",
        "|" + "---|" * len(head),
    ]
    lines += ["| " + " | ".join(r) + " |" for r in grid if any(r[1:])]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# reporting


def report(results: list[RunResult], fix_cost: float, check_cost: float, out_dir: Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Benchmark report",
        "",
        f"Fix cost per wrong cell: ${fix_cost:.3f} · check cost per flag: ${check_cost:.3f} · "
        f"flag credit: {FLAG_CREDIT:g} · species weight: {WEIGHTS['species']}x",
        "",
    ]

    lines += [
        "## Summary",
        "",
        "| Run | Model | Pages | Weighted score | Flag-adjusted score | Macro score | Flag recall | Flag precision "
        "| Cost $ | $/page | $/correct cell | Errors/page | Review-adjusted $/page |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    summary_rows = []
    for r in results:
        n = max(r.n_pages, 1)
        per_correct = r.cost / r.correct if r.correct else float("nan")
        adjusted = (r.cost + r.review_cost(fix_cost, check_cost)) / n
        lines.append(
            f"| {r.label} | {r.model} | {r.n_pages} | {r.weighted_score:.3f} | {r.flag_adjusted_score:.3f} | "
            f"{r.macro_score:.3f} | {r.flag_recall:.2f} | {r.flag_precision:.2f} | "
            f"{r.cost:.4f} | {r.cost / n:.4f} | {per_correct:.6f} | {r.errors / n:.1f} | {adjusted:.3f} |"
        )
        summary_rows.append(
            {
                "run": r.label,
                "model": r.model,
                "pages": r.n_pages,
                "weighted_score": r.weighted_score,
                "flag_adjusted_score": r.flag_adjusted_score,
                "macro_score": r.macro_score,
                "flag_recall": r.flag_recall,
                "flag_precision": r.flag_precision,
                "flags_total": r.flags_total,
                "cost": r.cost,
                "cost_per_page": r.cost / n,
                "cost_per_correct_cell": per_correct,
                "errors_per_page": r.errors / n,
                "review_adjusted_cost_per_page": adjusted,
                **{f"col_{c}": s.mean for c, s in r.cols.items()},
            }
        )
    lines += [
        "",
        "Flag recall = share of errors the LLM flagged as uncertain · flag precision = share of flags "
        "that were real errors · flag-adjusted score gives flagged errors "
        f"{FLAG_CREDIT:g} of the missing credit · review-adjusted $/page = LLM cost + flags × check cost + "
        "flagged errors × fix cost × (1 − flag credit) + unflagged errors × fix cost.",
    ]

    lines += [
        "",
        "## Column scores",
        "",
        "| Column | Weight | " + " | ".join(r.label for r in results) + " |",
        "|---|---|" + "---|" * len(results),
    ]
    for c in COLUMNS:
        cells = [
            f"{r.cols[c].mean:.3f} / {r.cols[c].flag_mean:.3f} (n={r.cols[c].count}, H={r.cols[c].hallucinated}, "
            f"M={r.cols[c].missed}, F={r.cols[c].flags})"
            for r in results
        ]
        lines.append(f"| {c} | {WEIGHTS[c]:g} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "score / flag-adjusted score · H = hallucinated (reference empty), M = missed (prediction empty), "
        "F = flagged cells. Cells empty in both are ignored.",
    ]

    lines += [
        "",
        "## Page scores (weighted)",
        "",
        "| Page | " + " | ".join(r.label for r in results) + " |",
        "|---|" + "---|" * len(results),
    ]
    for stem in sorted({p for r in results for p in r.pages}):
        lines.append(
            f"| {stem} | " + " | ".join(f"{r.pages[stem]:.3f}" if stem in r.pages else "–" for r in results) + " |"
        )

    for r in results:
        if r.missing_pages:
            lines += ["", f"**{r.label}: missing predictions for** {', '.join(r.missing_pages)}"]

    text = "\n".join(lines) + "\n"
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    if summary_rows:
        with open(out_dir / "report.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=summary_rows[0].keys())
            w.writeheader()
            w.writerows(summary_rows)
    return text


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
    """Run only the LLM step for one (model, effort) on the shared crops."""
    from backend.pipeline import PipelineTask
    from backend.pipeline.steps import LlmExtractor

    ext = LlmExtractor()
    ext.setup()
    ext.model = model
    ext.provider_id, ext.model_ref = model.split("/", 1)
    ext.reasoning_effort = effort
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
            ext.execute(PipelineTask(input_pdf=PDF_DIR / f"{pdf_stem}.pdf", output_directory=out, pages=pages))
    finally:
        ext.close()


def model_dir(model: str, effort: str) -> Path:
    provider, name = model.split("/", 1)
    return Path(provider) / name.replace("/", "_") / effort


def main() -> None:
    global FLAG_CREDIT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refs", nargs="*", default=[], help="Reference stems, e.g. scan_1972_sample_p001")
    ap.add_argument("--refs-file", type=Path, help="Text file with one reference stem per line")
    ap.add_argument(
        "--model",
        action="append",
        default=[],
        help="provider/model:effort, e.g. openai/gpt-6-luna:low. Repeatable. Default: MODELS list",
    )
    ap.add_argument(
        "--run",
        action="append",
        default=[],
        help="label=ROOT of existing outputs (ROOT contains <pdf_stem>/processed/). Repeatable. "
        "If given without --model, no LLM calls are made.",
    )
    ap.add_argument("--fix-cost", type=float, default=0.05, help="Human cost ($) to fix one wrong cell")
    ap.add_argument("--check-cost", type=float, default=0.01, help="Human cost ($) to check one flagged cell")
    ap.add_argument(
        "--flag-credit",
        type=float,
        default=FLAG_CREDIT,
        help="Credit (0-1) for wrong cells the LLM flagged as uncertain",
    )
    ap.add_argument("--out", type=Path, default=None, help=f"Result dir (default: {RESULTS_DIR}/<timestamp>)")
    args = ap.parse_args()
    FLAG_CREDIT = args.flag_credit

    out = args.out or RESULTS_DIR / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    refs = list(args.refs)
    if args.refs_file:
        refs += [l.strip().removesuffix(".csv") for l in args.refs_file.read_text().splitlines() if l.strip()]
    if not refs:
        refs = sorted(p.stem for p in REF_DIR.glob("*.csv"))

    models = [tuple(m.rsplit(":", 1)) for m in args.model]
    if not models and not args.run:
        models = MODELS

    runs: list[tuple[str, Path, Path]] = []  # (label, prediction root, result dir)
    if models:
        prepare_crops(refs, CROP_CACHE)
        for model, effort in models:
            root = out / model_dir(model, effort)
            run_model(model, effort, refs, CROP_CACHE, root)
            runs.append((f"{model}:{effort}", root, root))
    for spec in args.run:
        label, _, root = spec.partition("=")
        runs.append((label, Path(root or label), out / "external" / label))

    results = [evaluate(label, root, refs, diff_dir=res_dir / "diff") for label, root, res_dir in runs]
    print(report(results, args.fix_cost, args.check_cost, out))
    print(f"Results: {out}")


if __name__ == "__main__":
    main()
