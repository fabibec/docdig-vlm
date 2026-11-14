"""Code that runs inside the pool processes. Imports only the pipeline (no DB, no FastAPI)."""

import atexit
import logging
import multiprocessing as mp
from pathlib import Path

from backend.logging_config import setup_worker_logging
from backend.pipeline import Pipeline, PipelineTask, build_pipeline

_pipeline: Pipeline | None = None
_progress: "mp.Queue | None" = None
log = logging.getLogger(__name__)


def init_worker(
    progress_queue: "mp.Queue",
    log_queue: "mp.Queue | None" = None,
    log_level: str = "INFO",
    libs_level: str = "WARNING",
) -> None:
    """Pool initializer: build the pipeline once per process (models/clients load once).

    Log records go over ``log_queue`` to the main process, which owns the console and the log file.
    """
    global _pipeline, _progress
    if log_queue is not None:
        setup_worker_logging(log_queue, log_level, libs_level)
    else:
        logging.basicConfig(level=log_level, format="%(processName)s %(levelname)s %(name)s: %(message)s")
    _progress = progress_queue
    try:
        _pipeline = build_pipeline().setup()
    except Exception:
        log.exception("worker pipeline setup failed")
        raise
    atexit.register(_pipeline.close)
    log.info("worker ready")


def run_doc(doc_id: int, input_pdf: str, out_dir: str) -> str:
    """Process one document; returns the DocResult as JSON. Progress goes to the queue."""
    assert _pipeline is not None, "worker not initialised"

    def report(step: str, page: int, pages: int) -> None:
        if _progress is not None:
            _progress.put((doc_id, step, page, pages))

    log.debug("run_doc %s: %s -> %s", doc_id, input_pdf, out_dir, extra={"doc_id": doc_id})
    task = PipelineTask(input_pdf=Path(input_pdf), output_directory=Path(out_dir), on_progress=report)
    try:
        return _pipeline.run(task).model_dump_json()
    except Exception as exc:
        # Third-party exceptions (e.g. openai.APIConnectionError) often cannot be unpickled in the main
        # process, which then reports a misleading TypeError. Log the real traceback here and hand back
        # a plain, picklable error that keeps type and message.
        log.exception("run_doc %s failed", doc_id, extra={"doc_id": doc_id})
        raise WorkerError(f"{type(exc).__name__}: {exc}") from None


class WorkerError(RuntimeError):
    """Picklable stand-in for an exception raised inside a pool worker."""
