import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

import backend.api.db as api_db
import backend.api.processing as processor_module
import backend.api.storage as api_storage
from backend.api.app import app
from backend.api.db import _migrate_columns
from backend.api.enums import DocStatus
from backend.api.models import Doc, Page
from backend.api.processing import processor
from backend.api.schemas import BatchOut, DocOut
from backend.pipeline.models.llm_responses import PageTable
from backend.pipeline.models.pipeline_parts import PipelineTask, collect_result
from backend.pipeline.models.results import DocResult, PageGeometry, PageResult


@pytest.fixture
def env(tmp_path):
    """Isolated database and storage environment for FastAPI tests."""
    db_file = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    old_engine = api_db.engine
    old_session_maker = api_db.SessionLocal
    old_proc_session_maker = processor_module.SessionLocal
    old_store_root = api_storage.store.root

    api_db.engine = engine
    api_db.SessionLocal = session_maker
    processor_module.SessionLocal = session_maker
    batches_dir = tmp_path / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    api_storage.store.root = batches_dir

    async def override_session():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[api_db.get_session] = override_session

    with patch("backend.api.routes.batches.processor.enqueue"), TestClient(app) as client:
        yield {
            "client": client,
            "session_maker": session_maker,
            "engine": engine,
            "batches_dir": batches_dir,
            "tmp_path": tmp_path,
        }

    app.dependency_overrides.pop(api_db.get_session, None)
    api_db.engine = old_engine
    api_db.SessionLocal = old_session_maker
    processor_module.SessionLocal = old_proc_session_maker
    api_storage.store.root = old_store_root


def _create_batch_with_files(client, name="Test Batch", filenames=("doc1.pdf", "doc2.pdf")):
    files = [
        ("files", (fname, io.BytesIO(f"%PDF-1.4 content of {fname}".encode()), "application/pdf"))
        for fname in filenames
    ]
    res = client.post("/api/batches", data={"name": name}, files=files)
    assert res.status_code == 201
    return res.json()


def test_batch_soft_deletion(env):
    """Soft-deleting a batch hides it from API views without deleting disk files."""
    client = env["client"]
    batches_dir = env["batches_dir"]

    # 1. Create batch with 2 documents
    batch_data = _create_batch_with_files(client, "Soft Delete Batch", ["file1.pdf", "file2.pdf"])
    batch_slug = batch_data["slug"]

    # Verify batch is listed
    res = client.get("/api/batches")
    assert res.status_code == 200
    assert any(b["slug"] == batch_slug for b in res.json()["items"])
    assert res.json()["total"] >= 1

    # Verify files exist on disk
    docs_res = client.get(f"/api/batches/{batch_slug}/docs")
    assert docs_res.status_code == 200
    docs = docs_res.json()["items"]
    assert len(docs) == 2
    for doc in docs:
        file_path = batches_dir / batch_slug / doc["slug"] / "input" / doc["filename"]
        assert file_path.is_file(), f"Expected file {file_path} to exist on disk"

    # 2. Delete batch
    del_res = client.delete(f"/api/batches/{batch_slug}")
    assert del_res.status_code == 204
    assert del_res.content == b""

    # 3. Verify batch is hidden from GET /batches
    res = client.get("/api/batches")
    assert res.status_code == 200
    assert not any(b["slug"] == batch_slug for b in res.json()["items"])

    # 4. GET /batches/{slug} and GET /batches/{slug}/docs return 404
    assert client.get(f"/api/batches/{batch_slug}").status_code == 404
    assert client.get(f"/api/batches/{batch_slug}/docs").status_code == 404

    # 5. Accessing doc details or doc file returns 404
    assert client.get(f"/api/docs/{docs[0]['slug']}").status_code == 404
    assert client.get(f"/api/docs/{docs[0]['slug']}/file").status_code == 404

    # 6. Deleting again returns 404
    assert client.delete(f"/api/batches/{batch_slug}").status_code == 404

    # 7. STRICT: Files and directories MUST still exist on disk!
    for doc in docs:
        file_path = batches_dir / batch_slug / doc["slug"] / "input" / doc["filename"]
        assert file_path.is_file(), f"Violation: Disk file {file_path} was deleted!"


def test_review_queue_hides_deleted_batch(env):
    """Documents from soft-deleted batches must not appear in review queue."""
    client = env["client"]
    session_maker = env["session_maker"]

    # Create batch
    batch_data = _create_batch_with_files(client, "Review Batch", ["review_me.pdf"])
    batch_slug = batch_data["slug"]
    doc_res = client.get(f"/api/batches/{batch_slug}/docs")
    doc_slug = doc_res.json()["items"][0]["slug"]

    # Mark doc as review status with open_flags in DB
    import asyncio

    async def _set_review():
        async with session_maker() as session:
            doc = (await session.exec(select(Doc).where(Doc.slug == doc_slug))).one()
            doc.status = DocStatus.review
            doc.open_flags = 2
            await session.commit()

    asyncio.run(_set_review())

    # Check doc is in review queue
    rq = client.get("/api/review")
    assert rq.status_code == 200
    assert any(d["slug"] == doc_slug for d in rq.json()["docs"])
    assert rq.json()["files_left"] >= 1

    # Delete the batch
    assert client.delete(f"/api/batches/{batch_slug}").status_code == 204

    # Review queue should no longer list the doc
    rq_after = client.get("/api/review")
    assert rq_after.status_code == 200
    assert not any(d["slug"] == doc_slug for d in rq_after.json()["docs"])
    assert rq_after.json()["files_left"] == 0

    # Scoped queries for the deleted batch or doc must return 404
    assert client.get(f"/api/review?batch={batch_slug}").status_code == 404
    assert client.get(f"/api/review?doc={doc_slug}").status_code == 404


def test_batch_renaming(env):
    """PATCH /api/batches/{slug} updates batch name and retains slug."""
    client = env["client"]

    batch_data = _create_batch_with_files(client, "Original Name", ["doc.pdf"])
    slug = batch_data["slug"]

    # Rename batch
    res = client.patch(f"/api/batches/{slug}", json={"name": "New Batch Name"})
    assert res.status_code == 200
    updated = res.json()
    assert updated["name"] == "New Batch Name"
    assert updated["slug"] == slug  # Slug remains identical

    # GET /batches/{slug} reflects update
    get_res = client.get(f"/api/batches/{slug}")
    assert get_res.status_code == 200
    assert get_res.json()["name"] == "New Batch Name"

    # Validation: empty name rejected with 422
    assert client.patch(f"/api/batches/{slug}", json={"name": ""}).status_code == 422

    # Renaming deleted batch returns 404
    client.delete(f"/api/batches/{slug}")
    assert client.patch(f"/api/batches/{slug}", json={"name": "Should Fail"}).status_code == 404


def test_doc_renaming(env):
    """PATCH /api/docs/{slug} renames file in DB and on disk, rejecting duplicates with 409."""
    client = env["client"]
    batches_dir = env["batches_dir"]

    batch_data = _create_batch_with_files(client, "Doc Rename Batch", ["doc_a.pdf", "doc_b.pdf"])
    batch_slug = batch_data["slug"]
    docs = client.get(f"/api/batches/{batch_slug}/docs").json()["items"]
    doc_a = next(d for d in docs if d["filename"] == "doc_a.pdf")
    doc_b = next(d for d in docs if d["filename"] == "doc_b.pdf")

    old_path_a = batches_dir / batch_slug / doc_a["slug"] / "input" / "doc_a.pdf"
    assert old_path_a.is_file()

    # Rename doc_a to doc_renamed.pdf
    res = client.patch(f"/api/docs/{doc_a['slug']}", json={"filename": "doc_renamed.pdf"})
    assert res.status_code == 200
    assert res.json()["filename"] == "doc_renamed.pdf"

    # Verify physical file rename
    new_path_a = batches_dir / batch_slug / doc_a["slug"] / "input" / "doc_renamed.pdf"
    assert not old_path_a.exists(), "Old physical file must not exist"
    assert new_path_a.is_file(), "New physical file must exist"

    # Attempt duplicate rename (renaming doc_b to doc_renamed.pdf in same batch) -> 409 Conflict
    dup_res = client.patch(f"/api/docs/{doc_b['slug']}", json={"filename": "doc_renamed.pdf"})
    assert dup_res.status_code == 409
    assert "already exists in this batch" in dup_res.json()["detail"]

    # Missing .pdf extension should be appended automatically
    no_ext_res = client.patch(f"/api/docs/{doc_a['slug']}", json={"filename": "doc_no_ext"})
    assert no_ext_res.status_code == 200
    assert no_ext_res.json()["filename"] == "doc_no_ext.pdf"

    # Path traversal attempt should be sanitized
    traversal_res = client.patch(f"/api/docs/{doc_a['slug']}", json={"filename": "../../safe_name.pdf"})
    assert traversal_res.status_code == 200
    assert traversal_res.json()["filename"] == "safe_name.pdf"

    # Invalid filename (just .pdf or empty) -> 422
    assert client.patch(f"/api/docs/{doc_a['slug']}", json={"filename": ".pdf"}).status_code == 422


def test_doc_file_serving(env):
    """GET /api/docs/{slug}/file serves the original uploaded PDF inline."""
    client = env["client"]

    pdf_bytes = b"%PDF-1.4 \x01\x02\x03\x04 test binary content"
    files = [("files", ("preview_test.pdf", io.BytesIO(pdf_bytes), "application/pdf"))]
    b_res = client.post("/api/batches", data={"name": "Preview Batch"}, files=files)
    batch_slug = b_res.json()["slug"]

    doc = client.get(f"/api/batches/{batch_slug}/docs").json()["items"][0]
    doc_slug = doc["slug"]

    # Request the file
    res = client.get(f"/api/docs/{doc_slug}/file")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"] == 'inline; filename="preview_test.pdf"'
    assert res.content == pdf_bytes

    # Non-existent doc slug returns 404
    assert client.get("/api/docs/non-existent-slug/file").status_code == 404


def test_total_cost_persistence_and_schemas(env):
    """Cost per page is calculated in _persist, exposed on DocOut, and hidden on BatchOut."""
    client = env["client"]
    session_maker = env["session_maker"]

    # Verify schema contracts
    assert "total_cost" in DocOut.model_fields
    assert "total_cost" not in BatchOut.model_fields

    batch_data = _create_batch_with_files(client, "Cost Batch", ["doc1.pdf"])
    batch_slug = batch_data["slug"]
    doc = client.get(f"/api/batches/{batch_slug}/docs").json()["items"][0]
    doc_slug = doc["slug"]

    # Before processing, total_cost is None
    assert doc["total_cost"] is None

    # Collapsed batch overview does not expose individual per-page costs
    batch_out = client.get(f"/api/batches/{batch_slug}").json()
    assert "total_cost" not in batch_out

    # Simulate processor._persist with 2 pages having estimated costs 0.004 and 0.008 -> total 0.012
    geom = PageGeometry(width=1000, height=800, header=(0, 50), row_bounds=[(50, 100)], col_bounds=[0, 500, 1000])
    table = PageTable(rows=[], uncertainties=[], page_confidence=0.95)
    page1 = PageResult(num=1, geometry=geom, table=table, estimated_cost=0.004)
    page2 = PageResult(num=2, geometry=geom, table=table, estimated_cost=0.008)
    doc_result = DocResult(pages=[page1, page2])

    import asyncio

    async def _run_persist():
        async with session_maker() as session:
            doc_row = (await session.exec(select(Doc).where(Doc.slug == doc_slug))).one()
            doc_id = doc_row.id
        await processor._persist(doc_id, doc_result)

    asyncio.run(_run_persist())

    # Expanded batch docs returns total_cost = 0.012 (sum over pages)
    docs_after = client.get(f"/api/batches/{batch_slug}/docs").json()["items"]
    assert len(docs_after) == 1
    assert docs_after[0]["total_cost"] == 0.012

    # Pages in DB have estimated_cost set
    async def _check_pages():
        async with session_maker() as session:
            pages = (await session.exec(select(Page).order_by(Page.num))).all()
            assert len(pages) == 2
            assert pages[0].estimated_cost == 0.004
            assert pages[1].estimated_cost == 0.008

    asyncio.run(_check_pages())


def test_collect_result_forwards_cost(tmp_path):
    """collect_result reads extraction.estimated_cost and forwards it to PageResult."""
    task_dir = tmp_path / "task"
    cropped_dir = task_dir / "cropped"
    processed_dir = task_dir / "processed"
    cropped_dir.mkdir(parents=True)
    processed_dir.mkdir(parents=True)

    geom = PageGeometry(width=100, height=100, header=(0, 10), row_bounds=[], col_bounds=[0, 100])
    (cropped_dir / "p001.geom.json").write_text(geom.model_dump_json(), encoding="utf-8")

    from backend.pipeline.models.llm_responses import ExtractionResult, PageTable

    extraction = ExtractionResult(
        source="gpt-4o",
        table=PageTable(rows=[], uncertainties=[], page_confidence=0.9),
        input_tokens=100,
        output_tokens=50,
        estimated_cost=0.0025,
    )
    (processed_dir / "p001.json").write_text(extraction.model_dump_json(), encoding="utf-8")

    dummy_pdf = tmp_path / "dummy.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 dummy")
    task = PipelineTask(
        input_pdf=dummy_pdf,
        output_directory=task_dir,
    )
    doc_res = collect_result(task)
    assert len(doc_res.pages) == 1
    assert doc_res.pages[0].estimated_cost == 0.0025


def test_db_migration_columns():
    """_migrate_columns adds missing columns to existing SQLite tables."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "legacy.db"
        engine = create_engine(f"sqlite:///{db_path}")

        # Create older schema without deleted_at, total_cost, estimated_cost
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE batch (id INTEGER PRIMARY KEY, slug TEXT, name TEXT);")
            conn.exec_driver_sql("CREATE TABLE doc (id INTEGER PRIMARY KEY, slug TEXT, filename TEXT);")
            conn.exec_driver_sql("CREATE TABLE page (id INTEGER PRIMARY KEY, doc_id INTEGER, num INTEGER);")

            # Run migration
            _migrate_columns(conn)

            # Inspect columns
            batch_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(batch)").fetchall()}
            doc_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(doc)").fetchall()}
            page_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(page)").fetchall()}

            assert "deleted_at" in batch_cols
            assert "total_cost" in doc_cols
            assert "estimated_cost" in page_cols
