"""Request/response models of the HTTP API and the WebSocket events.

The frontend mirrors these by hand in frontend/src/app/core/api/api.types.ts
(reference: /openapi.json). Overview tables: docs/backend.md.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from backend.api.enums import ArtifactState, ColumnKey, DocStatus, FlagSource, Severity


class Paginated[T](BaseModel):
    items: list[T]
    total: int
    page: int
    size: int


class StatusCounts(BaseModel):
    queued: int = 0
    processing: int = 0
    review: int = 0
    done: int = 0
    error: int = 0


class BatchOut(BaseModel):
    slug: str
    name: str
    created_at: datetime
    num_docs: int
    counts: StatusCounts
    open_flags: int


class DocOut(BaseModel):
    slug: str
    batch_slug: str
    filename: str
    status: DocStatus
    num_pages: int | None
    open_flags: int
    error: str | None
    updated_at: datetime


class PageInfo(BaseModel):
    num: int
    open_flags: int
    page_confidence: float | None


class DocDetail(DocOut):
    batch_name: str
    pages: list[PageInfo]
    artifact_url: str = Field(
        description="Template, replace {state} and {num}: /api/docs/<slug>/artifacts/{state}/page/{num}"
    )


class RowOut(BaseModel):
    idx: int = Field(description="1-based data row")
    y0: int
    y1: int
    values: dict[ColumnKey, str] = Field(description="effective values (machine + corrections)")
    corrected: list[ColumnKey]


class PageRows(BaseModel):
    num: int
    width: int = Field(description="cropped image width (overlay viewBox)")
    height: int
    columns: list[ColumnKey]
    column_x: dict[ColumnKey, tuple[int, int]] = Field(
        description="x0, x1 of every column in the cropped image; cell = column_x[c] × (y0, y1)"
    )
    rows: list[RowOut]


class FlagOut(BaseModel):
    id: int
    page: int
    row: int
    column: ColumnKey | None
    source: FlagSource
    severity: Severity
    code: str | None = Field(description="rule flags: i18n key, translated by the frontend")
    params: dict[str, str | float]
    message: str
    suggestion: str | None
    confidence: float | None
    resolved: bool


class RowUpdate(BaseModel):
    values: dict[ColumnKey, str] = Field(
        default_factory=dict, description="changed cells only; empty = accept machine values"
    )


class ResolveOut(BaseModel):
    doc: DocOut
    resolved_flag_ids: list[int]


class FlagCreate(BaseModel):
    """A reviewer annotation (stored as a flag with source=user)."""

    page: int
    row: int = Field(description="1-based data row")
    column: ColumnKey | None = Field(None, description="null = whole row")
    message: str = Field(min_length=1, max_length=2000)
    severity: Severity = Severity.warning


class FlagIds(BaseModel):
    ids: list[int] = Field(min_length=1)


class FlagChangeOut(BaseModel):
    """Result of creating / deleting / reopening flags: the updated doc and the affected flags."""

    doc: DocOut
    flags: list[FlagOut] = Field(description="created or reopened flags; empty on delete")


class DuplicateFiles(BaseModel):
    detail: Literal["duplicate_files"] = "duplicate_files"
    filenames: list[str]


class ReviewItem(BaseModel):
    slug: str
    batch_slug: str
    filename: str
    open_flags: int


class ReviewQueue(BaseModel):
    scope: Literal["all", "batch", "doc"]
    files_left: int
    flags_left: int
    docs: list[ReviewItem] = Field(description="oldest batch first, then filename")


class Health(BaseModel):
    status: Literal["ok"] = "ok"


# ---------- WebSocket (server -> client) ----------


class Progress(BaseModel):
    step: ArtifactState
    page: int
    pages: int


class DocEvent(BaseModel):
    type: Literal["doc.status"] = "doc.status"
    doc: DocOut
    progress: Progress | None = None


class BatchEvent(BaseModel):
    type: Literal["batch.created", "batch.updated"]
    batch: BatchOut


class ReviewEvent(BaseModel):
    type: Literal["review.pending"] = "review.pending"
    files_left: int
    flags_left: int


ServerEvent = Annotated[DocEvent | BatchEvent | ReviewEvent, Field(discriminator="type")]


class ServerEventEnvelope(BaseModel):
    """Only used to publish the WS event schema in OpenAPI (GET /api/ws/schema)."""

    event: ServerEvent
