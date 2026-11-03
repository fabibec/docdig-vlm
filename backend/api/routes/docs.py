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
from backend.api.queries import doc_events, doc_out, get_doc, refresh_doc_flags
from backend.api.schemas import (
    DocDetail,
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

router = APIRouter(prefix="/docs", tags=["docs"])


async def _doc(session: AsyncSession, slug: str) -> Doc:
    doc = await get_doc(session, slug)
    if not doc:
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
        raise HTTPException(404, "row not found")

    corrections = dict(row.corrections)
    for key, value in body.values.items():
        value = value.strip()
        if value == row.values.get(key.value, ""):
            corrections.pop(key.value, None)  # back to the machine value
        else:
            corrections[key.value] = value
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

    await hub.broadcast(*await doc_events(session, doc))
    await store.after_review(doc.rel_dir)
    return ResolveOut(doc=doc_out(doc), resolved_flag_ids=[f.id for f in flags])


@router.post("/{slug}/page/{num}/resolve", response_model=ResolveOut)
async def resolve_page(slug: str, num: int, body: PageUpdate, session: AsyncSession = Depends(get_session)):
    """Save all modified rows on a page and resolve all open flags of that page."""
    doc = await _doc(session, slug)
    page = await _page(session, doc, num)
    if not page:
        raise HTTPException(404, "page not processed")

    rows_by_idx = {r.idx: r for r in await _rows(session, page)}
    for idx, changes in body.rows.items():
        row = rows_by_idx.get(idx)
        if not row:
            raise HTTPException(404, f"row {idx} not found")
        corrections = dict(row.corrections)
        for key, value in changes.items():
            value = value.strip()
            if value == row.values.get(key.value, ""):
                corrections.pop(key.value, None)
            else:
                corrections[key.value] = value
        row.corrections = corrections

    flags = (
        await session.exec(select(Flag).where(Flag.doc_id == doc.id, Flag.page_num == num, Flag.resolved == False))
    ).all()
    for f in flags:
        f.resolved = True
    await session.flush()
    await refresh_doc_flags(session, doc)
    await session.commit()

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
    if doc.status not in REVIEWABLE:
        raise HTTPException(409, "doc is not processed yet")
    page = await _page(session, doc, body.page)
    row = page and (await session.exec(select(Row).where(Row.page_id == page.id, Row.idx == body.row))).first()
    if not row:
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
    return FlagChangeOut(doc=doc_out(doc), flags=[_flag_out(flag)])


@router.delete("/{slug}/flags/{flag_id}", response_model=FlagChangeOut)
async def delete_flag(slug: str, flag_id: int, session: AsyncSession = Depends(get_session)):
    """Remove a reviewer annotation. Machine flags (llm/rule) can only be resolved, not deleted."""
    doc = await _doc(session, slug)
    flag = await session.get(Flag, flag_id)
    if not flag or flag.doc_id != doc.id:
        raise HTTPException(404, "flag not found")
    if flag.source is not FlagSource.user:
        raise HTTPException(409, "only user annotations can be deleted")
    await session.delete(flag)
    await _flags_changed(session, doc)
    return FlagChangeOut(doc=doc_out(doc), flags=[])


@router.post("/{slug}/flags/reopen", response_model=FlagChangeOut)
async def reopen_flags(slug: str, body: FlagIds, session: AsyncSession = Depends(get_session)):
    """Undo a resolve: mark the given flags open again (e.g. `resolved_flag_ids` of a resolve call)."""
    doc = await _doc(session, slug)
    flags = (await session.exec(select(Flag).where(Flag.doc_id == doc.id, col(Flag.id).in_(body.ids)))).all()
    if len(flags) != len(set(body.ids)):
        raise HTTPException(404, "flag not found")
    for f in flags:
        f.resolved = False
    await _flags_changed(session, doc)
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
            return PlainTextResponse(_markdown(await _rows(session, page)), media_type="text/markdown")
        path = store.artifact(doc.rel_dir, state, num, ".md")
        media = "text/markdown"
    else:
        path = store.artifact(doc.rel_dir, state, num, ".jpg")
        media = "image/jpeg"
    if not path.is_file():
        raise HTTPException(404, "artifact not found")
    return FileResponse(path, media_type=media)
