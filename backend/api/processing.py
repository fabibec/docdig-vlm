"""Document processing in a process pool.

- Unit of work = one Doc (file). Batches are split into docs and spread over the pool.
- Workers never touch the DB: they write artifacts and return a DocResult (JSON).
  Progress comes back over a multiprocessing queue, read by a thread and handed to the loop.
- This process is the only DB writer; it persists results and broadcasts WS events.
"""

import asyncio
import logging
import multiprocessing as mp
import threading
import traceback
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime

from sqlmodel import col, delete, select, update

from backend.api import worker
from backend.api.config import settings
from backend.api.db import SessionLocal
from backend.api.enums import ArtifactState, DocStatus
from backend.api.flags import make_flags, row_values
from backend.api.hub import hub
from backend.api.models import Doc, Flag, Page, Row
from backend.api.queries import doc_events, load_doc
from backend.api.schemas import Progress
from backend.api.storage import store
from backend.pipeline.models.results import DocResult

log = logging.getLogger(__name__)


class Processor:
    def __init__(self, pool_size: int):
        self.pool_size = pool_size
        self._ctx = mp.get_context("spawn")  # never fork a process that runs an event loop
        self._pool: ProcessPoolExecutor | None = None
        self._queue = None
        self._reader: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._slots = asyncio.Semaphore(pool_size)
        self._tasks: set[asyncio.Task] = set()

    # ---------- lifecycle ----------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = self._ctx.Queue()
        self._pool = ProcessPoolExecutor(
            max_workers=self.pool_size,
            mp_context=self._ctx,
            initializer=worker.init_worker,
            initargs=(self._queue,),
        )
        self._reader = threading.Thread(target=self._read_progress, name="progress-reader", daemon=True)
        self._reader.start()

        async with SessionLocal() as session:  # recover after a crash/restart
            await session.exec(
                update(Doc).where(col(Doc.status) == DocStatus.processing).values(status=DocStatus.queued)
            )
            await session.commit()
            queued = (
                await session.exec(select(Doc.id).where(Doc.status == DocStatus.queued).order_by(Doc.id))
            ).all()
        self.enqueue(list(queued))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)
        if self._queue:
            self._queue.put(None)

    # ---------- jobs ----------

    def enqueue(self, doc_ids: list[int]) -> None:
        for doc_id in doc_ids:
            t = asyncio.create_task(self._process(doc_id))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    async def _process(self, doc_id: int) -> None:
        async with self._slots:
            async with SessionLocal() as session:
                doc = await load_doc(session, doc_id)
                doc.status, doc.error, doc.updated_at = DocStatus.processing, None, datetime.now(UTC)
                await session.commit()
                await hub.broadcast(*await doc_events(session, doc))
                input_pdf = store.input_pdf(doc.rel_dir, doc.filename)
                out_dir = store.doc_dir(doc.rel_dir)
            try:
                result_json = await self._loop.run_in_executor(
                    self._pool, worker.run_doc, doc_id, str(input_pdf), str(out_dir)
                )
                await self._persist(doc_id, DocResult.model_validate_json(result_json))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("doc %s failed", doc_id)
                await self._fail(doc_id, exc)

    async def _persist(self, doc_id: int, result: DocResult) -> None:
        async with SessionLocal() as session:
            doc = await load_doc(session, doc_id)
            page_ids = select(Page.id).where(Page.doc_id == doc_id)
            await session.exec(delete(Row).where(col(Row.page_id).in_(page_ids)))
            await session.exec(delete(Page).where(col(Page.doc_id) == doc_id))
            await session.exec(delete(Flag).where(col(Flag.doc_id) == doc_id))

            n_flags = 0
            for pr in result.pages:
                g = pr.geometry
                page = Page(doc_id=doc_id, num=pr.num, width=g.width, height=g.height,
                            col_bounds=g.col_bounds, page_confidence=pr.table.page_confidence)
                session.add(page)
                await session.flush()
                for r in pr.table.rows:
                    y0, y1 = g.row_bounds[r.row_number - 1] if 0 < r.row_number <= len(g.row_bounds) else (0, 0)
                    session.add(Row(page_id=page.id, idx=r.row_number, y0=y0, y1=y1, values=row_values(r)))
                for f in make_flags(pr):
                    session.add(Flag(doc_id=doc_id, page_num=pr.num, **f))
                    n_flags += 1

            if not result.pages:
                doc.status, doc.error = DocStatus.error, "no page produced a result"
            else:
                doc.status = DocStatus.review if n_flags else DocStatus.done
            doc.num_pages = doc.num_pages or len(result.pages)
            doc.open_flags = n_flags
            doc.updated_at = datetime.now(UTC)
            await session.commit()
            await hub.broadcast(*await doc_events(session, doc))
        await store.after_processing(doc.rel_dir)

    async def _fail(self, doc_id: int, exc: BaseException) -> None:
        async with SessionLocal() as session:
            doc = await load_doc(session, doc_id)
            doc.status = DocStatus.error
            doc.error = "".join(traceback.format_exception_only(exc)).strip()[-2000:]
            doc.updated_at = datetime.now(UTC)
            await session.commit()
            await hub.broadcast(*await doc_events(session, doc))

    # ---------- progress ----------

    def _read_progress(self) -> None:
        """Thread: block on the mp queue and forward to the event loop."""
        while (item := self._queue.get()) is not None:
            asyncio.run_coroutine_threadsafe(self._on_progress(*item), self._loop)

    async def _on_progress(self, doc_id: int, step: str, page: int, pages: int) -> None:
        async with SessionLocal() as session:
            doc = await load_doc(session, doc_id)
            if step == ArtifactState.raw and doc.num_pages != pages:
                doc.num_pages = pages
                await session.commit()
            events = await doc_events(session, doc, Progress(step=ArtifactState(step), page=page, pages=pages))
        await hub.broadcast(events[0])  # only the DocEvent; counts don't change mid-run


processor = Processor(settings.pool_size)
