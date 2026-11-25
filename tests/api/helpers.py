"""Helpers for the API tests: the ``ApiEnv`` handle returned by the ``api`` fixture and the
``Factory`` that inserts rows directly. All DB work from a test goes through ``api.run(...)``,
which executes a coroutine on the TestClient's event loop (the same loop the app uses).
"""

import io
import itertools
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.api.enums import COLUMNS, DocStatus, FlagSource, Severity
from backend.api.models import Batch, Doc, Flag, Page, Row, pk
from backend.api.queries import load_doc, refresh_doc_flags
from backend.api.schemas import BatchEvent, DocEvent, ReviewEvent
from backend.api.storage import store

Event = DocEvent | BatchEvent | ReviewEvent  # what hub.broadcast() sends

# 12 grid lines for the 11 printed columns, 100 px apart.
COL_BOUNDS = [i * 100 for i in range(12)]


def pdf_bytes(label: str = "test") -> bytes:
    """A tiny, structurally valid one-page PDF. ``label`` makes the content unique."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] >>",
    ]
    out = bytearray(b"%PDF-1.4\n% " + label.encode() + b"\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def empty_values(**values: str) -> dict[str, str]:
    """Machine values for one row: every column present, unspecified ones empty."""
    return {c.value: "" for c in COLUMNS} | values


@dataclass
class ApiEnv:
    client: TestClient
    engine: AsyncEngine
    session_maker: async_sessionmaker[AsyncSession]
    batches_dir: Path
    enqueued: list[int] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)  # every hub.broadcast() event, in order

    def run(self, fn: Callable[..., Awaitable[Any]], *args: Any) -> Any:
        """Run ``await fn(*args)`` on the app's event loop and return the result."""
        portal = self.client.portal
        assert portal is not None, "the TestClient must be entered (with TestClient(app) as client)"
        return portal.call(fn, *args)

    def event_types(self) -> list[str]:
        return [e.type for e in self.events]

    def upload(self, name: str = "Batch", files: Iterable[str | tuple[str, bytes]] = ("a.pdf",), **kw):
        """POST /api/batches; ``files`` are filenames (content generated) or (filename, bytes)."""
        parts = []
        for f in files:
            fname, content = (f, pdf_bytes(f)) if isinstance(f, str) else f
            parts.append(("files", (fname, io.BytesIO(content), "application/pdf")))
        return self.client.post("/api/batches", data={"name": name}, files=parts, **kw)


class Factory:
    """Inserts DB rows directly, with sensible defaults. Returns detached, loaded objects."""

    def __init__(self, api: ApiEnv):
        self.api = api
        self._seq = itertools.count(1)

    def _add(self, obj):
        async def go():
            async with self.api.session_maker() as s:
                s.add(obj)
                await s.commit()
                await s.refresh(obj)
            return obj

        return self.api.run(go)

    def batch(self, name: str = "Batch", *, slug: str | None = None, created_at: datetime | None = None, **kw):
        slug = slug or f"batch-{next(self._seq)}"
        if created_at is not None:
            kw["created_at"] = created_at
        return self._add(Batch(slug=slug, name=name, **kw))

    def doc(
        self,
        batch: Batch,
        filename: str = "scan.pdf",
        *,
        status: DocStatus = DocStatus.queued,
        slug: str | None = None,
        with_file: bool = True,
        **kw,
    ) -> Doc:
        slug = slug or f"{batch.slug}-doc-{next(self._seq)}"
        doc = self._add(Doc(slug=slug, batch_id=pk(batch), filename=filename, status=status, **kw))
        if with_file:
            path = store.input_pdf(f"{batch.slug}/{slug}", filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pdf_bytes(filename))
        return doc

    def page(self, doc: Doc, num: int = 1, **kw) -> Page:
        defaults: dict[str, Any] = {"width": 1100, "height": 800, "col_bounds": COL_BOUNDS, "page_confidence": 0.9}
        return self._add(Page(doc_id=pk(doc), num=num, **(defaults | kw)))

    def row(self, page: Page, idx: int, values: dict[str, str] | None = None, **kw) -> Row:
        defaults: dict[str, Any] = {"y0": 100 + (idx - 1) * 50, "y1": 150 + (idx - 1) * 50, "corrections": {}}
        return self._add(Row(page_id=pk(page), idx=idx, values=empty_values(**(values or {})), **(defaults | kw)))

    def flag(self, doc: Doc, page_num: int = 1, row_idx: int = 1, **kw) -> Flag:
        defaults: dict[str, Any] = {"source": FlagSource.rule, "severity": Severity.warning, "message": "check this"}
        return self._add(Flag(doc_id=pk(doc), page_num=page_num, row_idx=row_idx, **(defaults | kw)))

    def refresh_flags(self, doc: Doc) -> None:
        """Recompute open_flags + status like the routes do."""

        async def go():
            async with self.api.session_maker() as s:
                await refresh_doc_flags(s, await load_doc(s, pk(doc)))
                await s.commit()

        self.api.run(go)

    def all(self, model, **where):
        """Every row of ``model`` matching ``column=value`` filters."""

        async def go():
            async with self.api.session_maker() as s:
                q = select(model).where(*(getattr(model, k) == v for k, v in where.items()))
                return list((await s.exec(q)).all())

        return self.api.run(go)

    def get(self, model, id_: int):
        async def go():
            async with self.api.session_maker() as s:
                return await s.get(model, id_)

        return self.api.run(go)

    def processed_doc(self, *, batch: Batch | None = None, filename: str = "scan.pdf"):
        """A reviewable doc: one page, rows 1-3, two open flags on row 1 and one on row 2."""
        batch = batch or self.batch("Processed")
        doc = self.doc(batch, filename, status=DocStatus.review, num_pages=1)
        page = self.page(doc)
        rows = [
            self.row(page, 1, {"ring_no": "A1", "species": "Parus major", "weight_g": "1x", "time": "8h"}),
            self.row(page, 2, {"ring_no": "A2", "species": "Erithacus", "wing_length_mm": "71"}),
            self.row(page, 3, {"ring_no": "A3", "species": "Turdus merula", "weight_g": "95"}),
        ]
        flags = [
            self.flag(doc, 1, 1, column="weight_g", code="not_a_number", severity=Severity.error),
            self.flag(doc, 1, 1, column="time", code="time_format"),
            self.flag(doc, 1, 2, column="species", source=FlagSource.llm, message="unclear"),
        ]
        self.refresh_flags(doc)
        return Processed(batch=batch, doc=doc, page=page, rows=rows, flags=flags)


@dataclass
class Processed:
    batch: Batch
    doc: Doc
    page: Page
    rows: list[Row]
    flags: list[Flag]
