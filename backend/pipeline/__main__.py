"""Pipeline CLI.

Examples:
    uv run python -m backend.pipeline run data/input/scan.pdf -o data/output
    uv run python -m backend.pipeline run data/input/scan.pdf -o data/output --steps cropped,processed
    uv run python -m backend.pipeline run data/input/scan.pdf -o data/output --page 2 --steps cropped --draw-rows
"""

import argparse
import sys
import time
from pathlib import Path

from backend.pipeline import STEP_NAMES, PipelineTask, build_pipeline


def _steps(value: str) -> list[str]:
    steps = [s.strip() for s in value.split(",") if s.strip()]
    allowed = [*STEP_NAMES, "ocr", "tesseract"]
    if bad := [s for s in steps if s not in allowed]:
        raise argparse.ArgumentTypeError(f"unknown step(s) {bad}, choose from {allowed}")
    return steps


def _progress(step: str, page: int, pages: int) -> None:
    print(f"  [{step:>9}] page {page}/{pages}", flush=True)


def cmd_run(args: argparse.Namespace) -> int:
    pdfs = [p for src in args.inputs for p in (sorted(src.glob("*.pdf")) if src.is_dir() else [src])]
    if not pdfs:
        print("no PDFs found", file=sys.stderr)
        return 1

    steps = args.steps or STEP_NAMES
    # Only set up what runs (the LLM step needs API credentials, OCR a GPU).
    pipeline = build_pipeline(draw_rows=args.draw_rows, ocr="ocr" in steps, tesseract="tesseract" in steps).setup(
        only=steps
    )
    try:
        for pdf in pdfs:
            out = args.output / pdf.stem
            print(f"{pdf} -> {out}")
            t0 = time.perf_counter()
            task = PipelineTask(input_pdf=pdf, output_directory=out, pages=args.page, on_progress=_progress)
            result = pipeline.run(task, only=steps)
            print(f"  done in {time.perf_counter() - t0:.1f}s, {len(result.pages)} page(s) complete")
    finally:
        pipeline.close(only=steps)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run the pipeline on PDFs (files or folders)")
    run.add_argument("inputs", nargs="+", type=Path)
    run.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("data/output"),
        help="output root; each PDF gets <output>/<pdf stem>/ (default: data/output)",
    )
    run.add_argument(
        "--steps",
        type=_steps,
        default=None,
        help=f"comma separated subset of {STEP_NAMES} (+ ocr, tesseract); missing inputs are read from disk",
    )
    run.add_argument("--page", type=int, action="append", help="1-based page number, repeatable (default: all pages)")
    run.add_argument(
        "--draw-rows", action="store_true", help="write cropped/grid/pNNN.jpg with detected rows and columns"
    )
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
