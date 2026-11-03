# syntax=docker/dockerfile:1
# DocDig: one image, Angular SPA embedded in the FastAPI app, all state on the /data volume.

# ---------- 1) frontend build ----------
FROM node:lts-trixie-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 2) python dependencies (runtime set only, no vLLM) ----------
FROM python:3.12-slim-trixie AS py
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-default-groups --no-install-project

# ---------- 3) runtime ----------
FROM python:3.12-slim-trixie AS final
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home docdig \
 && mkdir /data && chown docdig:docdig /data

WORKDIR /app
COPY --from=py /app/.venv /app/.venv
COPY pyproject.toml ./
COPY backend/ backend/
COPY data/prompts/ prompts/
COPY --from=web /web/dist/browser static/

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    WEB_DATA_DIR=/data \
    WEB_FRONTEND_DIR=/app/static \
    LLM_PROMPT_FILE=/app/prompts/llm_prompt.txt

USER docdig
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

# 1 Granian worker on purpose: the process pool inside the app does the parallel work.
CMD ["granian", "--interface", "asgi", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--loop", "uvloop", "backend.api.app:app"]
