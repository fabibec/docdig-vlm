"""Empirical challenge tests for Milestone M2.

Verifies:
1. Batch Soft Deletion lifecycle and critical disk file retention.
2. Review queue exclusion of soft-deleted batches and documents.
3. Batch renaming (name updated, slug immutable, whitespace stripped, validation).
4. Document renaming:
   - Physical file on disk renamed (old path gone, new path present, identical bytes).
   - Duplicate rename within same batch rejected with 409 Conflict (files untouched).
   - Cross-batch duplicate allowed (distinct batches).
   - Path traversal attempts sanitized and strictly confined to doc input folder.
   - Auto-extension appending.
5. Invariance of benchmark/run_benchmark.py.
"""

import io
import subprocess
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
from backend.api.enums import DocStatus
from backend.api.models import Doc


@pytest.fixture
def challenge_env(tmp_path):
    """Isolated database and storage environment for empirical challenge tests."""
    db_file = tmp_path / "challenge_test.db"
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


def _upload_batch(client, name: str, files_dict: dict[str, bytes]):
    """Upload a batch with specific filenames and byte contents."""
    files = [("files", (fname, io.BytesIO(content), "application/pdf")) for fname, content in files_dict.items()]
    res = client.post("/api/batches", data={"name": name}, files=files)
    assert res.status_code == 201, f"Batch creation failed: {res.text}"
    return res.json()


# ==============================================================================
# CHALLENGE 1: Soft Delete & Disk Retention
# ==============================================================================


def test_empirical_soft_delete_filesystem_retention(challenge_env):
    """DELETE /api/batches/{slug} soft-deletes the batch in API but retains ALL files on disk."""
    client = challenge_env["client"]
    batches_dir = challenge_env["batches_dir"]

    # 1. Create batch with 3 distinct PDF payloads
    doc_payloads = {
        "report_alpha.pdf": b"%PDF-1.4\n%Empirical Test Alpha Content\x00\x01\x02\xfe\xff",
        "report_beta.pdf": b"%PDF-1.4\n%Empirical Test Beta Content\x10\x20\x30\x40",
        "report_gamma.pdf": b"%PDF-1.4\n%Empirical Test Gamma Content\xaa\xbb\xcc\xdd",
    }
    batch = _upload_batch(client, "Empirical Delete Batch", doc_payloads)
    batch_slug = batch["slug"]

    # Retrieve docs
    docs_resp = client.get(f"/api/batches/{batch_slug}/docs")
    assert docs_resp.status_code == 200
    docs = docs_resp.json()["items"]
    assert len(docs) == 3

    # Verify all files exist on disk BEFORE deletion with correct byte content
    for doc in docs:
        disk_file = batches_dir / batch_slug / doc["slug"] / "input" / doc["filename"]
        assert disk_file.is_file(), f"Expected {disk_file} to exist before deletion"
        assert disk_file.read_bytes() == doc_payloads[doc["filename"]], "Disk content mismatch before delete"

    # 2. Perform soft deletion
    del_resp = client.delete(f"/api/batches/{batch_slug}")
    assert del_resp.status_code == 204
    assert del_resp.content == b""

    # 3. API Verifications: batch is hidden
    # GET /api/batches omits it
    batches_list = client.get("/api/batches").json()
    assert not any(b["slug"] == batch_slug for b in batches_list["items"])

    # GET /api/batches/{slug} -> 404
    assert client.get(f"/api/batches/{batch_slug}").status_code == 404

    # GET /api/batches/{slug}/docs -> 404
    assert client.get(f"/api/batches/{batch_slug}/docs").status_code == 404

    # Docs endpoints return 404
    for doc in docs:
        assert client.get(f"/api/docs/{doc['slug']}").status_code == 404
        assert client.get(f"/api/docs/{doc['slug']}/file").status_code == 404
        assert client.get(f"/api/docs/{doc['slug']}/flags").status_code == 404
        assert client.patch(f"/api/docs/{doc['slug']}", json={"filename": "renamed.pdf"}).status_code == 404

    # PATCH /api/batches/{slug} -> 404
    assert client.patch(f"/api/batches/{batch_slug}", json={"name": "New Name"}).status_code == 404

    # Second DELETE -> 404
    assert client.delete(f"/api/batches/{batch_slug}").status_code == 404

    # 4. CRITICAL EMPIRICAL ASSERTION: Filesystem Inspection
    batch_folder = batches_dir / batch_slug
    assert batch_folder.is_dir(), f"CRITICAL VIOLATION: Batch folder {batch_folder} was deleted from disk!"

    for doc in docs:
        doc_folder = batches_dir / batch_slug / doc["slug"]
        assert doc_folder.is_dir(), f"CRITICAL VIOLATION: Doc folder {doc_folder} was deleted!"

        input_folder = doc_folder / "input"
        assert input_folder.is_dir(), f"CRITICAL VIOLATION: Input folder {input_folder} was deleted!"

        disk_file = input_folder / doc["filename"]
        assert disk_file.is_file(), f"CRITICAL VIOLATION: PDF file {disk_file} was deleted from disk!"

        # Byte-for-byte exact equality check
        stored_bytes = disk_file.read_bytes()
        expected_bytes = doc_payloads[doc["filename"]]
        assert stored_bytes == expected_bytes, f"CRITICAL VIOLATION: File content of {disk_file} was altered!"


def test_empirical_soft_delete_review_queue_exclusion(challenge_env):
    """Review queue excludes docs and counts from soft-deleted batches."""
    client = challenge_env["client"]
    session_maker = challenge_env["session_maker"]

    # Create active batch A and to-be-deleted batch B
    batch_a = _upload_batch(client, "Keep Batch", {"doc_keep.pdf": b"%PDF-1.4 keep"})
    batch_b = _upload_batch(client, "Delete Batch", {"doc_del.pdf": b"%PDF-1.4 delete"})

    doc_a_slug = client.get(f"/api/batches/{batch_a['slug']}/docs").json()["items"][0]["slug"]
    doc_b_slug = client.get(f"/api/batches/{batch_b['slug']}/docs").json()["items"][0]["slug"]

    # Mark both docs as in review with open flags
    import asyncio

    async def _setup_review_status():
        async with session_maker() as session:
            da = (await session.exec(select(Doc).where(Doc.slug == doc_a_slug))).one()
            db = (await session.exec(select(Doc).where(Doc.slug == doc_b_slug))).one()
            da.status = DocStatus.review
            da.open_flags = 3
            db.status = DocStatus.review
            db.open_flags = 5
            await session.commit()

    asyncio.run(_setup_review_status())

    # Verify both in review queue
    rq = client.get("/api/review").json()
    assert rq["files_left"] >= 2
    slugs_in_rq = [d["slug"] for d in rq["docs"]]
    assert doc_a_slug in slugs_in_rq
    assert doc_b_slug in slugs_in_rq

    # Delete batch B
    del_res = client.delete(f"/api/batches/{batch_b['slug']}")
    assert del_res.status_code == 204

    # Review queue should only have doc A
    rq_after = client.get("/api/review").json()
    after_slugs = [d["slug"] for d in rq_after["docs"]]
    assert doc_a_slug in after_slugs
    assert doc_b_slug not in after_slugs
    assert rq_after["files_left"] == 1
    assert rq_after["flags_left"] == 3

    # Scoped queries for deleted batch/doc return 404
    assert client.get(f"/api/review?batch={batch_b['slug']}").status_code == 404
    assert client.get(f"/api/review?doc={doc_b_slug}").status_code == 404

    # Scoped queries for active batch/doc succeed
    assert client.get(f"/api/review?batch={batch_a['slug']}").status_code == 200
    assert client.get(f"/api/review?doc={doc_a_slug}").status_code == 200


# ==============================================================================
# CHALLENGE 2: Renaming & Edge Cases
# ==============================================================================


def test_empirical_batch_renaming(challenge_env):
    """Batch renaming updates name, retains slug, trims whitespace, validates length."""
    client = challenge_env["client"]

    batch = _upload_batch(client, "Original Name", {"test.pdf": b"%PDF-1.4 test"})
    slug = batch["slug"]
    original_created = batch["created_at"]

    # 1. Update name
    res = client.patch(f"/api/batches/{slug}", json={"name": "Updated Batch Name"})
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "Updated Batch Name"
    assert data["slug"] == slug
    assert data["created_at"] == original_created

    # GET verifies persisted change
    assert client.get(f"/api/batches/{slug}").json()["name"] == "Updated Batch Name"

    # 2. Whitespace trimming
    res2 = client.patch(f"/api/batches/{slug}", json={"name": "   Trimmed Name   "})
    assert res2.status_code == 200
    assert res2.json()["name"] == "Trimmed Name"

    # 3. Unicode and special characters
    res3 = client.patch(f"/api/batches/{slug}", json={"name": "Bâtch 2026: äöü/ß & % #1"})
    assert res3.status_code == 200
    assert res3.json()["name"] == "Bâtch 2026: äöü/ß & % #1"

    # 4. Validation: empty name -> 422
    assert client.patch(f"/api/batches/{slug}", json={"name": ""}).status_code == 422

    # 5. Validation: max length 120
    assert client.patch(f"/api/batches/{slug}", json={"name": "a" * 120}).status_code == 200
    assert client.patch(f"/api/batches/{slug}", json={"name": "a" * 121}).status_code == 422


def test_empirical_doc_renaming_physical_file_and_content(challenge_env):
    """Doc renaming renames the physical file on disk with exact byte preservation."""
    client = challenge_env["client"]
    batches_dir = challenge_env["batches_dir"]

    pdf_bytes = b"%PDF-1.4 \x00\x01\x02\x03\xfe\xff UNIQUE_BINARY_MARKER_998877"
    batch = _upload_batch(client, "Doc Rename Batch", {"orig_doc.pdf": pdf_bytes})
    batch_slug = batch["slug"]

    doc = client.get(f"/api/batches/{batch_slug}/docs").json()["items"][0]
    doc_slug = doc["slug"]

    old_file_path = batches_dir / batch_slug / doc_slug / "input" / "orig_doc.pdf"
    assert old_file_path.is_file()
    assert old_file_path.read_bytes() == pdf_bytes

    # 1. Rename to new name with .pdf
    rename_resp = client.patch(f"/api/docs/{doc_slug}", json={"filename": "new_doc.pdf"})
    assert rename_resp.status_code == 200
    assert rename_resp.json()["filename"] == "new_doc.pdf"

    new_file_path = batches_dir / batch_slug / doc_slug / "input" / "new_doc.pdf"
    assert not old_file_path.exists(), f"Old file {old_file_path} must no longer exist!"
    assert new_file_path.is_file(), f"New file {new_file_path} must exist on disk!"
    assert new_file_path.read_bytes() == pdf_bytes, "File byte contents must be identical!"

    # 2. File serving endpoint reflects renamed file
    serve_resp = client.get(f"/api/docs/{doc_slug}/file")
    assert serve_resp.status_code == 200
    assert serve_resp.headers["content-disposition"] == 'inline; filename="new_doc.pdf"'
    assert serve_resp.content == pdf_bytes

    # 3. Rename without .pdf extension -> should auto-append .pdf
    rename_no_ext = client.patch(f"/api/docs/{doc_slug}", json={"filename": "another_name"})
    assert rename_no_ext.status_code == 200
    assert rename_no_ext.json()["filename"] == "another_name.pdf"

    third_file_path = batches_dir / batch_slug / doc_slug / "input" / "another_name.pdf"
    assert not new_file_path.exists(), f"Previous file {new_file_path} must no longer exist!"
    assert third_file_path.is_file()
    assert third_file_path.read_bytes() == pdf_bytes

    # 4. Renaming to current name is idempotent
    same_name_resp = client.patch(f"/api/docs/{doc_slug}", json={"filename": "another_name.pdf"})
    assert same_name_resp.status_code == 200
    assert same_name_resp.json()["filename"] == "another_name.pdf"
    assert third_file_path.is_file()


def test_empirical_doc_renaming_duplicate_conflict_409(challenge_env):
    """Duplicate doc filename within same batch returns 409 and leaves files untouched."""
    client = challenge_env["client"]
    batches_dir = challenge_env["batches_dir"]

    bytes_1 = b"%PDF-1.4 file 1 content"
    bytes_2 = b"%PDF-1.4 file 2 content"
    batch = _upload_batch(client, "Dupe Test Batch", {"file1.pdf": bytes_1, "file2.pdf": bytes_2})
    batch_slug = batch["slug"]

    docs = client.get(f"/api/batches/{batch_slug}/docs").json()["items"]
    d1 = next(d for d in docs if d["filename"] == "file1.pdf")
    d2 = next(d for d in docs if d["filename"] == "file2.pdf")

    path1 = batches_dir / batch_slug / d1["slug"] / "input" / "file1.pdf"
    path2 = batches_dir / batch_slug / d2["slug"] / "input" / "file2.pdf"

    # Attempt to rename d2 to "file1.pdf" (already exists in this batch)
    res = client.patch(f"/api/docs/{d2['slug']}", json={"filename": "file1.pdf"})
    assert res.status_code == 409
    assert "already exists in this batch" in res.json()["detail"]

    # Also test with auto-extension: "file1" -> "file1.pdf"
    res_no_ext = client.patch(f"/api/docs/{d2['slug']}", json={"filename": "file1"})
    assert res_no_ext.status_code == 409

    # Verify both physical files are untouched and uncorrupted
    assert path1.is_file() and path1.read_bytes() == bytes_1
    assert path2.is_file() and path2.read_bytes() == bytes_2

    # Verify DB filenames are unchanged
    assert client.get(f"/api/docs/{d1['slug']}").json()["filename"] == "file1.pdf"
    assert client.get(f"/api/docs/{d2['slug']}").json()["filename"] == "file2.pdf"


def test_empirical_doc_renaming_cross_batch_non_conflict(challenge_env):
    """Documents in DIFFERENT batches can share the same filename without conflict."""
    client = challenge_env["client"]
    batches_dir = challenge_env["batches_dir"]

    b1 = _upload_batch(client, "Batch 1", {"shared.pdf": b"%PDF-1.4 batch 1"})
    b2 = _upload_batch(client, "Batch 2", {"other.pdf": b"%PDF-1.4 batch 2"})

    doc2 = client.get(f"/api/batches/{b2['slug']}/docs").json()["items"][0]

    # Rename doc2 to "shared.pdf" (which exists in batch 1, but NOT in batch 2)
    res = client.patch(f"/api/docs/{doc2['slug']}", json={"filename": "shared.pdf"})
    assert res.status_code == 200
    assert res.json()["filename"] == "shared.pdf"

    # Both files exist under their respective batches
    d1_slug = client.get(f"/api/batches/{b1['slug']}/docs").json()["items"][0]["slug"]
    file1 = batches_dir / b1["slug"] / d1_slug / "input" / "shared.pdf"
    file2 = batches_dir / b2["slug"] / doc2["slug"] / "input" / "shared.pdf"
    assert file1.is_file() and file1.read_bytes() == b"%PDF-1.4 batch 1"
    assert file2.is_file() and file2.read_bytes() == b"%PDF-1.4 batch 2"


def test_empirical_doc_renaming_path_traversal_sanitization(challenge_env):
    """Path traversal attempts in doc rename are sanitized and confined to doc input folder."""
    client = challenge_env["client"]
    batches_dir = challenge_env["batches_dir"]

    batch = _upload_batch(client, "Traversal Test", {"safe.pdf": b"%PDF-1.4 safe content"})
    batch_slug = batch["slug"]
    doc = client.get(f"/api/batches/{batch_slug}/docs").json()["items"][0]
    doc_slug = doc["slug"]
    doc_input_dir = batches_dir / batch_slug / doc_slug / "input"

    traversal_attacks = [
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("../test.pdf", "test.pdf"),
        ("../../../../../evil.pdf", "evil.pdf"),
        ("/root/secret.pdf", "secret.pdf"),
        ("nested/subfolder/document.pdf", "document.pdf"),
        ("./relative/path/report.pdf", "report.pdf"),
    ]

    for attack_input, expected_clean_name in traversal_attacks:
        res = client.patch(f"/api/docs/{doc_slug}", json={"filename": attack_input})
        assert res.status_code == 200, f"Failed on attack input: {attack_input}"
        assert res.json()["filename"] == expected_clean_name

        # Verify file is inside doc_input_dir
        expected_file = doc_input_dir / expected_clean_name
        assert expected_file.is_file(), f"File {expected_file} does not exist!"
        assert expected_file.read_bytes() == b"%PDF-1.4 safe content"

        # Verify nothing was written to root or parent directories
        assert not (batches_dir / expected_clean_name).exists()
        assert not (batches_dir / batch_slug / expected_clean_name).exists()

    # Inputs that resolve to empty or just extension are rejected with 422
    for invalid in [".", ".pdf", "   .pdf", "   ", ""]:
        res = client.patch(f"/api/docs/{doc_slug}", json={"filename": invalid})
        assert res.status_code == 422, f"Expected 422 for {invalid!r}, got {res.status_code}"

    # Dot-traversal attempts like ".." or "../.." are sanitized to "...pdf" and confined
    for dot_attack in ["..", "../.."]:
        res = client.patch(f"/api/docs/{doc_slug}", json={"filename": dot_attack})
        assert res.status_code == 200
        assert res.json()["filename"] == "...pdf"
        assert (doc_input_dir / "...pdf").is_file()
        assert not (batches_dir / "...pdf").exists()


# ==============================================================================
# CHALLENGE 3: Benchmark Invariance
# ==============================================================================


def test_empirical_benchmark_untouched():
    """Verify benchmark/run_benchmark.py is present and has not been modified."""
    benchmark_file = Path("benchmark/run_benchmark.py")
    assert benchmark_file.is_file(), "benchmark/run_benchmark.py is missing!"

    # Run git diff against origin/main or HEAD to ensure zero modifications
    res = subprocess.run(
        ["git", "diff", "origin/main...HEAD", "--", "benchmark/run_benchmark.py"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == "", f"benchmark/run_benchmark.py has committed diff: {res.stdout}"

    # Check git status for unstaged changes on benchmark/run_benchmark.py
    status_res = subprocess.run(
        ["git", "status", "--porcelain", "benchmark/run_benchmark.py"],
        capture_output=True,
        text=True,
        check=True,
    )
    # The file is untracked in this repo (shows ?? if untracked, but if modified it would show M)
    # If tracked, status output should be empty; if untracked, it shows '?? benchmark/run_benchmark.py'.
    # It must NOT show ' M ' (modified).
    assert " M " not in status_res.stdout, f"benchmark/run_benchmark.py is modified: {status_res.stdout}"
