# DocDig

Digitises handwritten bird ringing forms (Vogelwarte Sempach):
PDF → page images → table crop + row geometry → LLM table extraction → human review in the browser.

```
backend/pipeline   standalone extraction pipeline (CLI, no web dependencies)
backend/api        FastAPI app: upload, process pool, review API, WebSocket, serves the SPA
frontend           Angular SPA
docs               backend.md, frontend.md, proposal.md (design decisions)
```

## Setup

```bash
uv sync                      # all groups: runtime + dev + ocr (vLLM)
uv sync --no-group ocr       # without vLLM
cp .env.example .env         # set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL_NAME
cd frontend && npm ci
```

## Playing with the pipeline

```bash
uv run python -m backend.main                       # every PDF in data/input -> data/output/<pdf>/

uv run python -m backend.pipeline run data/input/scan.pdf -o data/output
uv run python -m backend.pipeline run data/input/scan.pdf --steps cropped --page 2 --draw-rows
uv run python -m backend.pipeline run data/input/scan.pdf --steps processed   # reuse crops on disk
```

Steps: `raw` (page images), `cropped` (table crop + `pNNN.geom.json`), `processed` (LLM JSON + markdown).
Each step reads the previous step's output from disk. `--draw-rows` writes `cropped/grid/pNNN.jpg`.

## Web app (dev)

```bash
uv run granian --interface asgi --reload backend.api.app:app   # API on :8000
cd frontend && npm start                                        # SPA on :4200, proxies /api
```

Production-like without Docker: `cd frontend && npm run build`, then run granian. The app serves `frontend/dist/browser`.

## Lint

```bash
uv run ruff check backend            # config in pyproject.toml ([tool.ruff]); --fix applies safe fixes
cd frontend && npm run lint          # ESLint: typescript-eslint + angular-eslint (eslint.config.mjs); lint:fix
```

## Docker

```bash
docker compose up --build        # http://localhost:8000, data in the `docdig-data` volume
```

The image contains only the runtime dependencies (`uv sync --no-default-groups`: no vLLM, no dev tools).
Everything stateful lives in `/data`:

```
/data/state/app.db                       SQLite (local only)
/data/batches/<batch>/<doc>/input|raw|cropped|processed/...
```

Settings: see [docs/backend.md](docs/backend.md#configuration).
