# AGENTS.md

Guidance for coding agents. Human docs: `README.md`, `docs/backend.md`, `docs/frontend.md`. Open problems
(`OP n`) and ideas: `Notes.md`. Read the relevant doc before changing a subsystem; it is kept up to date.

## What this is

DocDig digitises handwritten bird ringing forms (Vogelwarte Sempach). Flow:
PDF → `raw/` page JPGs → `cropped/` table crop + `pNNN.geom.json` (rows/cols in px, orientation fixed)
→ `processed/` LLM table (`.json` / `.md` / `.csv`) → flags → human review in the Angular SPA.

## Layout

```
backend/pipeline/            standalone pipeline, NO imports from backend/api
  __init__.py                build_pipeline(): ImgExtractor → TableExtractor → TableOrientationDetector → LlmExtractor
                             → LlmPostprocessor (ditto marks)
  steps/                     one class per step (PipelineStep: name, setup, execute, close)
  models/pipeline_parts.py   Pipeline, PipelineTask, collect_result (disk → DocResult)
  models/results.py          PageGeometry, PageResult, DocResult
  models/llm_responses.py    ExtractionResult / PageTable / Uncertainty (LLM structured output)
  settings.py                LLM_* / OCR settings from .env
  backup/                    old OCR code, excluded from ruff; ignore
backend/api/                 FastAPI app (granian, 1 worker; ProcessPoolExecutor does the work)
  models.py db.py            SQLModel tables + SQLite WAL + _migrate_columns()
  schemas.py enums.py        every request/response/WS model
  routes/                    batches, docs, review, realtime (/ws, /health)
  processing.py worker.py    pool orchestration / code running inside pool processes
  flags.py queries.py        flag generation / shared read helpers + schema builders
  storage.py                 ArtifactStore: the only place that builds absolute paths
frontend/src/app/            Angular 22, standalone, zoneless, signals, httpResource, Tailwind 4 + zard
  core/api/api.types.ts      hand-written TS mirror of backend/api/schemas.py
  core/i18n/{de,en,fr}.ts    dictionaries (de.ts is the source of TranslationKey)
  features/{batches,review,document}/   pages; shared/ui/ reusable components
  shared/core, shared/utils  vendored zard code: do not edit, not linted
frontend/biome.json lint/     Biome config + GritQL plugins for Angular rules (OnPush, readonly signals, @if/@for)
benchmark/                   `uv run -m benchmark run|report`: collect.py (LLM → raw cells.csv), scoring.py, report.py
backend/logging_config.py    LOG_* settings, console text + rotating JSON-lines file, worker log queue
tests/                       pytest: api/ (routes, flags, processing, WS, DB), pipeline/, benchmark/, test_logging.py
  conftest.py                dummy LLM env, LOG_FILE=, tmp WEB_DATA_DIR (set before imports)
  api/conftest.py helpers.py `api` fixture (own SQLite + artifact dir, no process pool) + Factory for rows
.gitlab-ci.yml               test (pytest+ruff, biome+ng test) → build → health-check → manual promote/deploy
data/prompts/llm_prompt.txt  the LLM prompt (copied into the Docker image)
data/dataset/                ground truth (pdfs/ + output/<pdf>_pNNN.csv, `;`-separated) — edit only on request
```

## Commands

```bash
uv sync                                    # Python 3.12; all groups (dev + ocr)
uv run pytest                              # ~20 s, no network; --cov=backend for coverage
uv run ruff check backend [--fix]          # line length 120
cd frontend && pnpm test                   # Angular unit tests
cd frontend && pnpm lint                   # Biome lint + format check; pnpm lint:fix
cd frontend && pnpm build                  # type-checks templates too
uv run granian --interface asgi --reload backend.api.app:app   # API :8000
cd frontend && pnpm start                  # SPA :4200 (proxies /api + /ws)
uv run python -m backend.pipeline run <pdf> -o data/output [--steps raw,cropped,processed] [--page N] [--draw-rows]
docker compose up --build                  # :8000, state in ./data/docker
```

The `processed` step and the benchmark call a paid LLM API (`.env`). Do not run them unless asked;
`--steps raw,cropped` and the tests need no network.

## Cross-cutting changes: keep these in sync

- **API schema change:** `backend/api/schemas.py` → `frontend/src/app/core/api/api.types.ts` (by hand) →
  API table in `docs/backend.md`.
- **New DB column:** `models.py` **and** an `ALTER TABLE` in `db._migrate_columns()` (no Alembic; existing
  DBs in `data/state/` and `data/docker/state/` must keep working). Update the DB table in `docs/backend.md`.
- **New UI string:** add the key to `de.ts`, `en.ts` **and** `fr.ts` (missing keys fail the build).
  Singular/plural are separate keys (`…One` / plural).
- **New WS event:** `schemas.py` (`ServerEvent` union) → `api.types.ts` → listeners (`batches-page.ts`,
  `review-page.ts`, `realtime.service.ts`).
- **New pipeline step:** class in `steps/`, export in `steps/__init__.py` (lazy `__getattr__` for heavy
  optional deps), register in `build_pipeline()`. Its `name` must be an existing step name / `ArtifactState`
  (`raw`, `cropped`, `processed`) or the API progress events break; CLI-only steps go into `__main__._steps`.
- **Column set change:** `enums.ColumnKey` + `COLUMN_GRID_SPAN`, `flags.HEADER_KEYS`, LLM prompt, benchmark
  `scoring.COLUMNS`, frontend `col.*` i18n keys.

## Invariants

- `Row.values` = machine output, never mutated. Human edits live in `Row.corrections`; the API returns the merge.
  A correction equal to the machine value is removed (undo relies on this).
- After any flag change call `queries.refresh_doc_flags` and broadcast `doc.status`, `batch.updated`,
  `review.pending` (see existing routes).
- Batch delete is **soft** (`Batch.deleted_at`). Every query that lists batches/docs or builds the review queue
  must filter `Batch.deleted_at.is_(None)`; doc routes 404 for docs of deleted batches. Never delete files.
- Only the main process writes the DB; pool workers only run the pipeline and return `DocResult` JSON.
- All filesystem paths go through `ArtifactStore` (`storage.py`); the DB stores paths relative to the data dir.
- Geometry is in pixels of the **cropped** image; `col_bounds` has 12 lines for 11 printed columns.
  Later steps assume upright pages (the orientation step rewrites `raw/` and `cropped/`).

## Conventions

- Python: type hints, pydantic/SQLModel models, async SQLAlchemy sessions in routes. Keep `backend/pipeline`
  free of web dependencies.
- Logging: `log = logging.getLogger(__name__)`, %-style args, context via `extra={"batch": …, "doc": …,
  "page": …}`. Never use LogRecord attribute names as extra keys (`name`, `msg`, `args`, `filename`,
  `module`, `process`, …; use `file`, `pdf`, `step`). INFO for state changes, WARNING before rejecting input
  (4xx), DEBUG for reads. Never log secrets, images or prompts. Don't add handlers; `setup_logging()` owns them.
- Tests: new behaviour gets a test in the matching `tests/` folder. Use the `api` fixture and `Factory`
  (`tests/api/helpers.py`) instead of real uploads/pipeline runs; fake the LLM client, never call it.
- Frontend: components `OnPush`, state in `readonly` signals, reads via `httpResource(() => url)` (check
  `hasValue()` before `value()`), writes via `Api` methods returning Observables, `@if`/`@for`, `===`,
  icons from `@ng-icons/lucide`, buttons get `aria-label` + `title` from i18n.
- Docs are written for humans in plain, short sentences; update `docs/*.md` / `README.md` together with the
  behaviour they describe.

## Gotchas

- Exceptions raised in pool workers are re-raised as `worker.WorkerError("<Type>: <msg>")`, because many
  third-party exceptions can't be unpickled in the main process. The real traceback is in the worker's log.
- A doc can't be renamed while `queued`/`processing` (409): the worker reads `input/<filename>`.
- Coverage needs `concurrency = ["greenlet", "thread"]` in `[tool.coverage.run]` (async SQLAlchemy).
- `benchmark/results/` and `benchmark/cache/` are generated output; never edit by hand.
- `data/state/`, `data/batches/`, `data/logs/` (local dev) and `data/docker/` (compose bind mount, incl.
  `logs/`) hold real state and are git-ignored. Do not delete them; stop the container before touching `data/docker/state/app.db`.
- The Docker image installs only runtime deps (`uv sync --no-default-groups`): anything imported by the API at
  module level must be in `[project].dependencies`, not in the `ocr`/`dev` groups.
- The frontend uses pnpm: the version in `frontend/package.json` (`packageManager`), the `Dockerfile` and
  `PNPM_VERSION` in `.gitlab-ci.yml` must match. New packages with install scripts go into `pnpm-workspace.yaml`
  (`allowBuilds`), otherwise `pnpm install` fails.
- `.dockerignore` excludes `docs`, `benchmark`, `*.md` and all of `data/` except `data/prompts`.
