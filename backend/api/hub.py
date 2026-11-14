import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel

log = logging.getLogger(__name__)


class Hub:
    """Keeps the open WebSocket connections and fans events out to them."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)
        log.debug("ws client connected (%d open)", len(self.clients), extra={"clients": len(self.clients)})

    def disconnect(self, ws: WebSocket):
        self.clients.discard(ws)
        log.debug("ws client disconnected (%d open)", len(self.clients), extra={"clients": len(self.clients)})

    async def send(self, ws: WebSocket, event: BaseModel):
        try:
            await ws.send_text(event.model_dump_json())
        except WebSocketDisconnect:
            raise  # normal close, logged by the endpoint
        except Exception as exc:
            name = getattr(event, "type", type(event).__name__)
            log.warning("ws send of %s failed: %r", name, exc, extra={"event": name})
            raise

    async def broadcast(self, *events: BaseModel):
        for event in events:
            data = event.model_dump_json()
            clients = list(self.clients)  # one snapshot, so results line up with sockets
            results = await asyncio.gather(*(ws.send_text(data) for ws in clients), return_exceptions=True)
            for ws, res in zip(clients, results, strict=True):
                if isinstance(res, Exception):
                    self.clients.discard(ws)
                    name = getattr(event, "type", type(event).__name__)
                    log.warning(
                        "ws send of %s failed, dropping client: %r (%d open)",
                        name,
                        res,
                        len(self.clients),
                        extra={"event": name, "clients": len(self.clients)},
                    )


hub = Hub()
