"""/api/health, /api/ws/schema and the /api/ws event stream."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.app import _mount_frontend
from backend.api.enums import DocStatus
from backend.api.hub import hub
from backend.api.schemas import ReviewEvent


def test_health(api):
    res = api.client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_ws_schema_publishes_event_envelope(api):
    assert api.client.get("/api/ws/schema").json() == {
        "event": {"type": "review.pending", "files_left": 0, "flags_left": 0}
    }
    schema = api.client.get("/openapi.json").json()["components"]["schemas"]
    assert {"DocEvent", "BatchEvent", "ReviewEvent", "ServerEventEnvelope"} <= set(schema)


def test_ws_sends_review_counts_on_connect(api, factory):
    batch = factory.batch()
    factory.doc(batch, "a.pdf", status=DocStatus.review, open_flags=2)
    factory.doc(batch, "b.pdf", status=DocStatus.review, open_flags=3)
    factory.doc(batch, "c.pdf", status=DocStatus.done)

    with api.client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json() == {"type": "review.pending", "files_left": 2, "flags_left": 5}


def test_ws_receives_broadcasts_from_writes(api, factory):
    factory.batch("Old", slug="b")
    with api.client.websocket_connect("/api/ws") as ws:
        ws.receive_json()  # initial review.pending
        api.client.patch("/api/batches/b", json={"name": "New"})
        event = ws.receive_json()
        assert event["type"] == "batch.updated"
        assert event["batch"]["name"] == "New"

        api.client.delete("/api/batches/b")
        assert [ws.receive_json()["type"] for _ in range(2)] == ["batch.deleted", "review.pending"]


def test_ws_client_is_removed_on_disconnect(api):
    with api.client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        assert len(hub.clients) == 1
    api.client.get("/api/health")  # give the server a turn to process the close
    assert len(hub.clients) == 0


def test_broadcast_drops_failing_clients(api):
    class Broken:
        async def send_text(self, _):
            raise RuntimeError("gone")

    class Ok:
        def __init__(self):
            self.sent = []

        async def send_text(self, data):
            self.sent.append(data)

    ok, broken = Ok(), Broken()
    hub.clients.update({ok, broken})
    api.run(hub.broadcast, ReviewEvent(files_left=1, flags_left=2))

    assert hub.clients == {ok}
    assert ok.sent == ['{"type":"review.pending","files_left":1,"flags_left":2}']


# ---------- SPA fallback (app.py) ----------


def test_spa_serves_files_and_falls_back_to_index(tmp_path):
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text("<app-root></app-root>")
    (root / "main.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("secret")
    app = FastAPI()
    _mount_frontend(app, root)
    client = TestClient(app)

    assert client.get("/main.js").text == "console.log(1)"
    deep = client.get("/batches/some-slug")
    assert deep.text == "<app-root></app-root>"
    assert deep.headers["cache-control"] == "no-cache"
    # %2e%2e survives client-side URL normalisation and reaches the handler as "..".
    assert client.get("/%2e%2e/secret.txt").text == "<app-root></app-root>"
    assert client.get("/api/unknown").status_code == 404


def test_spa_without_build_is_404(tmp_path):
    app = FastAPI()
    _mount_frontend(app, tmp_path / "missing")
    res = TestClient(app).get("/")
    assert res.status_code == 404
    assert "frontend not built" in res.json()["detail"]
