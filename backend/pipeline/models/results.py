"""Result models a pipeline run produces. Shared by the CLI and the web app."""

from pydantic import BaseModel, Field

from backend.pipeline.models.llm_responses import PageTable


class PageGeometry(BaseModel):
    """Table geometry of one page, in pixels of the *cropped* table image (step 2).

    Data row ``n`` (1-based, header excluded) spans ``row_bounds[n - 1]`` vertically.
    Column ``c`` spans ``col_bounds[c]..col_bounds[c + 1]`` horizontally.
    """

    width: int
    height: int
    skew_angle: float = 0.0
    header: tuple[int, int] = Field(description="y0, y1 of the header row")
    row_bounds: list[tuple[int, int]] = Field(description="y0, y1 per data row")
    col_bounds: list[int] = Field(description="x of every vertical grid line, left to right")


class PageResult(BaseModel):
    num: int  # 1-based page number in the PDF
    geometry: PageGeometry
    table: PageTable


class DocResult(BaseModel):
    pages: list[PageResult]
