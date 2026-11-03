from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.api.db import get_session
from backend.api.enums import DocStatus
from backend.api.models import Batch, Doc
from backend.api.queries import get_doc, review_stats
from backend.api.schemas import ReviewItem, ReviewQueue

router = APIRouter(prefix="/review", tags=["review"])


@router.get("", response_model=ReviewQueue)
async def review_queue(
    batch: str | None = Query(None, description="batch slug"),
    doc: str | None = Query(None, description="doc slug"),
    session: AsyncSession = Depends(get_session),
):
    """Docs that need review: oldest batch first, then filename."""
    q = (
        select(Doc)
        .join(Batch)
        .where(Doc.status == DocStatus.review)
        .options(selectinload(Doc.batch))
        .order_by(Batch.created_at, Batch.id, Doc.filename)
    )
    batch_id = doc_id = None
    if doc:
        d = await get_doc(session, doc)
        if not d:
            raise HTTPException(404, "doc not found")
        doc_id = d.id
        q = q.where(Doc.id == doc_id)
    elif batch:
        b = (await session.exec(select(Batch).where(Batch.slug == batch))).first()
        if not b:
            raise HTTPException(404, "batch not found")
        batch_id = b.id
        q = q.where(Doc.batch_id == batch_id)

    docs = (await session.exec(q)).all()
    files_left, flags_left = await review_stats(session, batch_id=batch_id, doc_id=doc_id)
    return ReviewQueue(
        scope="doc" if doc else "batch" if batch else "all",
        files_left=files_left,
        flags_left=flags_left,
        docs=[
            ReviewItem(slug=d.slug, batch_slug=d.batch.slug, filename=d.filename, open_flags=d.open_flags) for d in docs
        ],
    )
