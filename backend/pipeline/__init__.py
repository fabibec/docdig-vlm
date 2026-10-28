"""Extraction pipeline: PDF -> page images (raw) -> table crops (cropped) -> LLM tables (processed).

Standalone: no dependency on the web app. Entry points:
    uv run python -m backend.pipeline --help   (CLI)
    uv run python -m backend.main              (playground: data/input -> data/output)
"""

from backend.pipeline.models.pipeline_parts import Pipeline, PipelineTask
from backend.pipeline.models.results import DocResult, PageGeometry, PageResult

STEP_NAMES = ["raw", "cropped", "processed"]


def build_pipeline(draw_rows: bool = False, ocr: bool = False) -> Pipeline:
    """The default pipeline. Call ``.setup()`` before running it."""
    from backend.pipeline.steps import ImgExtractor, LlmExtractor, TableExtractor

    pipeline = Pipeline().add_step(ImgExtractor()).add_step(TableExtractor(draw_rows=draw_rows))
    if ocr:
        from backend.pipeline.steps import OcrExtractor

        pipeline.add_step(OcrExtractor())
    return pipeline.add_step(LlmExtractor())


__all__ = [
    "STEP_NAMES",
    "DocResult",
    "PageGeometry",
    "PageResult",
    "Pipeline",
    "PipelineTask",
    "build_pipeline",
]
