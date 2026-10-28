"""Database tables (SQLModel). The DB stores only paths relative to the artifact root."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from backend.api.enums import ColumnKey, DocStatus, FlagSource, Severity


def utcnow() -> datetime:
    return datetime.now(UTC)


class Batch(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    owner: str | None = None  # username once auth exists
    created_at: datetime = Field(default_factory=utcnow)

    docs: list["Doc"] = Relationship(back_populates="batch")


class Doc(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("batch_id", "filename"),)

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    batch_id: int = Field(foreign_key="batch.id", index=True)
    filename: str
    status: DocStatus = Field(default=DocStatus.queued, index=True)
    num_pages: int | None = None
    open_flags: int = 0  # denormalised for list views
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    batch: Batch = Relationship(back_populates="docs")

    @property
    def rel_dir(self) -> str:
        """Artifact folder relative to the batches root."""
        return f"{self.batch.slug}/{self.slug}"


class Page(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("doc_id", "num"),)

    id: int | None = Field(default=None, primary_key=True)
    doc_id: int = Field(foreign_key="doc.id", index=True, ondelete="CASCADE")
    num: int
    width: int
    height: int
    col_bounds: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    page_confidence: float | None = None


class Row(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("page_id", "idx"),)

    id: int | None = Field(default=None, primary_key=True)
    page_id: int = Field(foreign_key="page.id", index=True, ondelete="CASCADE")
    idx: int  # 1-based data row
    y0: int
    y1: int
    values: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))  # machine output
    corrections: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))  # human edits

    def effective(self) -> dict[str, str]:
        return {**self.values, **self.corrections}


class Flag(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    doc_id: int = Field(foreign_key="doc.id", index=True, ondelete="CASCADE")
    page_num: int
    row_idx: int
    column: ColumnKey | None = None
    source: FlagSource
    severity: Severity
    code: str | None = None
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    message: str
    suggestion: str | None = None
    confidence: float | None = None
    resolved: bool = Field(default=False, index=True)
