"""FastAPI app: API under /api, the built Angular SPA everywhere else.

Run (dev):  uv run granian --interface asgi --reload backend.api.app:app
Run (prod): granian --interface asgi --host 0.0.0.0 --port 8000 --workers 1 backend.api.app:app
Keep 1 Granian worker: the process pool inside the app provides the parallelism.
"""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from backend.api.auth import current_user
from backend.api.config import settings
from backend.api.db import init_db
from backend.api.processing import processor
from backend.api.routes import batches, docs, realtime, review
from backend.logging_config import setup_logging

log_settings = setup_logging()  # at import: granian's process logs before the lifespan runs
log = logging.getLogger(__name__)
access_log = logging.getLogger("backend.api.access")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "starting: data_dir=%s db=%s pool_size=%d log_file=%s log_level=%s",
        settings.data_dir,
        settings.db_path,
        settings.pool_size,
        log_settings.file or "-",
        log_settings.level,
    )
    await init_db()
    await processor.start()
    yield
    log.info("shutting down")
    await processor.stop()
    log.info("stopped")


class AccessLogMiddleware:
    """One log line per /api request (method, path, status, duration_ms); unhandled errors are logged.

    Skips /api/health, WebSockets and the static SPA files. Pure ASGI, so responses are untouched.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or path == "/api/health":
            return await self.app(scope, receive, send)

        status: int | None = None
        failed = False
        method = scope.get("method", "")
        t0 = time.perf_counter()

        async def send_status(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_status)
        except Exception:
            failed = True
            log.exception("unhandled error: %s %s", method, path)
            raise
        finally:
            ms = round((time.perf_counter() - t0) * 1000, 1)
            if status is None:
                status = 500 if failed else 499  # 499: cancelled / client went away before a response
            if failed or status >= 500:
                level = logging.ERROR
            elif status >= 400:
                level = logging.WARNING
            elif method == "GET" and ("/artifacts/" in path or path.endswith("/file")):
                level = logging.DEBUG  # page images / PDFs: many per review screen
            else:
                level = logging.INFO
            extra = {"method": method, "path": path, "status": status, "duration_ms": ms}
            access_log.log(level, "%s %s %d %.1fms", method, path, status, ms, extra=extra)


app = FastAPI(title="DocDig API", lifespan=lifespan)
app.add_middleware(AccessLogMiddleware)

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
