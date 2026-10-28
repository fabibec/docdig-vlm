"""Playground: run the full pipeline on every PDF in data/input -> data/output/<pdf stem>/.

    uv run python -m backend.main

For single steps / pages use the CLI: uv run python -m backend.pipeline --help
"""

from backend.pipeline import PipelineTask, build_pipeline
from backend.pipeline.directories import INPUT_DIR, OUTPUT_DIR

if __name__ == "__main__":
    extraction_pipeline = build_pipeline().setup()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for pdf in sorted(INPUT_DIR.glob("*.pdf")):
        task = PipelineTask(input_pdf=pdf, output_directory=OUTPUT_DIR / pdf.stem)
        result = extraction_pipeline.run(task)
        print(f"{pdf.name}: {len(result.pages)} page(s) -> {task.output_directory}")
