# Frontend

Angular 22 standalone SPA (`frontend/`), zoneless, signal-based, styled with Tailwind 4 and the existing zard CSS variables in `src/styles.css`.

## Layout

```
frontend/src/app/
  app.ts                Root shell (top bar + router outlet)
  app.config.ts         Zoneless, router (withComponentInputBinding), fetch HttpClient
  app.routes.ts         Lazy routes guarded by authGuard
  core/
    api/                api.types.ts (mirrors backend/api/schemas.py), api.service.ts
    realtime/           realtime.service.ts (/api/ws with backoff, pending + progress signals)
    i18n/               i18n.ts (I18n service + impure `t` pipe), de.ts, fr.ts, en.ts
    theme/              theme.service.ts (system | light | dark, toggles .dark on <html>)
    auth/               auth.ts (no-op interceptor & guard, ready for Nextcloud session auth)
  layout/               top-bar.ts, logo.ts
  features/
    batches/            batches-page.ts, batch-card.ts, upload-dialog.ts
    review/             review-page.ts, record-editor.ts
    document/           document-page.ts
  shared/ui/            pipeline-viewer.ts, paginator.ts, status-badge.ts,
                        empty-state.ts, file-size.pipe.ts, markdown.ts
```

## Routes

| Route | Component | Purpose |
|---|---|---|
| `/` | `BatchesPage` | Paginated batch list (`size=10`), expandable file lists (`size=10`), upload dialog |
| `/review` | `ReviewPage` | Global review queue (all batches, oldest first) |
| `/review/batch/:batchSlug` | `ReviewPage` | Review queue scoped to one batch |
| `/review/file/:docSlug` | `ReviewPage` | Review queue scoped to a single file |
| `/:docSlug` | `DocumentPage` | Read-only 3-step viewer for completed documents |

Clicking a file in a batch card opens `/review/file/:slug` when its status is `review` (or when it still has open flags) and `/:slug` when `done`. `queued`, `processing`, and `error` entries are not clickable.

## Data flow & live updates

- Components fetch data declaratively with `httpResource()` against URL helpers on `Api`.
- `Realtime` maintains a single WebSocket connection to `/api/ws` with exponential-backoff reconnect:
  - `review.pending` updates the top-bar badge (`flags_left`) and reloads an open review queue.
  - `batch.created` / `batch.updated` reload the current batch page (debounced).
  - `doc.status` updates per-document step/page progress while `processing` and reloads expanded file lists.
- In `ReviewPage`, open flags for the active document are grouped by `(page, row)` into records. Selecting a record switches `PipelineViewer` to that page and to step 2 (`cropped`), draws SVG bounding boxes from `PageRows.column_x` and `RowOut.y0/y1`, and scrolls the row into view.
- Unsaved edits in `RecordEditor` are kept in a `draft` signal and merged into the rows passed to `PipelineViewer`, so step 3 (*Rendered* and *Markdown*) previews changes live before saving.

## i18n & theming

- `de.ts` defines the canonical `TranslationKey` union; `fr.ts` and `en.ts` are typed as `Dictionary` so missing keys fail at compile time. Selected language is persisted in `localStorage` (`docdig.lang`).
- Rule flags (`source === 'rule'`) are translated client-side via `flag.<code>` and `flag.params`; LLM flags render `flag.message` directly alongside confidence and the read value.
- `Theme` defaults to `system` (`prefers-color-scheme: dark`), persists to `localStorage` (`docdig.theme`), and toggles `.dark` on `document.documentElement`.

## Development & build

```bash
cd frontend
npm start        # ng serve on :4200, proxies /api and /api/ws to localhost:8000 (proxy.conf.json)
npm run build    # production bundle in frontend/dist/browser (served by FastAPI / copied in Docker)
```
