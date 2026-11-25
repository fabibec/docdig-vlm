"""Benchmark: compare LLM tables against the hand-made reference CSVs in data/dataset/output/.

Two commands. `run` collects raw data (the LLM is paid, so this is the expensive part), `report` scores it
and can be re-run any time, also over several result folders and a subset of models.

    uv run -m benchmark run --model openai/gpt-6-luna:low --model anthropic/claude-haiku-5.5:low
    uv run -m benchmark run --refs scan_1972_CdB_1_20231125160539_p001 --model openai/gpt-6-luna:low
    uv run -m benchmark run --run luna=data/output                # score existing outputs, no LLM calls
    uv run -m benchmark report benchmark/results/<ts> [more dirs] [--model luna] [--cells]

Every `run` gets its own folder benchmark/results/<YYYY-MM-DD_HH-MM-SS>/ with the report on top and one folder
per model: <provider>/<model>/<effort>/{cells.csv, pages.csv, diff/<page>.md, <pdf>/processed/}
(outputs scored with --run go to external/<label>/). `report` writes into <first dir>/report/ unless --out is
given.
"""

import argparse
from datetime import datetime
from pathlib import Path

from backend.pipeline.directories import PROJECT_ROOT

RESULTS_DIR = PROJECT_ROOT / "benchmark" / "results"

# (model name, reasoning effort) pairs benchmarked when `run` gets neither --model nor --run.
MODELS: list[tuple[str, str]] = [
    # OpenAI
    ("openai/gpt-6-luna", "low"),  # 0.10/0.50
    # Anthropic
    ("anthropic/claude-haiku-5.5", "low"),  # 0.10/0.50
    # New Meta Model
    ("meta/muse-ember", "low"),  # 0.10/0.50
    # Chinese Open Source
    ("minimax/minimax-m3.1-flash", "low"),  # 0.075/0.30
    # Google (good vision)
    ("google/gemini-3.5-flash-lite", "low"),  # 0.30/2.50
    # Mistral
    ("mistralai/mistral-small-2603", "low"),
]


def _report_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cells", action="store_true", help="also list cells most runs get wrong (hard_cells.csv)")
    p.add_argument(
        "--min-share",
        type=float,
        default=1.0,
        help="--cells: share of runs (0-1) that must get a cell wrong to list it (default: all)",
    )


def cmd_run(args: argparse.Namespace) -> None:
    from benchmark.collect import REF_DIR, run_benchmark
    from benchmark.report import load, write_report

    refs = list(args.refs)
    if args.refs_file:
        refs += [line.strip().removesuffix(".csv") for line in args.refs_file.read_text().splitlines() if line.strip()]
    if not refs:
        refs = sorted(p.stem for p in REF_DIR.glob("*.csv"))
    models = [tuple(m.rsplit(":", 1)) for m in args.model]
    if not models and not args.run:
        models = MODELS
    external = [(label, Path(root or label)) for label, _, root in (s.partition("=") for s in args.run)]

    out = args.out or RESULTS_DIR / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty: every run gets its own folder (combine them with `report a b`)")
    run_benchmark(out, refs, models, external)
    print(write_report(load([out]), out, cells=args.cells, min_share=args.min_share))
    print(f"Results: {out}")


def cmd_report(args: argparse.Namespace) -> None:
    from benchmark.report import load, write_report

    out = args.out or args.dirs[0] / "report"
    runs = load(args.dirs, only=args.model or None)
    if not runs:
        raise SystemExit("no runs found (check the folders and --model)")
    print(write_report(runs, out, cells=args.cells, min_share=args.min_share))
    print(f"Report: {out}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="python -m benchmark", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run models on the reference pages and store raw cells")
    run.add_argument("--refs", nargs="*", default=[], help="reference stems, e.g. scan_..._p001 (default: all)")
    run.add_argument("--refs-file", type=Path, help="text file with one reference stem per line")
    run.add_argument(
        "--model", action="append", default=[], help="provider/model:effort, repeatable (default: MODELS list)"
    )
    run.add_argument(
        "--run",
        action="append",
        default=[],
        help="label=ROOT of existing pipeline outputs (ROOT/<pdf stem>/processed/), repeatable, no LLM calls",
    )
    run.add_argument("--out", type=Path, help=f"result folder (default: {RESULTS_DIR}/<timestamp>)")
    _report_args(run)
    run.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="score raw results and write report.md + charts")
    rep.add_argument("dirs", nargs="+", type=Path, help="result folders containing cells.csv and pages.csv")
    rep.add_argument("--model", action="append", default=[], help="only runs whose label contains this, repeatable")
    rep.add_argument("--out", type=Path, help="report folder (default: <first dir>/report)")
    _report_args(rep)
    rep.set_defaults(func=cmd_report)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
