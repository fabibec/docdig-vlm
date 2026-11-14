"""backend/api/db.py: schema migration of old databases, pragmas, init_db."""

import asyncio

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.ext.asyncio import create_async_engine

import backend.api.db as api_db
from backend.api.config import Settings
from backend.api.db import _migrate_columns


@pytest.fixture
def sqlite(tmp_path):
    """A sync SQLite engine on a fresh file, disposed afterwards."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    yield engine
    engine.dispose()


@pytest.fixture
def old_db(sqlite):
    """A DB as created before deleted_at / total_cost / estimated_cost existed."""
    engine = sqlite
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE batch (id INTEGER PRIMARY KEY, slug TEXT, name TEXT)")
        conn.exec_driver_sql(
            "CREATE TABLE doc (id INTEGER PRIMARY KEY, slug TEXT, filename TEXT, num_pages INTEGER, cost_per_page REAL)"
        )
        conn.exec_driver_sql("CREATE TABLE page (id INTEGER PRIMARY KEY, doc_id INTEGER, num INTEGER)")
        conn.exec_driver_sql("INSERT INTO doc VALUES (1, 'a', 'a.pdf', 4, 0.25), (2, 'b', 'b.pdf', NULL, 0.5)")
        conn.exec_driver_sql("INSERT INTO doc VALUES (3, 'c', 'c.pdf', 2, NULL)")
    return engine


def _columns(engine, table):
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_migrate_columns_adds_new_columns_and_backfills_cost(old_db):
    engine = old_db
    with engine.begin() as conn:
        _migrate_columns(conn)

    assert "deleted_at" in _columns(engine, "batch")
    assert "total_cost" in _columns(engine, "doc")
    assert "estimated_cost" in _columns(engine, "page")
    with engine.connect() as conn:
        costs = conn.exec_driver_sql("SELECT id, total_cost FROM doc ORDER BY id").fetchall()
    assert costs == [(1, 1.0), (2, None), (3, None)]


def test_migrate_columns_is_idempotent(old_db):
    engine = old_db
    with engine.begin() as conn:
        _migrate_columns(conn)
        conn.exec_driver_sql("UPDATE doc SET total_cost = 9 WHERE id = 1")
    with engine.begin() as conn:
        _migrate_columns(conn)  # second run: no ALTER, no re-backfill
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT total_cost FROM doc WHERE id = 1").scalar() == 9


def test_migrate_columns_on_empty_db_does_nothing(sqlite):
    engine = sqlite
    with engine.begin() as conn:
        _migrate_columns(conn)
    assert inspect(engine).get_table_names() == []


def test_init_db_creates_schema_and_folders(tmp_path, monkeypatch):
    """Runs the real init_db() + engine (WAL pragmas) against a fresh data dir."""
    data = tmp_path / "data"
    test_settings = Settings(data_dir=data, db_path=data / "state" / "app.db")  # ignore WEB_DB_PATH
    monkeypatch.setattr(api_db, "settings", test_settings)
    engine = create_async_engine(f"sqlite+aiosqlite:///{test_settings.db_path}")
    event.listen(engine.sync_engine, "connect", api_db._sqlite_pragmas)
    monkeypatch.setattr(api_db, "engine", engine)

    async def go():
        await api_db.init_db()
        await api_db.init_db()  # restart on an existing DB
        async with engine.connect() as conn:
            mode = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()
            fks = (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar()
            tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
        await engine.dispose()
        return mode, fks, tables

    mode, fks, tables = asyncio.run(go())

    assert test_settings.db_path == (tmp_path / "data" / "state" / "app.db").resolve()
    assert test_settings.db_path.is_file()
    assert test_settings.batches_dir.is_dir()
    assert mode == "wal"
    assert fks == 1
    assert {"batch", "doc", "page", "row", "flag"} <= set(tables)
