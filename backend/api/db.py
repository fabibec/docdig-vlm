from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.api import models  # noqa: F401  (registers tables)
from backend.api.config import settings

engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}", connect_args={"timeout": 10})


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(conn, _):
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.batches_dir.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)  # swap for Alembic once the schema settles


async def get_session() -> AsyncIterator[AsyncSession]:  # FastAPI dependency
    async with SessionLocal() as session:
        yield session
