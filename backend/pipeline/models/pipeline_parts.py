from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel, FilePath, DirectoryPath

class PipelineTask(BaseModel):
    files: list[FilePath]
    output_directory: DirectoryPath
    last_step_output_directory: Optional[DirectoryPath] = None

class PipelineStep(ABC):
    @abstractmethod
    def setup(self):
        pass

    @abstractmethod
    def execute(self, task: PipelineTask):
        pass


class Pipeline:
    def __init__(self):
        self.steps: list[PipelineStep] = []

    def add_step(self, step: PipelineStep) -> Pipeline:
        self.steps.append(step)
        return self

    def setup(self):
        for step in self.steps:
            step.setup()

    def run_from_task(self, task: PipelineTask):
        for step in self.steps:
            step.execute(task)
