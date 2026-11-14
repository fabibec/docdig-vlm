# DocDig

Digitises handwritten bird ringing forms (Vogelwarte Sempach):
PDF → page images → table crop + row geometry (+ orientation fix) → LLM table extraction → human review in the browser.

```
backend/pipeline   standalone extraction pipeline (CLI, no web dependencies)
backend/api        FastAPI app: upload, process pool, review API, WebSocket, serves the SPA
frontend           Angular SPA
benchmark          scores pipeline output against hand-made reference CSVs
tests              pytest suite (pipeline + API)
data/prompts       LLM prompt (llm_prompt.txt)
data/dataset       benchmark ground truth: pdfs/ + output/<pdf>_pNNN.csv
docs               backend.md, frontend.md, proposals/ (design decisions)
```

Further reading: [docs/backend.md](docs/backend.md), [docs/frontend.md](docs/frontend.md).
Open problems and ideas are tracked in [Notes.md](Notes.md).

## Setup

```bash
uv sync                      # all groups: runtime + dev + ocr
uv sync --no-group ocr       # without the optional local OCR steps
cp .env.example .env         # set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL_NAME
cd frontend && npm ci
```

The PDF → image step needs `poppler` (`pdftoppm`) on the system. The optional OCR steps additionally need
a `llama-server` binary in `lib/` (Ovis OCR) or `tesseract` (cell-by-cell OCR).

## Playing with the pipeline

```bash
uv run python -m backend.main                       # every PDF in data/input -> data/output/<pdf>/

uv run python -m backend.pipeline run data/input/scan.pdf -o data/output
uv run python -m backend.pipeline run data/input/scan.pdf --steps cropped --page 2 --draw-rows
uv run python -m backend.pipeline run data/input/scan.pdf --steps processed   # reuse crops on disk
uv run python -m backend.pipeline run data/input/scan.pdf --steps cropped,tesseract   # experimental OCR
```

| Step | Output |
|---|---|
| `raw` | page images `raw/pNNN.jpg` |
| `cropped` | table crop `cropped/pNNN.jpg` + geometry `pNNN.geom.json`; sideways/upside-down pages are rotated upright first |
| `processed` | LLM result `processed/pNNN.json`, `.md` and `.csv` |
| `ocr` / `tesseract` | optional local OCR experiments (replace `processed`) |

Each step reads the previous step's output from disk. `--draw-rows` writes `cropped/grid/pNNN.jpg`.

## Benchmark

Compares `processed/pNNN.csv` against the reference tables in `data/dataset/output/`:

```bash
uv run -m benchmark.run_benchmark --refs scan_1972_sample_p001 --model openai/gpt-6-luna:low
uv run -m benchmark.run_benchmark --run luna=data/output          # score existing outputs only
```

Results (report, per-cell diffs, errors) land in `benchmark/results/<timestamp>/`. See the docstring of
[benchmark/run_benchmark.py](benchmark/run_benchmark.py) for all options.

## Web app (dev)

```bash
uv run granian --interface asgi --reload backend.api.app:app   # API on :8000
cd frontend && npm start                                        # SPA on :4200, proxies /api
```

Production-like without Docker: `cd frontend && npm run build`, then run granian. The app serves `frontend/dist/browser`.
Local dev state goes to `data/state/` and `data/batches/` (both git-ignored).

Main features: batch upload (with preview and rename before upload), live processing progress, cost per
document, renaming and (soft) deleting batches and files, and a review UI with a 3-column and a split view.

## Tests and lint

```bash
uv run pytest                        # all tests in tests/ (no network, no LLM, ~15 s)
uv run pytest tests/api              # only the API tests
uv run pytest -m "not slow"          # skip tests marked slow
uv run pytest --cov=backend --cov-report=term   # with coverage (pytest-cov, config in pyproject.toml)
uv run ruff check backend            # config in pyproject.toml ([tool.ruff]); --fix applies safe fixes
cd frontend && npm test              # Angular unit tests (*.spec.ts)
cd frontend && npm run lint          # ESLint: typescript-eslint + angular-eslint (eslint.config.mjs); lint:fix
```

`tests/conftest.py` sets safe defaults before anything is imported: a dummy `LLM_BASE_URL`/`LLM_API_KEY`
and a temporary `WEB_DATA_DIR`, so the tests never touch `data/` or a real LLM. The API tests in `tests/api/`
run the FastAPI app with a fresh SQLite file and artifact folder per test (`api` fixture in
`tests/api/conftest.py`). The process pool is never started: `processor.enqueue` only records doc ids. Insert
test data with the `factory` fixture (`tests/api/helpers.py`). Files are split by area: `test_batches.py`,
`test_docs.py`, `test_review.py`, `test_flags.py`, `test_processing.py`, `test_realtime.py`, `test_db.py`.

## Docker

```bash
docker compose up --build        # http://localhost:8000, data in ./data/docker (bind mount)
```

The image contains only the runtime dependencies (`uv sync --no-default-groups`: no OCR, no dev tools).
Everything stateful lives in `/data`, which `compose.yaml` maps to `./data/docker`:

```
/data/state/app.db                       SQLite (local only)
/data/batches/<batch>/<doc>/input|raw|cropped|processed/...
/data/logs/docdig.log                    rotating JSON-lines log (LOG_* in .env, see docs/backend.md)
```

Uncomment the `prompts` mount in `compose.yaml` to try a different LLM prompt without rebuilding.
CI runs on GitLab (`.gitlab-ci.yml`): `test` (ruff + pytest, ESLint + `ng test`; also on merge requests) → `build`
(push to `registry.vogelwarte.ch`) → `health-check` on `main` and `testing`. `promote` and `deploy` are manual jobs
(`testing` branch → port 8502, `production` branch → port 8501); each deploy reads `/opt/docdig/<env>/.env` and mounts
`/opt/docdig/<env>/data` as `/data` on the host.

Settings: see [docs/backend.md](docs/backend.md#configuration).
