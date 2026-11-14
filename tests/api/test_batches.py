"""/api/batches: upload, listing, rename, soft delete, doc listing."""

from datetime import UTC, date, datetime, timedelta

import pytest

import backend.api.routes.batches as batches_routes
from backend.api.config import settings
from backend.api.enums import DocStatus
from backend.api.models import Batch, Doc
from tests.api.helpers import pdf_bytes


class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2024, 6, 1)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    """Batch slugs start with today's date; pin it so tests can't flake around midnight."""
    monkeypatch.setattr(batches_routes, "date", _FixedDate)


def _today() -> str:
    return "20240601"


# ---------- upload ----------


def test_upload_creates_batch_and_docs_and_stores_pdfs(api):
    content = pdf_bytes("one")
    res = api.upload("Sempach 1972", [("b.pdf", content), "a.pdf"])

    assert res.status_code == 201
    batch = res.json()
    assert batch["slug"] == f"{_today()}-sempach-1972"
    assert batch["name"] == "Sempach 1972"
    assert batch["num_docs"] == 2
    assert batch["counts"] == {"queued": 2, "processing": 0, "review": 0, "done": 0, "error": 0}
    assert batch["open_flags"] == 0

    docs = api.client.get(f"/api/batches/{batch['slug']}/docs").json()["items"]
    assert [d["filename"] for d in docs] == ["a.pdf", "b.pdf"]
    assert [d["slug"] for d in docs] == [f"{batch['slug']}-a", f"{batch['slug']}-b"]
    assert all(d["status"] == "queued" and d["total_cost"] is None for d in docs)
    stored = api.batches_dir / batch["slug"] / f"{batch['slug']}-b" / "input" / "b.pdf"
    assert stored.read_bytes() == content


def test_upload_enqueues_every_new_doc(api):
    api.upload("Queue", ["x.pdf", "y.pdf"])
    assert len(api.enqueued) == 2


def test_upload_broadcasts_batch_created_and_review_pending(api):
    api.upload("Events", ["x.pdf"])
    assert api.event_types() == ["batch.created", "review.pending"]


def test_upload_trims_name_and_makes_slugs_unique(api):
    first = api.upload("  Same Name ", ["a.pdf"]).json()
    second = api.upload("Same Name", ["a.pdf"]).json()
    assert first["name"] == "Same Name"
    assert first["slug"] == f"{_today()}-same-name"
    assert second["slug"] == f"{_today()}-same-name-2"


def test_upload_name_without_slug_chars_falls_back_to_batch(api):
    assert api.upload("???", ["a.pdf"]).json()["slug"] == f"{_today()}-batch"


def test_upload_strips_directories_from_filenames(api):
    slug = api.upload("Paths", ["../../evil.pdf"]).json()["slug"]
    (doc,) = api.client.get(f"/api/batches/{slug}/docs").json()["items"]
    assert doc["filename"] == "evil.pdf"
    assert not (api.batches_dir.parent / "evil.pdf").exists()


def test_upload_accepts_uppercase_pdf_extension(api):
    assert api.upload("Upper", ["SCAN.PDF"]).status_code == 201


def test_upload_rejects_non_pdf(api):
    res = api.upload("Bad", ["a.pdf", ("notes.txt", b"hello")])
    assert res.status_code == 422
    assert "notes.txt" in res.json()["detail"]
    assert api.client.get("/api/batches").json()["total"] == 0


def test_upload_rejects_duplicate_filenames(api):
    res = api.upload("Dupes", ["a.pdf", "b.pdf", "a.pdf", "dir/b.pdf"])
    assert res.status_code == 409
    assert res.json() == {"detail": "duplicate_files", "filenames": ["a.pdf", "b.pdf"]}
    assert api.client.get("/api/batches").json()["total"] == 0


def test_upload_requires_name_and_files(api):
    assert api.client.post("/api/batches", data={"name": "x"}).status_code == 422
    assert api.upload("", ["a.pdf"]).status_code == 422


def test_upload_too_large_returns_413_and_leaves_nothing_behind(api, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    big = b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 1)
    res = api.upload("Huge", ["small.pdf", ("big.pdf", big)])

    assert res.status_code == 413
    assert "big.pdf" in res.json()["detail"]
    assert api.client.get("/api/batches").json()["total"] == 0
    assert list(api.batches_dir.iterdir()) == []
    assert api.enqueued == []


# ---------- list / get ----------


def test_list_batches_is_paginated_newest_first(api, factory):
    base = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(5):
        factory.batch(f"B{i}", slug=f"b{i}", created_at=base + timedelta(days=i))

    first = api.client.get("/api/batches", params={"page": 1, "size": 2}).json()
    last = api.client.get("/api/batches", params={"page": 3, "size": 2}).json()

    assert first["total"] == 5 and first["page"] == 1 and first["size"] == 2
    assert [b["slug"] for b in first["items"]] == ["b4", "b3"]
    assert [b["slug"] for b in last["items"]] == ["b0"]


def test_list_batches_default_page_size_is_10(api, factory):
    for i in range(12):
        factory.batch(slug=f"b{i:02d}")
    body = api.client.get("/api/batches").json()
    assert (body["page"], body["size"], body["total"], len(body["items"])) == (1, 10, 12, 10)


def test_list_batches_validates_pagination(api):
    assert api.client.get("/api/batches", params={"page": 0}).status_code == 422
    assert api.client.get("/api/batches", params={"size": 101}).status_code == 422


def test_batch_counts_and_open_flags_aggregate_docs(api, factory):
    batch = factory.batch(slug="agg")
    factory.doc(batch, "a.pdf", status=DocStatus.review, open_flags=3)
    factory.doc(batch, "b.pdf", status=DocStatus.review, open_flags=2)
    factory.doc(batch, "c.pdf", status=DocStatus.done)
    factory.doc(batch, "d.pdf", status=DocStatus.error)

    out = api.client.get("/api/batches/agg").json()
    assert out["num_docs"] == 4
    assert out["counts"] == {"queued": 0, "processing": 0, "review": 2, "done": 1, "error": 1}
    assert out["open_flags"] == 5
    assert "total_cost" not in out


def test_get_unknown_batch_is_404(api):
    assert api.client.get("/api/batches/nope").status_code == 404
    assert api.client.get("/api/batches/nope/docs").status_code == 404


# ---------- rename ----------


def test_rename_batch_trims_name_and_keeps_slug(api, factory):
    factory.batch("Old", slug="keep")
    res = api.client.patch("/api/batches/keep", json={"name": "  New name  "})

    assert res.status_code == 200
    assert res.json()["name"] == "New name"
    assert res.json()["slug"] == "keep"
    assert api.client.get("/api/batches/keep").json()["name"] == "New name"
    assert api.event_types() == ["batch.updated"]


def test_rename_batch_rejects_empty_or_too_long_name(api, factory):
    factory.batch(slug="b")
    assert api.client.patch("/api/batches/b", json={"name": ""}).status_code == 422
    assert api.client.patch("/api/batches/b", json={"name": "x" * 121}).status_code == 422


def test_rename_unknown_batch_is_404(api):
    assert api.client.patch("/api/batches/nope", json={"name": "x"}).status_code == 404


# ---------- soft delete ----------


def test_delete_batch_is_soft(api, factory):
    batch = factory.batch(slug="gone")
    doc = factory.doc(batch, "a.pdf", status=DocStatus.review, open_flags=1)
    pdf = api.batches_dir / "gone" / doc.slug / "input" / "a.pdf"
    assert pdf.is_file()

    res = api.client.delete("/api/batches/gone")

    assert res.status_code == 204
    assert res.content == b""
    assert factory.get(Batch, batch.id).deleted_at is not None
    assert factory.get(Doc, doc.id) is not None  # DB rows stay
    assert pdf.is_file()  # files stay
    assert api.event_types() == ["batch.deleted", "review.pending"]
    assert api.events[0].batch.slug == "gone"
    assert api.events[1].files_left == 0


def test_deleted_batch_is_hidden_everywhere(api, factory):
    batch = factory.batch(slug="gone")
    doc = factory.doc(batch, "a.pdf", status=DocStatus.review, open_flags=1)
    factory.batch(slug="kept")
    api.client.delete("/api/batches/gone")

    listing = api.client.get("/api/batches").json()
    assert [b["slug"] for b in listing["items"]] == ["kept"]
    assert listing["total"] == 1
    for url in (
        "/api/batches/gone",
        "/api/batches/gone/docs",
        f"/api/docs/{doc.slug}",
        f"/api/docs/{doc.slug}/file",
        f"/api/docs/{doc.slug}/flags",
        "/api/review?batch=gone",
        f"/api/review?doc={doc.slug}",
    ):
        assert api.client.get(url).status_code == 404, url
    assert api.client.patch(f"/api/docs/{doc.slug}", json={"filename": "b.pdf"}).status_code == 404
    assert api.client.patch("/api/batches/gone", json={"name": "x"}).status_code == 404
    assert api.client.delete("/api/batches/gone").status_code == 404
    assert api.client.get("/api/review").json()["docs"] == []


# ---------- docs of a batch ----------


def test_list_docs_paginates_by_filename_and_shows_total_cost(api, factory):
    batch = factory.batch(slug="docs")
    factory.doc(batch, "c.pdf", total_cost=0.25, status=DocStatus.done, num_pages=2)
    factory.doc(batch, "a.pdf")
    factory.doc(batch, "b.pdf")
    other = factory.batch(slug="other")
    factory.doc(other, "a.pdf")

    first = api.client.get("/api/batches/docs/docs", params={"size": 2}).json()
    second = api.client.get("/api/batches/docs/docs", params={"size": 2, "page": 2}).json()

    assert first["total"] == 3
    assert [d["filename"] for d in first["items"]] == ["a.pdf", "b.pdf"]
    (c,) = second["items"]
    assert c["filename"] == "c.pdf"
    assert c["batch_slug"] == "docs"
    assert c["total_cost"] == 0.25
    assert c["num_pages"] == 2
    assert c["status"] == "done"
