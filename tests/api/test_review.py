"""/api/review: the queue of docs that still need review."""

from datetime import UTC, datetime, timedelta

from backend.api.enums import DocStatus


def _setup(factory):
    t0 = datetime(2024, 5, 1, tzinfo=UTC)
    newer = factory.batch("Newer", slug="newer", created_at=t0 + timedelta(days=1))
    older = factory.batch("Older", slug="older", created_at=t0)
    gone = factory.batch("Gone", slug="gone", created_at=t0 - timedelta(days=1))
    docs = {
        "newer/a": factory.doc(newer, "a.pdf", status=DocStatus.review, open_flags=1),
        "older/z": factory.doc(older, "z.pdf", status=DocStatus.review, open_flags=2),
        "older/m": factory.doc(older, "m.pdf", status=DocStatus.review, open_flags=4),
        "older/done": factory.doc(older, "b.pdf", status=DocStatus.done),
        "older/queued": factory.doc(older, "c.pdf", status=DocStatus.queued),
        "gone/x": factory.doc(gone, "x.pdf", status=DocStatus.review, open_flags=8),
    }
    return docs


def test_review_queue_all_orders_oldest_batch_then_filename(api, factory):
    docs = _setup(factory)
    api.client.delete("/api/batches/gone")

    body = api.client.get("/api/review").json()

    assert body["scope"] == "all"
    assert [d["filename"] for d in body["docs"]] == ["m.pdf", "z.pdf", "a.pdf"]
    assert body["files_left"] == 3
    assert body["flags_left"] == 7
    assert body["docs"][0] == {
        "slug": docs["older/m"].slug,
        "batch_slug": "older",
        "filename": "m.pdf",
        "open_flags": 4,
    }


def test_review_queue_scoped_to_batch(api, factory):
    _setup(factory)
    body = api.client.get("/api/review", params={"batch": "older"}).json()
    assert body["scope"] == "batch"
    assert [d["filename"] for d in body["docs"]] == ["m.pdf", "z.pdf"]
    assert (body["files_left"], body["flags_left"]) == (2, 6)


def test_review_queue_scoped_to_doc(api, factory):
    docs = _setup(factory)
    body = api.client.get("/api/review", params={"doc": docs["newer/a"].slug}).json()
    assert body["scope"] == "doc"
    assert [d["slug"] for d in body["docs"]] == [docs["newer/a"].slug]
    assert (body["files_left"], body["flags_left"]) == (1, 1)


def test_review_queue_of_doc_without_open_flags_is_empty(api, factory):
    docs = _setup(factory)
    body = api.client.get("/api/review", params={"doc": docs["older/done"].slug}).json()
    assert body == {"scope": "doc", "files_left": 0, "flags_left": 0, "docs": []}


def test_review_queue_doc_scope_wins_over_batch(api, factory):
    docs = _setup(factory)
    body = api.client.get("/api/review", params={"doc": docs["newer/a"].slug, "batch": "older"}).json()
    assert body["scope"] == "doc"
    assert len(body["docs"]) == 1


def test_review_queue_unknown_scope_is_404(api):
    assert api.client.get("/api/review", params={"batch": "nope"}).status_code == 404
    assert api.client.get("/api/review", params={"doc": "nope"}).status_code == 404


def test_review_queue_empty(api):
    assert api.client.get("/api/review").json() == {"scope": "all", "files_left": 0, "flags_left": 0, "docs": []}
