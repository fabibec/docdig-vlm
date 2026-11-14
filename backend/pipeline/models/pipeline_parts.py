from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, FilePath

from backend.pipeline.models.llm_responses import ExtractionResult
from backend.pipeline.models.results import DocResult, PageGeometry, PageResult

StepName = Literal["raw", "cropped", "processed"]
ProgressFn = Callable[[str, int, int], None]  # (step, page, pages)

PAGE_RE = re.compile(r"^p(\d{3})$")

log = logging.getLogger(__name__)


def page_stem(num: int) -> str:
    return f"p{num:03d}"


class PipelineTask(BaseModel):
    """One PDF. Every step reads its input from the previous step's folder on disk,
    so steps can be re-run on their own:

        <output_directory>/raw/p001.jpg
                           cropped/p001.jpg  cropped/p001.geom.json  cropped/outlines/p001.jpg
                           processed/p001.json  processed/p001.md
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    input_pdf: FilePath
    output_directory: Path
    pages: list[int] | None = Field(default=None, description="1-based page filter; None = all")
    on_progress: ProgressFn | None = Field(default=None, exclude=True)

    def step_dir(self, step: str) -> Path:
        return self.output_directory / step

    def page_nums(self, step: str, suffix: str = ".jpg") -> list[int]:
        """Pages that exist on disk for ``step`` (respecting the page filter)."""
        d = self.step_dir(step)
        if not d.is_dir():
            return []
        nums = sorted(
            int(m.group(1))
            for p in d.glob(f"*{suffix}")
            if (m := PAGE_RE.match(p.name.removesuffix(suffix)))
        )
        return [n for n in nums if self.pages is None or n in self.pages]

    def report(self, step: str, page: int, pages: int) -> None:
        log.debug(
            "%s: %s page %d/%d done",
            self.input_pdf.name,
            step,
            page,
            pages,
            extra={"pdf": self.input_pdf.name, "step": step, "page": page, "pages": pages},
        )
        if self.on_progress:
            self.on_progress(step, page, pages)


class PipelineStep(ABC):
    name: ClassVar[str]

    def setup(self) -> None:
        """Load models etc. Called once per process."""

    def close(self) -> None:
        """Release resources opened in ``setup``. Called once per process."""

    @abstractmethod
    def execute(self, task: PipelineTask) -> None: ...


class Pipeline:
    def __init__(self):
        self.steps: list[PipelineStep] = []

    def add_step(self, step: PipelineStep) -> Pipeline:
        self.steps.append(step)
        return self

    def setup(self, only: list[str] | None = None) -> Pipeline:
        for step in self.steps:
            if only is None or step.name in only:
                step.setup()
        return self

    def close(self, only: list[str] | None = None) -> None:
        for step in self.steps:
            if only is None or step.name in only:
                step.close()

    def __enter__(self) -> Pipeline:
        return self.setup()

    def __exit__(self, *exc) -> None:
        self.close()

    def run(self, task: PipelineTask, only: list[str] | None = None) -> DocResult:
        """Run all steps (or only the named ones) and collect what's on disk."""
        task.output_directory.mkdir(parents=True, exist_ok=True)
        pdf = task.input_pdf.name
        selected = [step for step in self.steps if only is None or step.name in only]
        step_names = [step.name for step in selected]
        log.info(
            "Pipeline started for %s (pages=%s, steps=%s)",
            pdf,
            task.pages if task.pages is not None else "all",
            ",".join(step_names),
            extra={"pdf": pdf, "page_filter": task.pages, "steps": step_names},
        )
        doc_start = time.perf_counter()
        for step in selected:
            step_start = time.perf_counter()
            step.execute(task)
            duration_ms = round((time.perf_counter() - step_start) * 1000)
            log.info(
                "Step %s (%s) finished for %s in %d ms",
                step.name,
                type(step).__name__,
                pdf,
                duration_ms,
                extra={"pdf": pdf, "step": step.name, "step_class": type(step).__name__, "duration_ms": duration_ms},
            )
        result = collect_result(task)
        duration_ms = round((time.perf_counter() - doc_start) * 1000)
        log.info(
            "Pipeline finished for %s: %d page(s) complete in %d ms",
            pdf,
            len(result.pages),
            duration_ms,
            extra={"pdf": pdf, "pages_complete": len(result.pages), "duration_ms": duration_ms},
        )
        return result

    # backwards compatible name
    run_from_task = run


def collect_result(task: PipelineTask) -> DocResult:
    """Build a DocResult from the artifacts of a finished run (pages missing a step are skipped)."""
    pages = []
    for num in task.page_nums("processed", ".json"):
        geom = task.step_dir("cropped") / f"{page_stem(num)}.geom.json"
        if not geom.exists():
            continue
        extraction = ExtractionResult.model_validate_json(
            (task.step_dir("processed") / f"{page_stem(num)}.json").read_text("utf-8")
        )
        pages.append(
            PageResult(
                num=num,
                geometry=PageGeometry.model_validate_json(geom.read_text("utf-8")),
                table=extraction.table,
                estimated_cost=extraction.estimated_cost,
            )
        )
    return DocResult(pages=pages)
