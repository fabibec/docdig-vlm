"""Read helpers that turn DB rows into API schemas. Shared by routes and processing."""

from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.api.enums import DocStatus
from backend.api.models import Batch, Doc, Flag
from backend.api.schemas import (
    BatchEvent,
    BatchOut,
    DocEvent,
    DocOut,
    Progress,
    ReviewEvent,
    StatusCounts,
)


def utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo; everything is stored as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def doc_out(doc: Doc) -> DocOut:
    return DocOut(
        slug=doc.slug,
        batch_slug=doc.batch.slug,
        filename=doc.filename,
        status=doc.status,
        num_pages=doc.num_pages,
        open_flags=doc.open_flags,
        error=doc.error,
        updated_at=utc(doc.updated_at),
    )


async def batch_outs(session: AsyncSession, batches: list[Batch]) -> list[BatchOut]:
    ids = [b.id for b in batches]
    counts: dict[int, StatusCounts] = {i: StatusCounts() for i in ids}
    flags: dict[int, int] = dict.fromkeys(ids, 0)
    rows = await session.exec(
        select(Doc.batch_id, Doc.status, func.count(), func.sum(Doc.open_flags))
        .where(Doc.batch_id.in_(ids))
        .group_by(Doc.batch_id, Doc.status)
    )
    for batch_id, status, n, open_flags in rows:
        setattr(counts[batch_id], DocStatus(status).value, n)
        flags[batch_id] += open_flags or 0
    return [
        BatchOut(
            slug=b.slug,
            name=b.name,
            created_at=utc(b.created_at),
            num_docs=sum(counts[b.id].model_dump().values()),
            counts=counts[b.id],
            open_flags=flags[b.id],
        )
        for b in batches
    ]


async def get_doc(session: AsyncSession, slug: str) -> Doc | None:
    return (
        await session.exec(select(Doc).where(Doc.slug == slug).options(selectinload(Doc.batch)))
    ).first()


async def load_doc(session: AsyncSession, doc_id: int) -> Doc:
    return (
        await session.exec(select(Doc).where(Doc.id == doc_id).options(selectinload(Doc.batch)))
    ).one()


async def review_stats(session: AsyncSession, batch_id: int | None = None, doc_id: int | None = None):
    q = select(func.count(), func.coalesce(func.sum(Doc.open_flags), 0)).where(
        Doc.status == DocStatus.review
    )
    if batch_id is not None:
        q = q.where(Doc.batch_id == batch_id)
    if doc_id is not None:
        q = q.where(Doc.id == doc_id)
    files_left, flags_left = (await session.exec(q)).one()
    return files_left, flags_left


async def refresh_doc_flags(session: AsyncSession, doc: Doc) -> None:
    """Recompute open_flags + review/done status (does not commit)."""
    open_flags = (
        await session.exec(
            select(func.count()).select_from(Flag).where(Flag.doc_id == doc.id, Flag.resolved == False)  # noqa: E712
        )
    ).one()
    doc.open_flags = open_flags
    doc.status = DocStatus.review if open_flags else DocStatus.done
    doc.updated_at = datetime.now(UTC)


# ---------- events ----------


async def doc_events(session: AsyncSession, doc: Doc, progress: Progress | None = None):
    """DocEvent + updated BatchEvent + global ReviewEvent for one changed doc."""
    (batch,) = await batch_outs(session, [doc.batch])
    files_left, flags_left = await review_stats(session)
    return [
        DocEvent(doc=doc_out(doc), progress=progress),
        BatchEvent(type="batch.updated", batch=batch),
        ReviewEvent(files_left=files_left, flags_left=flags_left),
    ]
