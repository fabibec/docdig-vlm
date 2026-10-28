import asyncio
import logging

from fastapi import WebSocket
from pydantic import BaseModel

log = logging.getLogger(__name__)


class Hub:
    """Keeps the open WebSocket connections and fans events out to them."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket):
        self.clients.discard(ws)

    async def send(self, ws: WebSocket, event: BaseModel):
        await ws.send_text(event.model_dump_json())

    async def broadcast(self, *events: BaseModel):
        for event in events:
            data = event.model_dump_json()
            results = await asyncio.gather(
                *(ws.send_text(data) for ws in list(self.clients)), return_exceptions=True
            )
            for ws, res in zip(list(self.clients), results):
                if isinstance(res, Exception):
                    self.clients.discard(ws)


hub = Hub()
