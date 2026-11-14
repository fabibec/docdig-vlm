"""``prepare_crops`` renders + crops the referenced pages once, without the orientation step or the LLM."""

import shutil
from unittest.mock import patch

import pytest

from backend.pipeline.steps import ImgExtractor, TableOrientationDetector
from benchmark.run_benchmark import prepare_crops

pytestmark = pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="pdftoppm (poppler-utils) not installed")


def test_prepare_crops_crops_referenced_pages_only(tmp_path):
    with patch.object(TableOrientationDetector, "execute", side_effect=AssertionError("orientation step ran")):
        prepare_crops(["scan_1972_sample_p001"], tmp_path)

    cropped = tmp_path / "scan_1972_sample" / "cropped"
    assert (cropped / "p001.jpg").is_file()
    assert (cropped / "p001.geom.json").is_file()
    assert sorted(p.name for p in (tmp_path / "scan_1972_sample" / "raw").iterdir()) == ["p001.jpg"]


def test_prepare_crops_reuses_cached_crops(tmp_path):
    prepare_crops(["scan_1972_sample_p001"], tmp_path)

    with patch.object(ImgExtractor, "execute", side_effect=AssertionError("re-rendered")):
        prepare_crops(["scan_1972_sample_p001"], tmp_path)
