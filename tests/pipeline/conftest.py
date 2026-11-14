"""Fixtures for the pipeline tests. No network, every artifact goes to ``tmp_path``."""

import os

# Settings() needs LLM credentials at import time. Tests never talk to the LLM; set dummies before any
# backend.pipeline import. setdefault: values already set (CI, a root tests/conftest.py) win.
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("LLM_API_KEY", "test-dummy")

from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.pipeline.models.pipeline_parts import PipelineTask
from tests.pipeline.helpers import HAS_PDFTOPPM, SAMPLE_PDF


@pytest.fixture
def dummy_pdf(tmp_path: Path) -> Path:
    """A placeholder input file: ``PipelineTask.input_pdf`` must exist, steps after ``raw`` never read it."""
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    return pdf


@pytest.fixture
def make_task(tmp_path: Path, dummy_pdf: Path):
    """``make_task(pages=None, name="doc")`` -> a PipelineTask writing to ``tmp_path / name``."""

    def factory(pages: list[int] | None = None, name: str = "doc", **kwargs) -> PipelineTask:
        return PipelineTask(input_pdf=dummy_pdf, output_directory=tmp_path / name, pages=pages, **kwargs)

    return factory


@pytest.fixture(scope="session")
def sample_scan(tmp_path_factory) -> np.ndarray:
    """Page 1 of the real 1972 sample form (50 data rows, 11 columns), rendered once per session."""
    if not HAS_PDFTOPPM:
        pytest.skip("pdftoppm (poppler-utils) not installed")
    from backend.pipeline.steps import ImgExtractor

    out = tmp_path_factory.mktemp("sample_scan")
    ImgExtractor().execute(PipelineTask(input_pdf=SAMPLE_PDF, output_directory=out, pages=[1]))
    img = cv2.imread(str(out / "raw" / "p001.jpg"))
    assert img is not None
    return img
