import logging
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.api import models  # noqa: F401  (registers tables)
from backend.api.config import settings

log = logging.getLogger(__name__)

engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}", connect_args={"timeout": 10})


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(conn, _):
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _migrate_columns(conn) -> None:
    batch_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(batch)").fetchall()}
    if batch_cols and "deleted_at" not in batch_cols:
        conn.exec_driver_sql("ALTER TABLE batch ADD COLUMN deleted_at DATETIME")
        log.info("db migration: added column batch.deleted_at", extra={"table": "batch", "column": "deleted_at"})

    doc_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(doc)").fetchall()}
    if doc_cols and "total_cost" not in doc_cols:
        conn.exec_driver_sql("ALTER TABLE doc ADD COLUMN total_cost REAL")
        log.info("db migration: added column doc.total_cost", extra={"table": "doc", "column": "total_cost"})
        if "cost_per_page" in doc_cols:  # backfill from the old per-page average
            res = conn.exec_driver_sql(
                "UPDATE doc SET total_cost = cost_per_page * num_pages "
                "WHERE cost_per_page IS NOT NULL AND num_pages IS NOT NULL"
            )
            log.info(
                "db migration: backfilled doc.total_cost for %d docs",
                res.rowcount,
                extra={"table": "doc", "column": "total_cost"},
            )

    page_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(page)").fetchall()}
    if page_cols and "estimated_cost" not in page_cols:
        conn.exec_driver_sql("ALTER TABLE page ADD COLUMN estimated_cost REAL")
        log.info("db migration: added column page.estimated_cost", extra={"table": "page", "column": "estimated_cost"})


async def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.batches_dir.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)  # swap for Alembic once the schema settles
        await conn.run_sync(_migrate_columns)
    log.info("database ready at %s", settings.db_path, extra={"db_path": str(settings.db_path)})


async def get_session() -> AsyncIterator[AsyncSession]:  # FastAPI dependency
    async with SessionLocal() as session:
        yield session
