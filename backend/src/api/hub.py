import asyncio

from fastapi import WebSocket


class Hub:
    """Keeps the open WebSocket connections and fans events out to them."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None   # set at startup so sync endpoints can emit

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket):
        self.clients.discard(ws)

    async def broadcast(self, event: dict):
        for ws in list(self.clients):
            try:
                await ws.send_json(event)
            except Exception:
                self.clients.discard(ws)

    def emit(self, event: dict):
        """Call from normal (sync) endpoints."""
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.broadcast(event), self.loop)


hub = Hub()
