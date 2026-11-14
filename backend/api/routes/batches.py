import logging
import shutil
from datetime import UTC, date, datetime
from pathlib import PurePath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.api.auth import User, current_user
from backend.api.config import settings
from backend.api.db import get_session
from backend.api.hub import hub
from backend.api.models import Batch, Doc
from backend.api.processing import processor
from backend.api.queries import batch_outs, doc_out, review_stats
from backend.api.schemas import BatchEvent, BatchOut, BatchUpdate, DocOut, DuplicateFiles, Paginated, ReviewEvent
from backend.api.storage import FileTooLarge, slugify, store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/batches", tags=["batches"])

PageNum = Query(1, ge=1)
PageSize = Query(10, ge=1, le=100)


async def _batch(session: AsyncSession, slug: str) -> Batch:
    batch = (await session.exec(select(Batch).where(Batch.slug == slug, Batch.deleted_at.is_(None)))).first()
    if not batch:
        log.debug("batch %s not found", slug, extra={"batch": slug})
        raise HTTPException(404, "batch not found")
    return batch


async def _unique_slug(session: AsyncSession, model, base: str) -> str:
    slug, n = base, 1
    while (await session.exec(select(model.id).where(model.slug == slug))).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


@router.post(
    "",
    status_code=201,
    response_model=BatchOut,
    responses={409: {"model": DuplicateFiles}, 413: {"description": "file too large"}},
)
async def create_batch(
    name: str = Form(..., min_length=1, max_length=120),
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    filenames = [PurePath(f.filename or "").name for f in files]
    if bad := [n for n in filenames if not n.lower().endswith(".pdf")]:
        log.warning("rejected upload %r: non-PDF files %s", name, bad, extra={"files": bad})
        raise HTTPException(422, f"only PDF files are accepted: {bad}")
    if dupes := sorted({n for n in filenames if filenames.count(n) > 1}):
        log.warning("rejected upload %r: duplicate files %s", name, dupes, extra={"files": dupes})
        return JSONResponse(DuplicateFiles(filenames=dupes).model_dump(), status_code=409)

    slug = await _unique_slug(session, Batch, f"{date.today():%Y%m%d}-{slugify(name) or 'batch'}")
    batch = Batch(slug=slug, name=name.strip(), owner=user.name)
    session.add(batch)

    used: set[str] = set()
    docs: list[Doc] = []
    for fname in sorted(filenames):
        base = f"{slug}-{slugify(PurePath(fname).stem) or 'file'}"
        doc_slug, n = base, 1
        while doc_slug in used:
            n += 1
            doc_slug = f"{base}-{n}"
        used.add(doc_slug)
        docs.append(Doc(slug=doc_slug, filename=fname, batch=batch))
    session.add_all(docs)

    by_name = {PurePath(f.filename or "").name: f for f in files}
    total_bytes = 0
    try:
        for doc in docs:
            path = await store.save_upload(
                by_name[doc.filename], f"{slug}/{doc.slug}", doc.filename, settings.max_upload_mb * 1024 * 1024
            )
            total_bytes += path.stat().st_size
        await session.commit()  # docs become visible only after their files are on disk
    except FileTooLarge as exc:
        await session.rollback()
        shutil.rmtree(store.doc_dir(slug), ignore_errors=True)
        raise HTTPException(413, f"{exc} exceeds {settings.max_upload_mb} MB") from None
    except BaseException as exc:
        await session.rollback()
        shutil.rmtree(store.doc_dir(slug), ignore_errors=True)
        log.warning("upload into batch %s aborted (%r), rolled back", slug, exc, extra={"batch": slug})
        raise

    for doc in docs:
        await store.after_upload(f"{slug}/{doc.slug}")
    processor.enqueue([d.id for d in docs])
    log.info(
        "created batch %s (%r) with %d files, %.2f MB, owner %s",
        slug,
        batch.name,
        len(docs),
        total_bytes / (1024 * 1024),
        user.name,
        extra={"batch": slug, "n_files": len(docs), "bytes": total_bytes},
    )

    (out,) = await batch_outs(session, [batch])
    files_left, flags_left = await review_stats(session)
    await hub.broadcast(
        BatchEvent(type="batch.created", batch=out), ReviewEvent(files_left=files_left, flags_left=flags_left)
    )
    return out


@router.get("", response_model=Paginated[BatchOut])
async def list_batches(page: int = PageNum, size: int = PageSize, session: AsyncSession = Depends(get_session)):
    total = (await session.exec(select(func.count()).select_from(Batch).where(Batch.deleted_at.is_(None)))).one()
    batches = (
        await session.exec(
            select(Batch)
            .where(Batch.deleted_at.is_(None))
            .order_by(Batch.created_at.desc(), Batch.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()
    log.debug("listing batches page %d (size %d): %d of %d", page, size, len(batches), total)
    return Paginated[BatchOut](items=await batch_outs(session, list(batches)), total=total, page=page, size=size)


@router.get("/{slug}", response_model=BatchOut)
async def get_batch(slug: str, session: AsyncSession = Depends(get_session)):
    (out,) = await batch_outs(session, [await _batch(session, slug)])
    return out


@router.patch("/{slug}", response_model=BatchOut)
async def update_batch(slug: str, body: BatchUpdate, session: AsyncSession = Depends(get_session)):
    batch = await _batch(session, slug)
    old_name = batch.name
    batch.name = body.name.strip()
    await session.commit()
    log.info("renamed batch %s: %r -> %r", slug, old_name, batch.name, extra={"batch": slug})
    (out,) = await batch_outs(session, [batch])
    await hub.broadcast(BatchEvent(type="batch.updated", batch=out))
    return out


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_batch(slug: str, session: AsyncSession = Depends(get_session)):
    batch = await _batch(session, slug)
    (out,) = await batch_outs(session, [batch])
    batch.deleted_at = datetime.now(UTC)
    await session.commit()
    log.info("soft-deleted batch %s (%r, %d docs)", slug, batch.name, out.num_docs, extra={"batch": slug})
    files_left, flags_left = await review_stats(session)
    await hub.broadcast(
        BatchEvent(type="batch.deleted", batch=out), ReviewEvent(files_left=files_left, flags_left=flags_left)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{slug}/docs", response_model=Paginated[DocOut])
async def list_docs(slug: str, page: int = PageNum, size: int = PageSize, session: AsyncSession = Depends(get_session)):
    batch = await _batch(session, slug)
    total = (await session.exec(select(func.count()).select_from(Doc).where(Doc.batch_id == batch.id))).one()
    docs = (
        await session.exec(
            select(Doc).where(Doc.batch_id == batch.id).order_by(Doc.filename).offset((page - 1) * size).limit(size)
        )
    ).all()
    for d in docs:
        d.batch = batch
    log.debug("listing docs of batch %s page %d: %d of %d", slug, page, len(docs), total, extra={"batch": slug})
    return Paginated[DocOut](items=[doc_out(d) for d in docs], total=total, page=page, size=size)
