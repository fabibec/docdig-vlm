import io
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

import backend.api.db as api_db
import backend.api.processing as processor_module
import backend.api.storage as api_storage
from backend.api.app import app
from backend.api.models import Doc
from backend.api.processing import processor
from backend.api.schemas import BatchOut, DocOut
from backend.pipeline.models.llm_responses import PageTable
from backend.pipeline.models.results import DocResult, PageGeometry, PageResult


@pytest.fixture
def challenger_env(tmp_path):
    """Isolated environment for challenger empirical verification."""
    db_file = tmp_path / "challenger_test.db"
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
            "db_file": db_file,
        }

    app.dependency_overrides.pop(api_db.get_session, None)
    api_db.engine = old_engine
    api_db.SessionLocal = old_session_maker
    processor_module.SessionLocal = old_proc_session_maker
    api_storage.store.root = old_store_root


def test_empirical_doc_file_serving(challenger_env):
    """Empirical challenge 1: Document file serving.

    - Call GET /api/docs/{slug}/file on a test document.
    - Assert response status is 200.
    - Assert Content-Type header is application/pdf.
    - Assert Content-Disposition header starts with inline.
    - Assert returned byte stream matches the original input PDF.
    - Adversarial edge cases: missing file on disk (404), deleted batch (404), non-existent slug (404).
    """
    client = challenger_env["client"]

    # Binary payload with special bytes, non-ASCII and nulls
    test_pdf_content = (
        b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        b"1 0 obj\n<< /Title (Empirical Challenger Test) >>\nendobj\n"
        b"trailer\n<< /Root 1 0 R >>\n%%EOF\n\x00\x01\xfe\xff"
    )

    filename = "sample_test_doc.pdf"
    files = [("files", (filename, io.BytesIO(test_pdf_content), "application/pdf"))]
    res_batch = client.post("/api/batches", data={"name": "File Serving Test Batch"}, files=files)
    assert res_batch.status_code == 201
    batch_slug = res_batch.json()["slug"]

    # Retrieve doc slug
    docs_res = client.get(f"/api/batches/{batch_slug}/docs")
    assert docs_res.status_code == 200
    docs = docs_res.json()["items"]
    assert len(docs) == 1
    doc_slug = docs[0]["slug"]

    # 1. Empirical test: GET /api/docs/{slug}/file
    file_res = client.get(f"/api/docs/{doc_slug}/file")
    assert file_res.status_code == 200, f"Expected 200 but got {file_res.status_code}"
    assert file_res.headers["content-type"] == "application/pdf", (
        f"Expected 'application/pdf', got '{file_res.headers.get('content-type')}'"
    )
    content_disp = file_res.headers.get("content-disposition", "")
    assert content_disp.startswith("inline"), (
        f"Expected Content-Disposition to start with 'inline', got '{content_disp}'"
    )
    assert f'filename="{filename}"' in content_disp, (
        f"Expected filename '{filename}' in Content-Disposition: '{content_disp}'"
    )
    assert file_res.content == test_pdf_content, "Returned byte stream does NOT match original PDF bytes!"

    # 2. Adversarial test: doc from soft-deleted batch must return 404
    del_res = client.delete(f"/api/batches/{batch_slug}")
    assert del_res.status_code == 204
    file_res_after_delete = client.get(f"/api/docs/{doc_slug}/file")
    assert file_res_after_delete.status_code == 404, (
        f"File serving should return 404 for doc of soft-deleted batch, got {file_res_after_delete.status_code}"
    )

    # 3. Adversarial test: non-existent slug returns 404
    assert client.get("/api/docs/non-existent-doc-slug/file").status_code == 404


def test_empirical_total_cost_and_schemas(challenger_env):
    """Empirical challenge 2: Cost per page persistence and schemas.

    - Simulate document processing with page-level costs.
    - Assert Page.estimated_cost is persisted in SQLite.
    - Assert Doc.total_cost equals round(sum(costs), 6).
    - Assert GET /api/batches/{slug}/docs returns total_cost on DocOut.
    - Assert GET /api/batches (BatchOut) does NOT contain individual per-page costs.
    """
    client = challenger_env["client"]
    session_maker = challenger_env["session_maker"]

    # Verify schema models directly
    assert "total_cost" in DocOut.model_fields
    assert "total_cost" not in BatchOut.model_fields

    # Create batch with 2 documents
    files = [
        ("files", ("multi_page.pdf", io.BytesIO(b"%PDF multi"), "application/pdf")),
        ("files", ("single_page.pdf", io.BytesIO(b"%PDF single"), "application/pdf")),
    ]
    b_res = client.post("/api/batches", data={"name": "Cost Empirical Batch"}, files=files)
    assert b_res.status_code == 201
    batch_slug = b_res.json()["slug"]

    docs = client.get(f"/api/batches/{batch_slug}/docs").json()["items"]
    doc_multi = next(d for d in docs if d["filename"] == "multi_page.pdf")
    doc_single = next(d for d in docs if d["filename"] == "single_page.pdf")

    # Document 1: 3 pages with non-trivial costs
    # Page costs: 0.0035, 0.0071, 0.0014 -> total = 0.012
    geom = PageGeometry(width=800, height=1000, header=(0, 50), row_bounds=[], col_bounds=[0, 800])
    table = PageTable(rows=[], uncertainties=[], page_confidence=0.98)

    costs_multi = [0.0035, 0.0071, 0.0014]
    pages_multi = [
        PageResult(num=i + 1, geometry=geom, table=table, estimated_cost=cost) for i, cost in enumerate(costs_multi)
    ]
    doc_res_multi = DocResult(pages=pages_multi)

    # Document 2: 1 page with repeating float cost
    # Page cost: 0.00777 -> total = 0.00777
    cost_single = 0.00777
    pages_single = [PageResult(num=1, geometry=geom, table=table, estimated_cost=cost_single)]
    doc_res_single = DocResult(pages=pages_single)

    import asyncio

    async def _persist_all():
        async with session_maker() as session:
            d_multi = (await session.exec(select(Doc).where(Doc.slug == doc_multi["slug"]))).one()
            d_single = (await session.exec(select(Doc).where(Doc.slug == doc_single["slug"]))).one()
            id_multi, id_single = d_multi.id, d_single.id
        await processor._persist(id_multi, doc_res_multi)
        await processor._persist(id_single, doc_res_single)

    asyncio.run(_persist_all())

    # Raw SQLite check via native sqlite3:
    import sqlite3

    with sqlite3.connect(challenger_env["db_file"]) as conn:
        cursor = conn.cursor()
        # Check Page.estimated_cost in SQLite table
        raw_pages = cursor.execute(
            "SELECT num, estimated_cost FROM page JOIN doc ON page.doc_id = doc.id WHERE doc.slug = ? ORDER BY num",
            (doc_multi["slug"],),
        ).fetchall()
        assert len(raw_pages) == 3
        for idx, (p_num, est_cost) in enumerate(raw_pages):
            assert p_num == idx + 1
            assert est_cost is not None
            assert abs(est_cost - costs_multi[idx]) < 1e-6, (
                f"Page {p_num} raw cost in SQLite {est_cost} != expected {costs_multi[idx]}"
            )

        # Check Doc.total_cost in SQLite table
        raw_doc_multi = cursor.execute(
            "SELECT total_cost FROM doc WHERE slug = ?",
            (doc_multi["slug"],),
        ).fetchone()
        expected_multi_cpp = round(sum(costs_multi), 6)
        assert raw_doc_multi[0] == expected_multi_cpp, (
            f"Doc {doc_multi['slug']} total_cost in SQLite {raw_doc_multi[0]} != {expected_multi_cpp}"
        )

        raw_doc_single = cursor.execute(
            "SELECT total_cost FROM doc WHERE slug = ?",
            (doc_single["slug"],),
        ).fetchone()
        expected_single_cpp = round(cost_single, 6)
        assert raw_doc_single[0] == expected_single_cpp, (
            f"Doc {doc_single['slug']} total_cost in SQLite {raw_doc_single[0]} != {expected_single_cpp}"
        )

    # API Check 1: GET /api/batches/{slug}/docs returns total_cost on DocOut
    docs_api_res = client.get(f"/api/batches/{batch_slug}/docs")
    assert docs_api_res.status_code == 200
    items = docs_api_res.json()["items"]
    assert len(items) == 2

    multi_item = next(item for item in items if item["slug"] == doc_multi["slug"])
    assert "total_cost" in multi_item
    assert multi_item["total_cost"] == expected_multi_cpp

    single_item = next(item for item in items if item["slug"] == doc_single["slug"])
    assert "total_cost" in single_item
    assert single_item["total_cost"] == expected_single_cpp

    # API Check 2: GET /api/batches (BatchOut) does NOT contain individual per-page costs
    batches_res = client.get("/api/batches")
    assert batches_res.status_code == 200
    batches_data = batches_res.json()
    assert "total_cost" not in batches_data
    for batch in batches_data["items"]:
        assert "total_cost" not in batch, f"Violation: 'total_cost' found in BatchOut: {batch}"

    # Also check GET /api/batches/{slug}
    batch_detail_res = client.get(f"/api/batches/{batch_slug}")
    assert batch_detail_res.status_code == 200
    assert "total_cost" not in batch_detail_res.json()


def test_adversarial_file_serving_edge_cases(challenger_env):
    """Stress test file serving with renames, special characters, and missing disk files."""
    client = challenger_env["client"]
    batches_dir = challenger_env["batches_dir"]

    # File with spaces and parenthesis
    filename = "Scan (Bird Records) 1972 #1.pdf"
    content = b"%PDF-1.4 specialized stream content"
    files = [("files", (filename, io.BytesIO(content), "application/pdf"))]
    b_res = client.post("/api/batches", data={"name": "Special Chars Batch"}, files=files)
    assert b_res.status_code == 201
    batch_slug = b_res.json()["slug"]

    docs = client.get(f"/api/batches/{batch_slug}/docs").json()["items"]
    doc_slug = docs[0]["slug"]

    # Check serving
    res = client.get(f"/api/docs/{doc_slug}/file")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"] == f'inline; filename="{filename}"'
    assert res.content == content

    # Test file serving after rename
    new_filename = "Renamed_Records_1972.pdf"
    patch_res = client.patch(f"/api/docs/{doc_slug}", json={"filename": new_filename})
    assert patch_res.status_code == 200
    assert patch_res.json()["filename"] == new_filename

    res_renamed = client.get(f"/api/docs/{doc_slug}/file")
    assert res_renamed.status_code == 200
    assert res_renamed.headers["content-disposition"] == f'inline; filename="{new_filename}"'
    assert res_renamed.content == content

    # Test missing disk file -> 404 (not 500)
    disk_file = batches_dir / batch_slug / doc_slug / "input" / new_filename
    assert disk_file.is_file()
    disk_file.unlink()  # simulate file disappearance

    res_missing = client.get(f"/api/docs/{doc_slug}/file")
    assert res_missing.status_code == 404
    assert res_missing.json()["detail"] == "file not found"


def test_adversarial_cost_calculations(challenger_env):
    """Adversarial testing of cost per page edge cases: zero, mixed None, repeating decimals."""
    client = challenger_env["client"]
    session_maker = challenger_env["session_maker"]

    geom = PageGeometry(width=800, height=1000, header=(0, 50), row_bounds=[], col_bounds=[0, 800])
    table = PageTable(rows=[], uncertainties=[], page_confidence=0.9)

    # 1. Zero cost document
    files = [("files", ("zero_cost.pdf", io.BytesIO(b"%PDF zero"), "application/pdf"))]
    b1 = client.post("/api/batches", data={"name": "Zero Batch"}, files=files).json()
    d1_slug = client.get(f"/api/batches/{b1['slug']}/docs").json()["items"][0]["slug"]

    # 2. Mixed cost: 4 pages, 2 with costs [0.0020, 0.0040], 2 with None -> sum 0.0060 / 4 = 0.0015
    files = [("files", ("mixed_cost.pdf", io.BytesIO(b"%PDF mixed"), "application/pdf"))]
    b2 = client.post("/api/batches", data={"name": "Mixed Batch"}, files=files).json()
    d2_slug = client.get(f"/api/batches/{b2['slug']}/docs").json()["items"][0]["slug"]

    # 3. Repeating decimal: 3 pages [0.001, 0.002, 0.001] -> sum 0.004 / 3 = 0.0013333... -> round 0.0013
    files = [("files", ("repeating_cost.pdf", io.BytesIO(b"%PDF rep"), "application/pdf"))]
    b3 = client.post("/api/batches", data={"name": "Repeating Batch"}, files=files).json()
    d3_slug = client.get(f"/api/batches/{b3['slug']}/docs").json()["items"][0]["slug"]

    # 4. All None costs: 2 pages with estimated_cost=None -> doc.total_cost should remain None
    files = [("files", ("none_cost.pdf", io.BytesIO(b"%PDF none"), "application/pdf"))]
    b4 = client.post("/api/batches", data={"name": "None Batch"}, files=files).json()
    d4_slug = client.get(f"/api/batches/{b4['slug']}/docs").json()["items"][0]["slug"]

    import asyncio

    async def _persist_all():
        async with session_maker() as session:
            d1_id = (await session.exec(select(Doc.id).where(Doc.slug == d1_slug))).one()
            d2_id = (await session.exec(select(Doc.id).where(Doc.slug == d2_slug))).one()
            d3_id = (await session.exec(select(Doc.id).where(Doc.slug == d3_slug))).one()
            d4_id = (await session.exec(select(Doc.id).where(Doc.slug == d4_slug))).one()

        # D1: 2 pages with 0.0 cost
        res1 = DocResult(
            pages=[
                PageResult(num=1, geometry=geom, table=table, estimated_cost=0.0),
                PageResult(num=2, geometry=geom, table=table, estimated_cost=0.0),
            ]
        )
        await processor._persist(d1_id, res1)

        # D2: 4 pages mixed
        res2 = DocResult(
            pages=[
                PageResult(num=1, geometry=geom, table=table, estimated_cost=0.0020),
                PageResult(num=2, geometry=geom, table=table, estimated_cost=None),
                PageResult(num=3, geometry=geom, table=table, estimated_cost=0.0040),
                PageResult(num=4, geometry=geom, table=table, estimated_cost=None),
            ]
        )
        await processor._persist(d2_id, res2)

        # D3: 3 pages repeating
        res3 = DocResult(
            pages=[
                PageResult(num=1, geometry=geom, table=table, estimated_cost=0.001),
                PageResult(num=2, geometry=geom, table=table, estimated_cost=0.002),
                PageResult(num=3, geometry=geom, table=table, estimated_cost=0.001),
            ]
        )
        await processor._persist(d3_id, res3)

        # D4: all None
        res4 = DocResult(
            pages=[
                PageResult(num=1, geometry=geom, table=table, estimated_cost=None),
                PageResult(num=2, geometry=geom, table=table, estimated_cost=None),
            ]
        )
        await processor._persist(d4_id, res4)

    asyncio.run(_persist_all())

    # Check D1
    d1_out = client.get(f"/api/batches/{b1['slug']}/docs").json()["items"][0]
    assert d1_out["total_cost"] == 0.0

    # Check D2
    d2_out = client.get(f"/api/batches/{b2['slug']}/docs").json()["items"][0]
    assert d2_out["total_cost"] == 0.006

    # Check D3
    d3_out = client.get(f"/api/batches/{b3['slug']}/docs").json()["items"][0]
    assert d3_out["total_cost"] == 0.004

    # Check D4
    d4_out = client.get(f"/api/batches/{b4['slug']}/docs").json()["items"][0]
    assert d4_out["total_cost"] is None


def test_benchmark_invariance():
    """Verify that benchmark/run_benchmark.py is untouched and independent of orientation detector and backend APIs."""
    bench_file = Path("benchmark/run_benchmark.py")
    assert bench_file.is_file(), "benchmark/run_benchmark.py must exist"

    content = bench_file.read_text(encoding="utf-8")
    assert "TableOrientationDetector" not in content, (
        "benchmark/run_benchmark.py must NOT reference TableOrientationDetector"
    )
    assert "table_orientation_detector" not in content, (
        "benchmark/run_benchmark.py must NOT import table_orientation_detector"
    )
    assert "backend.api" not in content, "benchmark/run_benchmark.py must NOT import backend.api"
