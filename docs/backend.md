# Backend

## Pipeline (`backend/pipeline`)

Standalone, no web dependencies. `build_pipeline()` in `__init__.py` assembles the default steps:

| Step | Class | Reads | Writes |
|---|---|---|---|
| `raw` | `ImgExtractor` | input PDF | `raw/pNNN.jpg` |
| `cropped` | `TableExtractor` | `raw/` | `cropped/pNNN.jpg`, `cropped/pNNN.geom.json`, `cropped/outlines/`, optional `cropped/grid/` |
| `processed` | `LlmExtractor` | `cropped/` | `processed/pNNN.json` (`ExtractionResult`), `processed/pNNN.md` |
| `ocr` (optional) | `OcrExtractor` | `cropped/` | `ocr/pNNN.md`. Needs the `ocr` group (vLLM); imported lazily. |

- `PipelineTask` describes one PDF: output folder, optional page filter and `on_progress(step, page, pages)` callback.
- `Pipeline.run(task, only=[...])` returns a `DocResult`: per page, the geometry and the LLM `PageTable`.

**Geometry** (`PageGeometry`) is in pixels of the cropped image:
- `header`: (y0, y1) of the header row
- `row_bounds[n-1]`: (y0, y1) of data row *n*
- `col_bounds`: x of every vertical grid line (12 lines for the 11 printed columns)

Lines closer than 0.75 × the median row height are dropped (`drop_spurious_lines`). This catches crossed-out rows and underlined handwriting.

## Web app (`backend/api`)

| Module | Purpose |
|---|---|
| `app.py` | FastAPI app, lifespan (DB + pool), `/api` router, SPA fallback |
| `config.py` | `Settings` (env prefix `WEB_`) |
| `db.py`, `models.py` | async SQLite (WAL) + SQLModel tables `Batch`, `Doc`, `Page`, `Row`, `Flag` |
| `schemas.py`, `enums.py` | every request/response + WS event model |
| `processing.py` | process pool orchestration, persistence, events |
| `worker.py` | code that runs inside pool processes (pipeline only) |
| `flags.py` | LLM uncertainties + plausibility rules → flags |
| `queries.py` | shared read helpers / schema builders |
| `storage.py` | `ArtifactStore`, the only owner of absolute paths, plus Nextcloud hook points |
| `auth.py` | `current_user` dependency (local user for now) |
| `hub.py`, `routes/` | WebSocket fan-out, HTTP routes |

### Processing

```mermaid
flowchart LR
  API["FastAPI (1 Granian worker)"] -- "run_doc(doc) per file" --> Pool["ProcessPoolExecutor (spawn)"]
  Pool -- "progress (mp.Queue)" --> Reader["reader thread"] --> Hub["WS hub"]
  Pool -- "DocResult JSON" --> API -- "only writer" --> DB[("SQLite")]
```

- One job per document. At most `WEB_POOL_SIZE` documents run in parallel; each worker builds the pipeline once.
- After a job, the main process replaces the doc's pages, rows and flags. The status becomes `review` (open flags), `done`, or `error`.
- On startup, docs left in `processing` are re-queued.

### Data rules

- `Row.values` holds the machine output and is never changed. Human edits go to `Row.corrections`. The API always returns the merged values.
- Resolving is per row: `POST /api/docs/{slug}/page/{num}/rows/{idx}/resolve` stores the changed cells and resolves every open flag of that row.
- `processed` artifacts are rendered from the DB, so the markdown includes corrections.
- `PageRows.column_x` maps each column key to its x-range in the cropped image (`additional_remark` spans the last 3 printed columns). See `COLUMN_GRID_SPAN` in `enums.py`.

### Flags

| Source | Created from | Fields |
|---|---|---|
| `llm` | every `Uncertainty` with a row number. The printed header (e.g. "Espèce") is mapped to a column key in `flags.HEADER_KEYS`. | `message` = reason, `suggestion` = best guess, `confidence` |
| `rule` | non-empty cells: `not_a_number`, `weight_out_of_range` (2–2000 g), `wing_out_of_range` (20–700 mm), `time_format` (H:MM) | `code` + `params` for the frontend translations, English `message` fallback |

### API

All routes are under `/api`. OpenAPI is at `/docs` and `/openapi.json`. The WS event types are published via `GET /api/ws/schema`.

| Method | Path | Response |
|---|---|---|
| POST | `/batches` (multipart `name`, `files[]`, PDF only) | `BatchOut` · 409 `DuplicateFiles` · 413 · 422 |
| GET | `/batches?page&size` | `Paginated[BatchOut]` |
| GET | `/batches/{slug}` · `/batches/{slug}/docs?page&size` | `BatchOut` · `Paginated[DocOut]` |
| GET | `/docs/{slug}` · `/docs/{slug}/flags?open=` | `DocDetail` · `list[FlagOut]` |
| GET | `/docs/{slug}/page/{num}/rows` | `PageRows` |
| POST | `/docs/{slug}/page/{num}/rows/{idx}/resolve` | `ResolveOut` |
| GET | `/docs/{slug}/artifacts/{raw\|cropped\|processed}/page/{num}` | JPEG / markdown |
| GET | `/review?batch=` / `?doc=` | `ReviewQueue` |
| GET | `/health` | `Health` |
| WS | `/ws` | `doc.status`, `batch.created`, `batch.updated`, `review.pending` (also sent on connect) |

## Configuration

| Env | Default | Docker |
|---|---|---|
| `WEB_DATA_DIR` | `./data` | `/data` |
| `WEB_DB_PATH` | `<data>/state/app.db` | |
| `WEB_FRONTEND_DIR` | `frontend/dist/browser` | `/app/static` |
| `WEB_POOL_SIZE` | `2` | |
| `WEB_MAX_UPLOAD_MB` | `200` (per file) | |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_NAME`, `LLM_REASONING_EFFORT`, `STEP_CONCURRENCY` | `.env` | env |
| `LLM_PROMPT_FILE` | `data/prompts/llm_prompt.txt` | `/app/prompts/llm_prompt.txt` |

## Later: auth + Nextcloud

- `auth.current_user` is already a dependency of every batch, doc and review route. A Nextcloud login (username + app password checked via OCS, then an HttpOnly session cookie) only needs to replace it. `Batch.owner` is stored.
- `ArtifactStore.after_upload / after_processing / after_review` are the sync hook points. The artifact tree `batches/<batch>/<doc>/` is self-contained and can be mirrored 1:1. The DB in `state/` stays local.
