"""Extraction pipeline: PDF -> page images (raw) -> table crops (cropped) -> LLM tables (processed).

Standalone: no dependency on the web app. Entry points:
    uv run python -m backend.pipeline --help   (CLI)
    uv run python -m backend.main              (playground: data/input -> data/output)
"""

from backend.pipeline.models.pipeline_parts import Pipeline, PipelineTask
from backend.pipeline.models.results import DocResult, PageGeometry, PageResult

STEP_NAMES = ["raw", "cropped", "processed"]


def build_pipeline(draw_rows: bool = False, ocr: bool = False, tesseract: bool = False) -> Pipeline:
    """The default pipeline. Call ``.setup()`` before running it."""
    from backend.pipeline.steps import ImgExtractor, LlmExtractor, TableExtractor

    pipeline = Pipeline().add_step(ImgExtractor()).add_step(TableExtractor(draw_rows=draw_rows))
    if ocr:
        from backend.pipeline.steps import OcrExtractor

        pipeline.add_step(OcrExtractor())
    if tesseract:
        from backend.pipeline.steps import TesseractExtractor

        pipeline.add_step(TesseractExtractor())
    if not ocr and not tesseract:
        pipeline.add_step(LlmExtractor())
    return pipeline


__all__ = [
    "STEP_NAMES",
    "DocResult",
    "PageGeometry",
    "PageResult",
    "Pipeline",
    "PipelineTask",
    "build_pipeline",
]
