"""Score raw benchmark data (``cells.csv`` + ``pages.csv`` from ``collect.py``) and write the report.

Outputs (in the report folder):
    report.md       summary, per-column and per-page tables (+ hard cells with --cells)
    <provider>/<model>/<effort>/diff/<page>.md   every cell of a page marked ✅ / ⚠️ / ❌ / 🚩 / 🔁
    summary.csv     one line per run
    columns.csv     one line per run and column
    columns.png     error rate per column and run
    hard_cells.csv  (--cells) cells most runs get wrong: likely hard handwriting or labelling errors
    cells.png       (--cells) hard cells per page and column
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from benchmark.prices import page_cost
from benchmark.scoring import (
    COLUMNS,
    FLAG_CORRECT_PENALTY,
    FLAG_WRONG_CREDIT,
    SYSTEMATIC_MIN_ROWS,
    SYSTEMATIC_REPEAT_COST,
    WEIGHTS,
    Cell,
    load_reference,
    score_page,
)

# Categorical slots in fixed order (validated palette, light surface); a run keeps its slot.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SEQUENTIAL = ["#fcfcfb", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


@dataclass
class ScoredCell(Cell):
    run: str = ""
    page: str = ""
    row: int = 0
    ref_ditto: str = ""
    pred_ditto: str = ""


@dataclass
class Run:
    label: str
    model: str = ""
    cells: list[ScoredCell] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)  # pages with a prediction
    missing: list[str] = field(default_factory=list)
    cost: float = 0.0
    tokens: int = 0
    folder: Path | None = None  # where its cells.csv / pages.csv are
    rel: Path = Path()  # folder relative to the results dir, e.g. openai/gpt-6-luna/low

    def counted(self, column: str | None = None) -> list[ScoredCell]:
        return [c for c in self.cells if c.score is not None and (column is None or c.column == column)]


# ---------------------------------------------------------------- loading


def load(dirs: list[Path], only: list[str] | None = None) -> list[Run]:
    """Read and score all runs below ``dirs`` (every folder with a ``pages.csv``).

    ``only``: keep runs whose label contains one of these strings.
    """
    runs: dict[str, Run] = {}
    for d in dirs:
        for pages_csv in sorted(d.rglob("pages.csv")):
            folder = pages_csv.parent
            rename = {}
            with open(pages_csv, encoding="utf-8", newline="") as f:
                for p in csv.DictReader(f):
                    label = p["run"]
                    if label in runs and label not in rename and runs[label].folder != folder:
                        rename[label] = f"{label} [{d.name}]"  # same run in another results folder
                    label = rename.get(label, label)
                    if only and not any(o in label for o in only):
                        continue
                    run = runs.setdefault(label, Run(label, model=p["model"], folder=folder, rel=folder.relative_to(d)))
                    if p["status"] != "ok":
                        run.missing.append(p["page"])
                        continue
                    run.pages.append(p["page"])
                    tokens_in, tokens_out = int(p["input_tokens"] or 0), int(p["output_tokens"] or 0)
                    run.cost += page_cost(p["model"], float(p["cost"] or 0), tokens_in, tokens_out)
                    run.tokens += tokens_in + tokens_out
            by_page: dict[tuple[str, str], list[ScoredCell]] = defaultdict(list)
            with open(folder / "cells.csv", encoding="utf-8", newline="") as f:
                for c in csv.DictReader(f):
                    label = rename.get(c["run"], c["run"])
                    if label not in runs:
                        continue
                    by_page[label, c["page"]].append(
                        ScoredCell(
                            column=c["column"],
                            reference=c["reference"],
                            predicted=c["predicted"],
                            flagged=c["flagged"] == "1",
                            run=label,
                            page=c["page"],
                            row=int(c["row"]),
                            ref_ditto=c["ref_ditto"],
                            pred_ditto=c["pred_ditto"],
                        )
                    )
            for (label, page), cells in by_page.items():
                cells = with_current_reference(cells, label, page)
                cells.sort(key=lambda c: (c.row, COLUMNS.index(c.column)))
                runs[label].cells += score_page(cells)
    return list(runs.values())


def with_current_reference(cells: list[ScoredCell], label: str, page: str) -> list[ScoredCell]:
    """Swap the reference stored at collection time for the current reference CSV (if it still exists).

    The ground truth is still being cleaned, so old results are always scored against today's reference.
    Cells left out of ``cells.csv`` were empty in the prediction and unflagged.
    """
    from benchmark.collect import REF_DIR

    path = REF_DIR / f"{page}.csv"
    if not path.exists():
        return cells
    ref = load_reference(path)
    stored = {(c.row, c.column): c for c in cells}
    n_rows = max(len(ref.rows), max((c.row for c in cells), default=0))
    out = []
    for row in range(1, n_rows + 1):
        for col in COLUMNS:
            value = ref.rows[row - 1][col] if row <= len(ref.rows) else ""
            old = stored.get((row, col))
            if not value and old is None:
                continue
            out.append(
                ScoredCell(
                    column=col,
                    reference=value,
                    predicted=old.predicted if old else "",
                    flagged=old.flagged if old else False,
                    run=label,
                    page=page,
                    row=row,
                    ref_ditto="🔁" if (row - 1, col) in ref.dittos else "",
                    pred_ditto=old.pred_ditto if old else "",
                )
            )
    return out


# ---------------------------------------------------------------- metrics


def weighted(cells: list[ScoredCell], attr: str = "score") -> float:
    num = sum(WEIGHTS[c.column] * getattr(c, attr) for c in cells)
    den = sum(WEIGHTS[c.column] for c in cells)
    return num / den if den else math.nan


def flag_metrics(cells: list[ScoredCell]) -> tuple[float, float, float]:
    """Precision, recall and F2 of flags as a detector of wrong cells (recall weighs more)."""
    from sklearn.metrics import precision_recall_fscore_support

    if not cells:
        return math.nan, math.nan, math.nan
    y_true = [c.is_error for c in cells]
    y_pred = [c.flagged for c in cells]
    p, r, f, _ = precision_recall_fscore_support(y_true, y_pred, beta=2, average="binary", zero_division=math.nan)
    return float(p), float(r), float(f)


def errors(cells: list[ScoredCell]) -> float:
    """Error units: sum of (1 - score). A fully wrong cell is 1, one wrong ring digit about 0.17."""
    return sum(1 - c.points for c in cells)


def kinds(cells: list[ScoredCell]) -> dict[str, int]:
    out = {"H": 0, "M": 0, "F": 0}
    for c in cells:
        out["H"] += c.kind == "hallucinated"
        out["M"] += c.kind == "missed"
        out["F"] += c.flagged
    return out


def summary(run: Run) -> dict:
    cells = run.counted()
    n = len(run.pages)
    err = errors(cells)
    correct = sum(c.points for c in cells)
    p, r, f2 = flag_metrics(cells)
    return {
        "run": run.label,
        "model": run.model,
        "pages": n,
        "missing_pages": len(run.missing),
        "cells": len(cells),
        "score": weighted(cells),
        "flag_adjusted_score": weighted(cells, "adjusted"),
        "flag_precision": p,
        "flag_recall": r,
        "flag_f2": f2,
        "errors": err,
        "errors_per_100_pages": err / n * 100 if n else math.nan,
        "error_pct": err / len(cells) * 100 if cells else math.nan,
        "cost": run.cost,
        "cost_per_100_pages": run.cost / n * 100 if n else math.nan,
        "cost_per_100_correct_cells": run.cost / correct * 100 if correct else math.nan,
        "tokens": run.tokens,
        **kinds(cells),
    }


def column_stats(run: Run, column: str) -> dict:
    cells = run.counted(column)
    p, r, f2 = flag_metrics(cells)
    err = errors(cells)
    return {
        "run": run.label,
        "column": column,
        "cells": len(cells),
        "score": sum(c.points for c in cells) / len(cells) if cells else math.nan,
        "errors": err,
        "error_pct": err / len(cells) * 100 if cells else math.nan,
        "flag_precision": p,
        "flag_recall": r,
        "flag_f2": f2,
        **kinds(cells),
    }


class HardCell(TypedDict):
    page: str
    row: int
    column: str
    reference: str
    wrong: int
    runs: int
    mean_error: float
    predicted: str


def hard_cells(runs: list[Run], min_share: float) -> list[HardCell]:
    """Cells wrong in at least ``min_share`` of the runs that have their page, most-missed and worst first."""
    runs_per_page: dict[str, int] = defaultdict(int)
    for run in runs:
        for page in run.pages:
            runs_per_page[page] += 1
    by_cell: dict[tuple[str, int, str], list[ScoredCell]] = defaultdict(list)
    for run in runs:
        for c in run.counted():
            by_cell[c.page, c.row, c.column].append(c)
    out: list[HardCell] = []
    for (page, row, column), cells in by_cell.items():
        wrong = [c for c in cells if c.is_error]
        total = runs_per_page[page]
        if not wrong or len(wrong) / total < min_share:
            continue
        ref = cells[0]
        out.append(
            HardCell(
                page=page,
                row=row,
                column=column,
                reference=f"{ref.ref_ditto} {ref.reference}".strip(),
                wrong=len(wrong),
                runs=total,
                mean_error=round(sum(1 - c.points for c in wrong) / len(wrong), 3),
                predicted=" | ".join(f"{c.run}: " + f"{c.pred_ditto} {c.predicted or '∅'}".strip() for c in wrong),
            )
        )
    # most runs wrong first, then the biggest errors (a repeated ring misread costs little per row)
    out.sort(key=lambda h: (-h["wrong"] / h["runs"], -h["mean_error"], h["page"], h["row"], COLUMNS.index(h["column"])))
    return out


# ---------------------------------------------------------------- output


def fmt(v, digits: int = 3) -> str:
    if isinstance(v, float):
        return "–" if math.isnan(v) else f"{v:.{digits}f}"
    return str(v)


def md_table(head: list[str], rows: list[list]) -> list[str]:
    return [
        "| " + " | ".join(head) + " |",
        "|" + "---|" * len(head),
        *("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows),
    ]


def write_csv(path: Path, lines: Sequence[Mapping[str, object]]) -> None:
    if not lines:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(lines[0]))
        w.writeheader()
        w.writerows(lines)


def _axes(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelcolor=INK_2, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_columns(cols: list[dict], labels: list[str], path: Path) -> None:
    """Grouped bars: error % per column, one colour per run (fixed slot order)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = labels[: len(SERIES)]  # more than 8 runs: filter with --model
    fig, ax = plt.subplots(figsize=(11, 4.5), facecolor=SURFACE)
    _axes(ax)
    width = 0.8 / max(len(labels), 1)
    by = {(c["run"], c["column"]): c["error_pct"] for c in cols}
    for k, label in enumerate(labels):
        xs = [i - 0.4 + width * (k + 0.5) for i in range(len(COLUMNS))]
        ys = [0 if math.isnan(by.get((label, c), math.nan)) else by[label, c] for c in COLUMNS]
        ax.bar(xs, ys, width=width * 0.9, color=SERIES[k], label=label, edgecolor=SURFACE, linewidth=1)
    ax.set_xticks(range(len(COLUMNS)), COLUMNS, rotation=20, ha="right")
    ax.set_ylabel("errors (% of cells)", color=INK_2)
    ax.set_title("Errors per column", color=INK, loc="left", fontsize=12)
    if len(labels) > 1:
        ax.legend(frameon=False, labelcolor=INK_2, fontsize=9, ncols=min(len(labels), 3), loc="upper right")
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def plot_hard_cells(hard: list[HardCell], pages: list[str], path: Path) -> None:
    """Heatmap: number of hard cells per page (rows) and column."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    grid = [[0] * len(COLUMNS) for _ in pages]
    for h in hard:
        if h["page"] in pages:
            grid[pages.index(h["page"])][COLUMNS.index(h["column"])] += 1
    fig, ax = plt.subplots(figsize=(10, 1.5 + 0.32 * len(pages)), facecolor=SURFACE)
    cmap = LinearSegmentedColormap.from_list("seq", SEQUENTIAL)
    img = ax.imshow(grid, cmap=cmap, aspect="auto", vmin=0)
    top = max((v for r in grid for v in r), default=0)
    for i, r in enumerate(grid):
        for j, v in enumerate(r):
            if v:
                ax.text(j, i, str(v), ha="center", va="center", fontsize=8, color="#ffffff" if v > top / 2 else INK)
    ax.set_xticks(range(len(COLUMNS)), COLUMNS, rotation=20, ha="right")
    one_pdf = len({p.rsplit("_p", 1)[0] for p in pages}) == 1
    ax.set_yticks(range(len(pages)), ["p" + p.rsplit("_p", 1)[1] if one_pdf else p for p in pages])
    ax.tick_params(colors=MUTED, labelcolor=INK_2, length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("Hard cells per page and column", color=INK, loc="left", fontsize=12)
    cb = fig.colorbar(img, ax=ax, fraction=0.03)
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=MUTED, labelcolor=INK_2, length=0)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def mark(c: ScoredCell | None) -> str:
    """✅ correct · ⚠️ partial · ❌ wrong (predicted → reference) · 🚩 flagged · 🔁 ditto resolved · blank = empty."""
    if c is None or c.score is None:
        return ""

    def esc(v: str) -> str:
        return v.replace("|", "\\|") or "∅"

    extra = ("🚩" if c.flagged else "") + ("🔁" if c.pred_ditto or c.ref_ditto else "")
    if c.score == 1:
        return f"✅{extra} {esc(c.predicted)}"
    icon = "⚠️" if c.score > 0 else "❌"
    return f"{icon}{extra} {esc(c.predicted)} → {esc(c.reference)}"


def write_page_diffs(run: Run, diff_dir: Path) -> None:
    """One markdown table per page with every cell marked, for reading next to the scan."""
    diff_dir.mkdir(parents=True, exist_ok=True)
    by_page: dict[str, dict[tuple[int, str], ScoredCell]] = defaultdict(dict)
    for c in run.cells:
        by_page[c.page][c.row, c.column] = c
    for page in run.pages:
        cells = by_page.get(page, {})
        counted = [c for c in cells.values() if c.score is not None]
        n_rows = max((r for r, _ in cells), default=0)
        wrong_flags = sum(c.flagged and not c.is_error for c in counted)
        lines = [
            f"# {page} · {run.label}",
            "",
            f"Score {fmt(weighted(counted))} · flag-adjusted {fmt(weighted(counted, 'adjusted'))} · "
            f"errors {errors(counted):.1f} · flags {sum(c.flagged for c in counted)} "
            f"({wrong_flags} on correct cells)",
            "",
            "✅ correct · ⚠️ partial (predicted → reference) · ❌ wrong (predicted → reference) · "
            "🚩 flagged as uncertain by the LLM (✅🚩 = false alarm) · 🔁 resolved from a ditto mark · "
            "∅ empty",
            "",
            "| row | " + " | ".join(COLUMNS) + " |",
            "|" + "---|" * (len(COLUMNS) + 1),
        ]
        for r in range(1, n_rows + 1):
            marks = [mark(cells.get((r, col))) for col in COLUMNS]
            if any(marks):
                lines.append(f"| {r} | " + " | ".join(marks) + " |")
        (diff_dir / f"{page}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(runs: list[Run], out: Path, cells: bool = False, min_share: float = 1.0) -> str:
    out.mkdir(parents=True, exist_ok=True)
    for run in runs:
        write_page_diffs(run, out / run.rel / "diff")
    sums = [summary(r) for r in runs]
    cols = [column_stats(r, c) for r in runs for c in COLUMNS]
    labels = [r.label for r in runs]
    write_csv(out / "summary.csv", sums)
    write_csv(out / "columns.csv", cols)
    if runs:
        plot_columns(cols, labels, out / "columns.png")

    lines = [
        "# Benchmark report",
        "",
        f"Species weight {WEIGHTS['species']:g}× · flagged wrong cell gets {FLAG_WRONG_CREDIT:.0%} of its error back"
        f" · flagged correct cell loses {FLAG_CORRECT_PENALTY:.0%} · repeated ring substitution "
        f"(≥ {SYSTEMATIC_MIN_ROWS} rows) costs {SYSTEMATIC_REPEAT_COST:g} of a character after the first row",
        "",
        "## Summary",
        "",
        *md_table(
            [
                "Run",
                "Pages",
                "Score",
                "Flag-adj.",
                "Flag P",
                "Flag R",
                "Flag F2",
                "Errors / 100 p",
                "Error %",
                "$ / 100 p",
                "$ / 100 correct",
                "H",
                "M",
                "F",
            ],
            [
                [
                    s["run"],
                    s["pages"],
                    fmt(s["score"]),
                    fmt(s["flag_adjusted_score"]),
                    fmt(s["flag_precision"], 2),
                    fmt(s["flag_recall"], 2),
                    fmt(s["flag_f2"], 2),
                    fmt(s["errors_per_100_pages"], 1),
                    fmt(s["error_pct"], 1),
                    fmt(s["cost_per_100_pages"], 3),
                    fmt(s["cost_per_100_correct_cells"], 4),
                    s["H"],
                    s["M"],
                    s["F"],
                ]
                for s in sums
            ],
        ),
        "",
        "Score = weighted mean cell score · Flag P/R/F2 = flags as detector of wrong cells (β = 2, recall "
        "counts more) · errors = sum of (1 − cell score) · H = hallucinated (reference empty), "
        "M = missed (prediction empty), F = flagged cells. Cells empty in both are ignored.",
        "",
        "## Columns",
        "",
        "![errors per column](columns.png)",
        "",
        *md_table(
            ["Column", "Weight", *labels],
            [
                [c, f"{WEIGHTS[c]:g}"]
                + [
                    f"{fmt(s['score'])} · {fmt(s['error_pct'], 1)} % (n={s['cells']}, H={s['H']}, M={s['M']}, "
                    f"F={s['F']}, F2={fmt(s['flag_f2'], 2)})"
                    for s in cols
                    if s["column"] == c
                ]
                for c in COLUMNS
            ],
        ),
        "",
        "score · error % (cells, hallucinated, missed, flagged, flag F2)",
        "",
        "## Pages (weighted score)",
        "",
    ]
    pages = sorted({p for r in runs for p in r.pages + r.missing})
    page_rows = []
    for page in pages:
        line = [page]
        for r in runs:
            pc = [c for c in r.counted() if c.page == page]
            line.append(fmt(weighted(pc)) if page in r.pages else "missing")
        page_rows.append(line)
    lines += md_table(["Page", *labels], page_rows)

    if cells:
        hard = hard_cells(runs, min_share)
        write_csv(out / "hard_cells.csv", hard)
        plot_hard_cells(hard, pages, out / "cells.png")
        lines += [
            "",
            f"## Hard cells (wrong in ≥ {min_share:.0%} of runs)",
            "",
            f"{len(hard)} cells, full list in `hard_cells.csv`. 🔁 = resolved from a ditto mark.",
            "",
            "![hard cells](cells.png)",
            "",
            *md_table(
                ["Page", "Row", "Column", "Reference", "Wrong", "Mean error", "Predictions"],
                [
                    [
                        h["page"],
                        h["row"],
                        h["column"],
                        h["reference"],
                        f"{h['wrong']}/{h['runs']}",
                        fmt(h["mean_error"], 2),
                        h["predicted"],
                    ]
                    for h in hard[:40]
                ],
            ),
        ]
    text = "\n".join(lines) + "\n"
    (out / "report.md").write_text(text, encoding="utf-8")
    return text
