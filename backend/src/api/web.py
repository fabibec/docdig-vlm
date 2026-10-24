import asyncio
import shutil
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.src.api.config import settings
from backend.src.api.db import Batch, Correction, Doc, Flag, get_db, init, now, slugify
from backend.src.api.hub import hub
from backend.src.api.result import COLS, load_table


@asynccontextmanager
async def lifespan(app: FastAPI):
    init()
    hub.loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="Ringlist API", lifespan=lifespan)
app.openapi_version = "3.0.2"
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _doc(db: Session, slug: str) -> Doc:
    d = db.scalar(select(Doc).where(Doc.slug == slug))
    if not d:
        raise HTTPException(404, "doc not found")
    return d


# ---------- batches ----------
@app.post("/batches", status_code=201)
def create_batch(
    name: str = Form(...),
    from_folder: bool = Form(False),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    base = f"{date.today():%Y%m%d}-{slugify(name) or 'batch'}"
    slug, n = base, 1
    while db.scalar(select(Batch.id).where(Batch.slug == slug)):
        n += 1
        slug = f"{base}-{n}"
    batch = Batch(slug=slug, name=name, from_folder=from_folder)
    for i, f in enumerate(sorted(files, key=lambda f: f.filename or ""), 1):
        doc_slug = f"{slug}-p{i:03d}"
        rel = f"{slug}/{doc_slug}"
        inp = settings.data_dir / rel / "input"
        inp.mkdir(parents=True, exist_ok=True)
        fname = Path(f.filename or f"page{i}.pdf").name
        with open(inp / fname, "wb") as out:
            shutil.copyfileobj(f.file, out)
        batch.docs.append(Doc(slug=doc_slug, dir=rel, filename=fname))
    db.add(batch)
    db.commit()  # all docs become visible to the worker at once, after their files are on disk
    hub.emit({"type": "batch.created", "batch": slug})
    return {"slug": slug}


@app.get("/batches")
def list_batches(db: Session = Depends(get_db)):
    batches = db.scalars(
        select(Batch).options(selectinload(Batch.docs)).order_by(Batch.id.desc())
    )
    return [
        {
            "slug": b.slug,
            "name": b.name,
            "from_folder": b.from_folder,
            "docs": [
                {
                    "slug": d.slug,
                    "filename": d.filename,
                    "status": d.status,
                    "flags": d.flags,
                    "error": d.error,
                }
                for d in b.docs
            ],
        }
        for b in batches
    ]


# ---------- one document: table + flags + debug file list ----------
@app.get("/docs/{slug}")
def get_doc(slug: str, db: Session = Depends(get_db)):
    d = _doc(db, slug)
    out_dir = settings.data_dir / d.dir
    table = load_table(out_dir)
    for c in db.scalars(select(Correction).where(Correction.doc_id == d.id)):
        table[c.row_idx][c.col_idx] = c.value  # human edits win
    files = [
        {"path": str(p.relative_to(settings.data_dir)), "size": p.stat().st_size}
        for p in sorted(out_dir.rglob("*"))
        if p.is_file()
    ]
    flags = [
        {
            "id": f.id,
            "row": f.row_idx,
            "col": f.col_idx,
            "kind": f.kind,
            "msg": f.msg,
            "why": f.why,
            "suggestion": f.suggestion,
            "done": f.done,
        }
        for f in d.flag_items
    ]
    return {
        "slug": d.slug,
        "filename": d.filename,
        "status": d.status,
        "error": d.error,
        "columns": COLS,
        "table": table,
        "flags": flags,
        "files": files,
    }


class Resolve(BaseModel):
    value: str | None = None  # corrected cell value; omit to accept the machine value


@app.post("/docs/{slug}/flags/{flag_id}/resolve")
def resolve_flag(slug: str, flag_id: int, body: Resolve, db: Session = Depends(get_db)):
    d = _doc(db, slug)
    flag = db.get(Flag, flag_id)
    if not flag or flag.doc_id != d.id:
        raise HTTPException(404, "flag not found")
    if body.value is not None:
        db.merge(
            Correction(
                doc_id=d.id,
                row_idx=flag.row_idx,
                col_idx=flag.col_idx,
                value=body.value,
            )
        )
    flag.done = True
    db.flush()
    left = db.scalar(
        select(func.count())
        .select_from(Flag)
        .where(Flag.doc_id == d.id, Flag.done.is_(False))
    )
    d.flags, d.status, d.updated_at = left, ("review" if left else "done"), now()
    db.commit()
    hub.emit({"type": "doc.updated", "doc": slug, "status": d.status, "flags": left})
    return {"flags": left, "status": d.status}


# ---------- debug: serve any file under DATA_DIR ----------
@app.get("/artifacts/{path:path}")
def artifact(path: str):
    p = (settings.data_dir / path).resolve()
    if not p.is_relative_to(settings.data_dir) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


# ---------- realtime ----------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):  # TODO: check an auth token here
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)


@app.post("/internal/events")  # called by the worker process only
async def internal_event(event: dict, x_token: str = Header()):
    if x_token != settings.internal_token:
        raise HTTPException(403)
    await hub.broadcast(event)
    return {"ok": True}
