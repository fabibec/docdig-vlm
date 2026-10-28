from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, FilePath

from backend.pipeline.models.llm_responses import ExtractionResult
from backend.pipeline.models.results import DocResult, PageGeometry, PageResult

StepName = Literal["raw", "cropped", "processed"]
ProgressFn = Callable[[str, int, int], None]  # (step, page, pages)

PAGE_RE = re.compile(r"^p(\d{3})$")


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
        if self.on_progress:
            self.on_progress(step, page, pages)


class PipelineStep(ABC):
    name: ClassVar[str]

    def setup(self) -> None:
        """Load models etc. Called once per process."""

    @abstractmethod
    def execute(self, task: PipelineTask) -> None: ...


class Pipeline:
    def __init__(self):
        self.steps: list[PipelineStep] = []

    def add_step(self, step: PipelineStep) -> Pipeline:
        self.steps.append(step)
        return self

    def setup(self) -> Pipeline:
        for step in self.steps:
            step.setup()
        return self

    def run(self, task: PipelineTask, only: list[str] | None = None) -> DocResult:
        """Run all steps (or only the named ones) and collect what's on disk."""
        task.output_directory.mkdir(parents=True, exist_ok=True)
        for step in self.steps:
            if only is None or step.name in only:
                step.execute(task)
        return collect_result(task)

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
            )
        )
    return DocResult(pages=pages)
