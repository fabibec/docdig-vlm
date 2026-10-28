import shutil
from datetime import date
from pathlib import PurePath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.api.auth import User, current_user
from backend.api.config import settings
from backend.api.db import get_session
from backend.api.models import Batch, Doc
from backend.api.processing import processor
from backend.api.queries import batch_outs, doc_out, review_stats
from backend.api.hub import hub
from backend.api.schemas import BatchEvent, BatchOut, DocOut, DuplicateFiles, Paginated, ReviewEvent
from backend.api.storage import FileTooLarge, slugify, store

router = APIRouter(prefix="/batches", tags=["batches"])

PageNum = Query(1, ge=1)
PageSize = Query(10, ge=1, le=100)


async def _batch(session: AsyncSession, slug: str) -> Batch:
    batch = (await session.exec(select(Batch).where(Batch.slug == slug))).first()
    if not batch:
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
        raise HTTPException(422, f"only PDF files are accepted: {bad}")
    if dupes := sorted({n for n in filenames if filenames.count(n) > 1}):
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
    try:
        for doc in docs:
            await store.save_upload(by_name[doc.filename], f"{slug}/{doc.slug}", doc.filename,
                                    settings.max_upload_mb * 1024 * 1024)
        await session.commit()  # docs become visible only after their files are on disk
    except FileTooLarge as exc:
        await session.rollback()
        shutil.rmtree(store.doc_dir(slug), ignore_errors=True)
        raise HTTPException(413, f"{exc} exceeds {settings.max_upload_mb} MB") from None
    except BaseException:
        await session.rollback()
        shutil.rmtree(store.doc_dir(slug), ignore_errors=True)
        raise

    for doc in docs:
        await store.after_upload(f"{slug}/{doc.slug}")
    processor.enqueue([d.id for d in docs])

    (out,) = await batch_outs(session, [batch])
    files_left, flags_left = await review_stats(session)
    await hub.broadcast(BatchEvent(type="batch.created", batch=out),
                        ReviewEvent(files_left=files_left, flags_left=flags_left))
    return out


@router.get("", response_model=Paginated[BatchOut])
async def list_batches(page: int = PageNum, size: int = PageSize, session: AsyncSession = Depends(get_session)):
    total = (await session.exec(select(func.count()).select_from(Batch))).one()
    batches = (
        await session.exec(
            select(Batch).order_by(Batch.created_at.desc(), Batch.id.desc()).offset((page - 1) * size).limit(size)
        )
    ).all()
    return Paginated[BatchOut](items=await batch_outs(session, list(batches)), total=total, page=page, size=size)


@router.get("/{slug}", response_model=BatchOut)
async def get_batch(slug: str, session: AsyncSession = Depends(get_session)):
    (out,) = await batch_outs(session, [await _batch(session, slug)])
    return out


@router.get("/{slug}/docs", response_model=Paginated[DocOut])
async def list_docs(slug: str, page: int = PageNum, size: int = PageSize,
                    session: AsyncSession = Depends(get_session)):
    batch = await _batch(session, slug)
    total = (await session.exec(select(func.count()).select_from(Doc).where(Doc.batch_id == batch.id))).one()
    docs = (
        await session.exec(
            select(Doc).where(Doc.batch_id == batch.id).order_by(Doc.filename).offset((page - 1) * size).limit(size)
        )
    ).all()
    for d in docs:
        d.batch = batch
    return Paginated[DocOut](items=[doc_out(d) for d in docs], total=total, page=page, size=size)
