"""FastAPI app: API under /api, the built Angular SPA everywhere else.

Run (dev):  uv run granian --interface asgi --reload backend.api.app:app
Run (prod): granian --interface asgi --host 0.0.0.0 --port 8000 --workers 1 backend.api.app:app
Keep 1 Granian worker: the process pool inside the app provides the parallelism.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from backend.api.auth import current_user
from backend.api.config import settings
from backend.api.db import init_db
from backend.api.processing import processor
from backend.api.routes import batches, docs, realtime, review

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await processor.start()
    yield
    await processor.stop()


app = FastAPI(title="DocDig API", lifespan=lifespan)

api = APIRouter(prefix="/api")
for module in (batches, docs, review):
    api.include_router(module.router, dependencies=[Depends(current_user)])
api.include_router(realtime.router)
app.include_router(api)


def _mount_frontend(app: FastAPI, root: Path) -> None:
    """Serve the SPA: real files as-is, every other non-API path -> index.html."""
    root = root.resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        file = (root / path).resolve()
        if path and file.is_file() and file.is_relative_to(root):
            return FileResponse(file)
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        raise HTTPException(404, f"frontend not built ({root})")


_mount_frontend(app, settings.frontend_dir)
