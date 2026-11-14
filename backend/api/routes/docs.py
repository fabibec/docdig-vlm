import logging
from datetime import UTC, datetime
from pathlib import PurePath

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from tabulate import tabulate

from backend.api.db import get_session
from backend.api.enums import COLUMN_GRID_SPAN, COLUMNS, ArtifactState, ColumnKey, DocStatus, FlagSource
from backend.api.hub import hub
from backend.api.models import Doc, Flag, Page, Row
from backend.api.queries import batch_outs, doc_events, doc_out, get_doc, refresh_doc_flags
from backend.api.schemas import (
    BatchEvent,
    DocDetail,
    DocEvent,
    DocOut,
    DocUpdate,
    FlagChangeOut,
    FlagCreate,
    FlagIds,
    FlagOut,
    PageInfo,
    PageRows,
    PageUpdate,
    ResolveOut,
    RowOut,
    RowUpdate,
)
from backend.api.storage import store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/docs", tags=["docs"])


async def _doc(session: AsyncSession, slug: str) -> Doc:
    doc = await get_doc(session, slug)
    if not doc or doc.batch.deleted_at is not None:
        log.debug("doc %s not found", slug, extra={"doc": slug})
        raise HTTPException(404, "doc not found")
    return doc


async def _page(session: AsyncSession, doc: Doc, num: int) -> Page | None:
    return (await session.exec(select(Page).where(Page.doc_id == doc.id, Page.num == num))).first()


async def _rows(session: AsyncSession, page: Page) -> list[Row]:
    return list((await session.exec(select(Row).where(Row.page_id == page.id).order_by(Row.idx))).all())


def _column_x(page: Page) -> dict[ColumnKey, tuple[int, int]]:
    b = page.col_bounds
    out = {}
    for key, (first, last) in COLUMN_GRID_SPAN.items():
        out[key] = (b[first], b[last + 1]) if last + 1 < len(b) else (0, page.width)
    return out


def _row_out(row: Row) -> RowOut:
    return RowOut(
        idx=row.idx,
        y0=row.y0,
        y1=row.y1,
        values={c: row.effective().get(c.value, "") for c in COLUMNS},
        corrected=[ColumnKey(c) for c in row.corrections],
    )


def _n_changed(before: dict[str, str], after: dict[str, str]) -> int:
    """Number of cells whose correction was added, changed or removed."""
    return sum(before.get(k) != after.get(k) for k in before.keys() | after.keys())


def _flag_out(f: Flag) -> FlagOut:
    return FlagOut(
        id=f.id,
        page=f.page_num,
        row=f.row_idx,
        column=f.column,
        source=f.source,
        severity=f.severity,
        code=f.code,
        params=f.params,
        message=f.message,
        suggestion=f.suggestion,
        confidence=f.confidence,
        resolved=f.resolved,
    )


@router.get("/{slug}", response_model=DocDetail)
async def get_doc_detail(slug: str, session: AsyncSession = Depends(get_session)):
    doc = await _doc(session, slug)
    nums = (
        await session.exec(select(Page.num, Page.page_confidence).where(Page.doc_id == doc.id).order_by(Page.num))
    ).all()
    open_by_page = dict(
        (
            await session.exec(
                select(Flag.page_num, func.count())
                .where(Flag.doc_id == doc.id, Flag.resolved == False)
                .group_by(Flag.page_num)
            )
        ).all()
    )
    pages = [PageInfo(num=n, open_flags=open_by_page.get(n, 0), page_confidence=c) for n, c in nums]
    if not pages and doc.num_pages:  # still processing: page numbers are known, results are not
        pages = [PageInfo(num=n, open_flags=0, page_confidence=None) for n in range(1, doc.num_pages + 1)]
    return DocDetail(
        **doc_out(doc).model_dump(),
        batch_name=doc.batch.name,
        pages=pages,
        artifact_url=f"/api/docs/{doc.slug}/artifacts/{{state}}/page/{{num}}",
    )


@router.get("/{slug}/flags", response_model=list[FlagOut])
async def list_flags(
    slug: str,
    open: bool | None = Query(None, description="only open (true) / resolved (false)"),
    session: AsyncSession = Depends(get_session),
):
    doc = await _doc(session, slug)
    q = select(Flag).where(Flag.doc_id == doc.id)
    if open is not None:
        q = q.where(Flag.resolved == (not open))
    flags = (await session.exec(q.order_by(Flag.page_num, Flag.row_idx, Flag.id))).all()
    return [_flag_out(f) for f in flags]


@router.get("/{slug}/page/{num}/rows", response_model=PageRows)
async def page_rows(slug: str, num: int, session: AsyncSession = Depends(get_session)):
    doc = await _doc(session, slug)
    page = await _page(session, doc, num)
    if not page:
        raise HTTPException(404, "page not processed")
    return PageRows(
        num=num,
        width=page.width,
        height=page.height,
        columns=COLUMNS,
        column_x=_column_x(page),
        rows=[_row_out(r) for r in await _rows(session, page)],
    )


@router.post("/{slug}/page/{num}/rows/{idx}/resolve", response_model=ResolveOut)
async def resolve_row(slug: str, num: int, idx: int, body: RowUpdate, session: AsyncSession = Depends(get_session)):
    """Save a record and resolve all open flags of that row."""
    doc = await _doc(session, slug)
    page = await _page(session, doc, num)
    row = page and (await session.exec(select(Row).where(Row.page_id == page.id, Row.idx == idx))).first()
    if not row:
        log.warning(
            "resolve rejected: row %d on page %d of %s not found",
            idx,
            num,
            slug,
            extra={"batch": doc.batch.slug, "doc": slug, "page": num, "row": idx},
        )
        raise HTTPException(404, "row not found")

    corrections = dict(row.corrections)
    for key, value in body.values.items():
        value = value.strip()
        if value == row.values.get(key.value, ""):
            corrections.pop(key.value, None)  # back to the machine value
        else:
            corrections[key.value] = value
    n_changed = _n_changed(row.corrections, corrections)
    row.corrections = corrections  # reassign: JSON columns don't track in-place changes

    flags = (
        await session.exec(
            select(Flag).where(Flag.doc_id == doc.id, Flag.page_num == num, Flag.row_idx == idx, Flag.resolved == False)
        )
    ).all()
    for f in flags:
        f.resolved = True
    await session.flush()
    await refresh_doc_flags(session, doc)
    await session.commit()
    log.info(
        "resolved row %d on page %d of %s: %d cells changed, %d flags resolved",
        idx,
        num,
        slug,
        n_changed,
        len(flags),
        extra={"batch": doc.batch.slug, "doc": slug, "page": num, "row": idx},
    )

    await hub.broadcast(*await doc_events(session, doc))
    await store.after_review(doc.rel_dir)
    return ResolveOut(doc=doc_out(doc), resolved_flag_ids=[f.id for f in flags])


@router.post("/{slug}/page/{num}/resolve", response_model=ResolveOut)
async def resolve_page(slug: str, num: int, body: PageUpdate, session: AsyncSession = Depends(get_session)):
    """Save all modified rows on a page and resolve all open flags of that page."""
    doc = await _doc(session, slug)
    page = await _page(session, doc, num)
    if not page:
        log.warning(
            "resolve rejected: page %d of %s not processed",
            num,
            slug,
            extra={"batch": doc.batch.slug, "doc": slug, "page": num},
        )
        raise HTTPException(404, "page not processed")

    n_changed = n_rows = 0
    rows_by_idx = {r.idx: r for r in await _rows(session, page)}
    for idx, changes in body.rows.items():
        row = rows_by_idx.get(idx)
        if not row:
            log.warning(
                "resolve rejected: row %d on page %d of %s not found",
                idx,
                num,
                slug,
                extra={"batch": doc.batch.slug, "doc": slug, "page": num, "row": idx},
            )
            raise HTTPException(404, f"row {idx} not found")
        corrections = dict(row.corrections)
        for key, value in changes.items():
            value = value.strip()
            if value == row.values.get(key.value, ""):
                corrections.pop(key.value, None)
            else:
                corrections[key.value] = value
        if changed := _n_changed(row.corrections, corrections):
            n_changed += changed
            n_rows += 1
        row.corrections = corrections

    flags = (
        await session.exec(select(Flag).where(Flag.doc_id == doc.id, Flag.page_num == num, Flag.resolved == False))
    ).all()
    for f in flags:
        f.resolved = True
    await session.flush()
    await refresh_doc_flags(session, doc)
    await session.commit()
    log.info(
        "resolved page %d of %s: %d cells changed in %d rows, %d flags resolved",
        num,
        slug,
        n_changed,
        n_rows,
        len(flags),
        extra={"batch": doc.batch.slug, "doc": slug, "page": num},
    )

    await hub.broadcast(*await doc_events(session, doc))
    await store.after_review(doc.rel_dir)
    return ResolveOut(doc=doc_out(doc), resolved_flag_ids=[f.id for f in flags])


# ---------- reviewer annotations + undo ----------

REVIEWABLE = (DocStatus.review, DocStatus.done)


async def _flags_changed(session: AsyncSession, doc: Doc) -> None:
    """Recompute doc status/open_flags, commit, notify clients and the store."""
    await session.flush()
    await refresh_doc_flags(session, doc)
    await session.commit()
    await hub.broadcast(*await doc_events(session, doc))
    await store.after_review(doc.rel_dir)


@router.post("/{slug}/flags", response_model=FlagChangeOut, status_code=201)
async def create_flag(slug: str, body: FlagCreate, session: AsyncSession = Depends(get_session)):
    """Add a reviewer annotation (source=user) to any row. A `done` doc goes back to `review`."""
    doc = await _doc(session, slug)
    ctx = {"batch": doc.batch.slug, "doc": slug, "page": body.page, "row": body.row}
    if doc.status not in REVIEWABLE:
        log.warning("note rejected: %s is %s, not processed yet", slug, doc.status, extra=ctx)
        raise HTTPException(409, "doc is not processed yet")
    page = await _page(session, doc, body.page)
    row = page and (await session.exec(select(Row).where(Row.page_id == page.id, Row.idx == body.row))).first()
    if not row:
        log.warning("note rejected: row %d on page %d of %s not found", body.row, body.page, slug, extra=ctx)
        raise HTTPException(404, "row not found")
    flag = Flag(
        doc_id=doc.id,
        page_num=body.page,
        row_idx=body.row,
        column=body.column,
        source=FlagSource.user,
        severity=body.severity,
        message=body.message.strip(),
    )
    session.add(flag)
    await _flags_changed(session, doc)
    log.info(
        "created note %d on page %d row %d of %s (%s)",
        flag.id,
        body.page,
        body.row,
        slug,
        body.severity,
        extra={**ctx, "flag": flag.id},
    )
    return FlagChangeOut(doc=doc_out(doc), flags=[_flag_out(flag)])


@router.delete("/{slug}/flags/{flag_id}", response_model=FlagChangeOut)
async def delete_flag(slug: str, flag_id: int, session: AsyncSession = Depends(get_session)):
    """Remove a reviewer annotation. Machine flags (llm/rule) can only be resolved, not deleted."""
    doc = await _doc(session, slug)
    flag = await session.get(Flag, flag_id)
    ctx = {"batch": doc.batch.slug, "doc": slug, "flag": flag_id}
    if not flag or flag.doc_id != doc.id:
        log.warning("note delete rejected: flag %d not found in %s", flag_id, slug, extra=ctx)
        raise HTTPException(404, "flag not found")
    if flag.source is not FlagSource.user:
        log.warning("note delete rejected: flag %d of %s is a %s flag", flag_id, slug, flag.source, extra=ctx)
        raise HTTPException(409, "only user annotations can be deleted")
    page_num, row_idx = flag.page_num, flag.row_idx
    await session.delete(flag)
    await _flags_changed(session, doc)
    log.info(
        "deleted note %d on page %d row %s of %s",
        flag_id,
        page_num,
        row_idx,
        slug,
        extra={**ctx, "page": page_num, "row": row_idx},
    )
    return FlagChangeOut(doc=doc_out(doc), flags=[])


@router.post("/{slug}/flags/reopen", response_model=FlagChangeOut)
async def reopen_flags(slug: str, body: FlagIds, session: AsyncSession = Depends(get_session)):
    """Undo a resolve: mark the given flags open again (e.g. `resolved_flag_ids` of a resolve call)."""
    doc = await _doc(session, slug)
    flags = (await session.exec(select(Flag).where(Flag.doc_id == doc.id, col(Flag.id).in_(body.ids)))).all()
    if len(flags) != len(set(body.ids)):
        missing = sorted(set(body.ids) - {f.id for f in flags})
        log.warning(
            "reopen rejected: flags %s not found in %s",
            missing,
            slug,
            extra={"batch": doc.batch.slug, "doc": slug, "flags": missing},
        )
        raise HTTPException(404, "flag not found")
    for f in flags:
        f.resolved = False
    await _flags_changed(session, doc)
    log.info(
        "reopened %d flags of %s",
        len(flags),
        slug,
        extra={"batch": doc.batch.slug, "doc": slug, "flags": [f.id for f in flags]},
    )
    return FlagChangeOut(doc=doc_out(doc), flags=[_flag_out(f) for f in flags])


def _markdown(rows: list[Row]) -> str:
    table = [[r.idx, *(r.effective().get(c.value, "") for c in COLUMNS)] for r in rows]
    return tabulate(table, headers=["#", *(c.value for c in COLUMNS)], tablefmt="pipe", disable_numparse=True) + "\n"


@router.get(
    "/{slug}/artifacts/{state}/page/{num}",
    response_class=FileResponse,
    responses={200: {"content": {"image/jpeg": {}, "text/markdown": {}}}},
)
async def artifact(slug: str, state: ArtifactState, num: int, session: AsyncSession = Depends(get_session)):
    """raw / cropped: page image (streamed). processed: markdown table incl. human corrections."""
    doc = await _doc(session, slug)
    if state is ArtifactState.processed:
        page = await _page(session, doc, num)
        if page:
            log.debug("serving processed markdown page %d of %s", num, slug, extra={"doc": slug, "page": num})
            return PlainTextResponse(_markdown(await _rows(session, page)), media_type="text/markdown")
        path = store.artifact(doc.rel_dir, state, num, ".md")
        media = "text/markdown"
    else:
        path = store.artifact(doc.rel_dir, state, num, ".jpg")
        media = "image/jpeg"
    if not path.is_file():
        log.debug("artifact %s page %d of %s not found", state, num, slug, extra={"doc": slug, "page": num})
        raise HTTPException(404, "artifact not found")
    log.debug("serving %s artifact page %d of %s", state, num, slug, extra={"doc": slug, "page": num})
    return FileResponse(path, media_type=media)


@router.patch("/{slug}", response_model=DocOut)
async def update_doc(slug: str, body: DocUpdate, session: AsyncSession = Depends(get_session)):
    doc = await _doc(session, slug)
    clean_name = PurePath(body.filename.strip()).name
    ctx = {"batch": doc.batch.slug, "doc": slug}
    if not clean_name or clean_name == ".pdf":
        log.warning("doc rename rejected: invalid filename %r for %s", body.filename, slug, extra=ctx)
        raise HTTPException(422, "invalid filename")
    if not clean_name.lower().endswith(".pdf"):
        clean_name = f"{clean_name}.pdf"

    existing = (
        await session.exec(
            select(Doc.id).where(Doc.batch_id == doc.batch_id, Doc.filename == clean_name, Doc.id != doc.id)
        )
    ).first()
    if existing:
        log.warning(
            "doc rename rejected: %r already exists in batch %s",
            clean_name,
            doc.batch.slug,
            extra={**ctx, "file": clean_name},
        )
        raise HTTPException(409, "A document with this filename already exists in this batch")

    if clean_name != doc.filename:
        old_path = store.input_pdf(doc.rel_dir, doc.filename)
        new_path = store.input_pdf(doc.rel_dir, clean_name)
        if old_path.is_file():
            old_path.rename(new_path)
        else:
            log.warning("doc rename: input file %r of %s missing on disk", doc.filename, slug, extra=ctx)
        old_name = doc.filename
        doc.filename = clean_name
        doc.updated_at = datetime.now(UTC)
        await session.commit()
        log.info("renamed doc %s: %r -> %r", slug, old_name, clean_name, extra={**ctx, "file": clean_name})

    (batch_out,) = await batch_outs(session, [doc.batch])
    out = doc_out(doc)
    await hub.broadcast(
        DocEvent(type="doc.status", doc=out),
        BatchEvent(type="batch.updated", batch=batch_out),
    )
    return out


@router.get("/{slug}/file", response_class=FileResponse)
async def get_doc_file(slug: str, session: AsyncSession = Depends(get_session)):
    doc = await _doc(session, slug)
    path = store.input_pdf(doc.rel_dir, doc.filename)
    if not path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(
        path=path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{doc.filename}"'},
    )
