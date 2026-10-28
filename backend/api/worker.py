"""Code that runs inside the pool processes. Imports only the pipeline (no DB, no FastAPI)."""

import logging
import multiprocessing as mp
from pathlib import Path

from backend.pipeline import Pipeline, PipelineTask, build_pipeline

_pipeline: Pipeline | None = None
_progress: "mp.Queue | None" = None


def init_worker(progress_queue: "mp.Queue") -> None:
    """Pool initializer: build the pipeline once per process (models/clients load once)."""
    global _pipeline, _progress
    logging.basicConfig(level=logging.INFO, format="%(processName)s %(levelname)s %(message)s")
    _progress = progress_queue
    _pipeline = build_pipeline().setup()


def run_doc(doc_id: int, input_pdf: str, out_dir: str) -> str:
    """Process one document; returns the DocResult as JSON. Progress goes to the queue."""
    assert _pipeline is not None, "worker not initialised"

    def report(step: str, page: int, pages: int) -> None:
        if _progress is not None:
            _progress.put((doc_id, step, page, pages))

    task = PipelineTask(input_pdf=Path(input_pdf), output_directory=Path(out_dir), on_progress=report)
    return _pipeline.run(task).model_dump_json()
