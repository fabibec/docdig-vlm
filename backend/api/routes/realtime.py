from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api.db import SessionLocal
from backend.api.hub import hub
from backend.api.queries import review_stats
from backend.api.schemas import Health, ReviewEvent, ServerEventEnvelope

router = APIRouter(tags=["realtime"])


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """Server -> client events (see ServerEvent). Sends the current review counts on connect."""
    await hub.connect(ws)
    try:
        async with SessionLocal() as session:
            files_left, flags_left = await review_stats(session)
        await hub.send(ws, ReviewEvent(files_left=files_left, flags_left=flags_left))
        while True:
            await ws.receive_text()  # we don't expect messages; this detects disconnects
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)


@router.get("/ws/schema", response_model=ServerEventEnvelope, summary="WebSocket event schema (docs only)")
async def ws_schema():
    """Not meant to be called: publishes the WS event types in OpenAPI for type generation."""
    return ServerEventEnvelope(event=ReviewEvent(files_left=0, flags_left=0))


@router.get("/health", response_model=Health)
async def health():
    return Health()
