"""Run with:  python -m api.worker      (separate process from the API)"""
import logging
import time
import traceback

import httpx
from sqlalchemy import delete, select, update

from backend.src.api.config import settings
from backend.src.api.db import Doc, Flag, SessionLocal, init, now
from backend.src.api.result import load_table, make_flags


from backend.src.pipeline.models.pipeline_parts import Pipeline, PipelineTask
from backend.src.pipeline.steps import ImgExtractor, TableExtractor, LlmExtractor

log = logging.getLogger("worker")


def build_pipeline():
    pipeline = Pipeline()
    pipeline.add_step(ImgExtractor()).add_step(TableExtractor()).add_step(LlmExtractor()).setup()
    return pipeline


def notify(event: dict):
    """Tell the API process to broadcast. Losing one is fine: the DB is the truth and clients refetch."""
    try:
        httpx.post(f"{settings.api_url}/internal/events", json=event,
                   headers={"x-token": settings.internal_token}, timeout=2)
    except httpx.HTTPError:
        log.warning("notify failed for %s", event)


def claim():
    """Atomically take the oldest queued doc (one UPDATE ... RETURNING, so two workers can't collide)."""
    with SessionLocal.begin() as db:
        oldest = select(Doc.id).where(Doc.status == "queued").order_by(Doc.id).limit(1).scalar_subquery()
        return db.execute(
            update(Doc).where(Doc.id == oldest)
            .values(status="processing", error=None, updated_at=now())
            .returning(Doc.id, Doc.slug, Doc.dir)
            .execution_options(synchronize_session=False)
        ).first()


def finish(doc_id: int, status: str, flags: list[dict], error: str | None):
    with SessionLocal.begin() as db:                       # one transaction: flags + status together
        db.execute(delete(Flag).where(Flag.doc_id == doc_id))
        db.add_all(Flag(doc_id=doc_id, row_idx=f["row"], col_idx=f["col"], kind=f["kind"],
                        msg=f["msg"], why=f["why"], suggestion=f["suggestion"]) for f in flags)
        db.execute(update(Doc).where(Doc.id == doc_id)
                   .values(status=status, flags=len(flags), error=error, updated_at=now()))


def main():
    logging.basicConfig(level=logging.INFO)
    init()
    with SessionLocal.begin() as db:                       # recover after a crash
        db.execute(update(Doc).where(Doc.status == "processing").values(status="queued"))
    pipeline = build_pipeline()                            # models load once
    log.info("worker ready")
    while True:
        doc = claim()
        if not doc:
            time.sleep(1)
            continue
        notify({"type": "doc.updated", "doc": doc.slug, "status": "processing", "flags": 0})
        out_dir = settings.data_dir / doc.dir
        try:
            files = sorted((out_dir / "input").iterdir())
            pipeline.run_from_task(PipelineTask(files=files, output_directory=out_dir))
            flags = make_flags(load_table(out_dir))
            status, error = ("review" if flags else "done"), None
        except Exception:
            log.exception("doc %s failed", doc.slug)
            flags, status, error = [], "error", traceback.format_exc()[-2000:]
        finish(doc.id, status, flags, error)
        notify({"type": "doc.updated", "doc": doc.slug, "status": status, "flags": len(flags)})   # after commit


if __name__ == "__main__":
    main()
