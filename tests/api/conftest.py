"""Isolated FastAPI app for the API tests.

Every test gets its own SQLite file and artifact folder under ``tmp_path``. The real process
pool is never started and nothing is enqueued for real: ``processor.enqueue`` only records
doc ids, so the pipeline and the LLM are never called. ``hub.broadcast`` is wrapped so tests
can assert on the emitted events (``api.events``) while real WebSocket clients still get them.
"""

from collections.abc import Iterable

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import backend.api.app as api_app
import backend.api.db as api_db
import backend.api.processing as api_processing
import backend.api.routes.realtime as api_realtime
from backend.api.hub import hub
from backend.api.processing import processor
from backend.api.storage import store
from tests.api.helpers import ApiEnv, Factory


@pytest.fixture
def api(tmp_path, monkeypatch) -> Iterable[ApiEnv]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    batches_dir = tmp_path / "batches"
    batches_dir.mkdir()

    async def init_db():
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    async def no_op():
        pass

    async def override_session():
        async with session_maker() as session:
            yield session

    enqueued: list[int] = []
    events: list[BaseModel] = []
    real_broadcast = hub.broadcast

    async def recording_broadcast(*evs: BaseModel):
        events.extend(evs)
        await real_broadcast(*evs)

    monkeypatch.setattr(api_db, "engine", engine)
    monkeypatch.setattr(api_db, "SessionLocal", session_maker)
    monkeypatch.setattr(api_processing, "SessionLocal", session_maker)
    monkeypatch.setattr(api_realtime, "SessionLocal", session_maker)
    monkeypatch.setattr(store, "root", batches_dir)
    monkeypatch.setattr(api_app, "init_db", init_db)
    monkeypatch.setattr(processor, "start", no_op)
    monkeypatch.setattr(processor, "stop", no_op)
    monkeypatch.setattr(processor, "enqueue", enqueued.extend)
    monkeypatch.setattr(hub, "broadcast", recording_broadcast)
    monkeypatch.setitem(api_app.app.dependency_overrides, api_db.get_session, override_session)

    with TestClient(api_app.app) as client:
        env = ApiEnv(client, engine, session_maker, batches_dir, enqueued, events)
        try:
            yield env
        finally:
            hub.clients.clear()
            env.run(engine.dispose)


@pytest.fixture
def factory(api: ApiEnv) -> Factory:
    return Factory(api)
