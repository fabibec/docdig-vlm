# Frontend

Angular 22 SPA in `frontend/`: standalone components, zoneless, signals plus `httpResource`, Tailwind 4 with the zard CSS variables. The production build is embedded in the backend (served from `WEB_FRONTEND_DIR`); in Docker it lives in `/app/static`.

## Development

```bash
cd frontend
npm ci
npm start        # ng serve on :4200; proxies /api and the WebSocket to :8000 (proxy.conf.json)
npm run build    # -> frontend/dist/browser (what the backend serves)
npm run lint     # ESLint (eslint.config.mjs); `npm run lint:fix` applies safe fixes
```

Lint rules: `typescript-eslint` recommended + stylistic, `angular-eslint` TS + template + accessibility presets, plus:
- `OnPush` is required.
- Signal properties must be `readonly`.
- Use `@if` / `@for` control flow and self-closing tags.
- Use `===` everywhere.

The vendored zard code (`shared/core`, `shared/utils`) is not linted.

Start the backend alongside with `uv run granian --interface asgi --reload backend.api.app:app` (see [backend.md](backend.md)).

## Structure

```
src/app/
  app.ts, app.config.ts      shell (top bar + router outlet); zoneless, router with input binding, fetch HttpClient
  app.routes.ts              lazy routes, all children behind authGuard
  core/
    api/api.types.ts         TS mirror of backend/api/schemas.py (keep in sync by hand)
    api/api.service.ts       Api: URL helpers for httpResource / <img>, plus the write calls
    realtime/                Realtime: one WebSocket to /api/ws, reconnect with backoff
    i18n/                    I18n service + `t` pipe; de.ts (source), fr.ts, en.ts
    theme/                   Theme: system | light | dark
    auth/                    no-op interceptor + guard (Nextcloud login later)
  layout/                    top-bar.ts, logo.ts
  features/
    batches/                 batches-page, batch-card, upload-dialog
    review/                  review-page, record-editor
    document/                document-page
  shared/ui/                 pipeline-viewer, paginator, status-badge, empty-state, file-size pipe, markdown
  shared/core, shared/utils  existing zard code (untouched)
```

Conventions:
- Components are `OnPush` and keep state in signals.
- Reads go through `httpResource(() => url)`. A resource whose URL function returns `undefined` stays idle; `reload()` refetches. Check `hasValue()` before calling `value()` (calling `value()` in the error state throws).
- Writes go through `Api` and return Observables.

## Routes

| Route | Page | Notes |
|---|---|---|
| `/` | `BatchesPage` | batches, paginated 10 per page |
| `/review` | `ReviewPage` | all files needing review, oldest batch first |
| `/review/batch/:batchSlug` | `ReviewPage` | same, limited to one batch |
| `/review/file/:docSlug` | `ReviewPage` | one file; also works for `done` files (to annotate them) |
| `/:docSlug` | `DocumentPage` | read-only 3-step view and an *Annotate* button |

Route params arrive as component inputs (`withComponentInputBinding`).

## Pages

### Top bar
- Logo plus "DocDig", linking to `/`.
- *Review queue* link with a live `flags_left` badge (from the `review.pending` WS event).
- Language select (de/fr/en) and theme select.

### Batches (`/`)
- **Batch card:** name (links to the batch's review queue), creation time, file count, per-status badges, and open flags.
- **Expanding a card** lists its files (paginated, 10 per page) with live processing progress (step, page/pages).
- **Clickable files:** `review` opens `/review/file/:slug`, `done` opens `/:slug`. `queued`, `processing` and `error` files are not clickable (the error text shows as a tooltip).
- **Live updates:** `batch.*` events reload the list; `doc.status` events reload an expanded card.
- **Upload dialog:**
  - Accepts drag and drop, file selection, or a whole folder (`webkitdirectory`).
  - Non-PDF files are dropped silently, which matters for folder uploads.
  - Duplicate names are skipped with a notice; the server returns `409 DuplicateFiles` as a second line of defence.
  - The preview is paginated, 10 per page, with sizes in B / KB / MB.
  - Upload progress is shown in %.

### Review (`/review…`)

Layout:
- The shell is fixed to the viewport height and `<main>` is the scroll container.
- `xl`: three columns, records | editor | viewer. The page fills exactly the space below the top bar (no page scroll) and each column scrolls on its own; the viewer uses `fill` mode.
- `md` (tablet): records and editor stacked on the left, viewer on the right. The page scrolls normally.

**Header:**
- A file selector in batch/all scope, or the filename in file scope.
- `files_left` / `flags_left`, with singular/plural text.

**Records list:** the open flags of the current file, grouped by `(page, row)` into *records* and listed per page.
- A record shows its row number, species, a short summary, a flag count (only when > 1), and a blue dot if it has a reviewer note.
- With nothing open, it shows a green check.

**Record editor:**
- **Flags:** one block per open flag, with a source label (*AI*, *Rule*, *Note*).
  - Rule flags are translated from `code` and `params`; AI and Note messages are shown as written.
  - AI flags also show the value read and the confidence. Clicking the suggestion fills the field.
  - Colours: error = red, warning = amber, note = blue.
- **Add note:** creates a reviewer annotation (see below).
- **Fields:** all 9 columns, with flagged fields highlighted.
- ***Save & next*** sends the changed cells (`POST …/rows/{idx}/resolve`), which resolves every open flag of that row, then moves to the next record. After the last record of a file, the queue continues with the next file.
- ***Skip*** moves to the next record without saving.

**Pipeline viewer** (`shared/ui/pipeline-viewer.ts`):
- A step toggle (1 Original · 2 Table detection · 3 Result) with a page selector next to it.
- **Step 2:** the cropped table image with an SVG overlay, drawn from `PageRows.column_x` × `RowOut.y0/y1`.
  - Flagged cells are amber and the selected row is blue.
  - The view scrolls to the selected row.
- **Step 3:** *Rendered | Markdown* (with a copy button).
  - Unsaved edits are merged into the rows passed to the viewer, so step 3 updates while you type.
- **Selecting:** choosing a record jumps to its page and to step 2.
- **Picking any row** (`interactive` input): a click on a row in step 2 (click position mapped through the geometry) or a cell in step 3 opens that row in the editor, even when it has no flag.
  - The clicked column is preselected for a new note.
  - You can correct values there or add a note.

**Reviewer notes and undo:**
- **Add note:** pick a column (or *Whole row*), write a comment, then *Add note*. This calls `POST /api/docs/{slug}/flags`.
  - The note is stored as a flag with `source = "user"` and behaves like every other flag:
    - it counts towards `open_flags`
    - it puts a `done` file back into `review`
    - it appears in the queue
    - it is resolved by *Save & next*
- **Delete note:** the trash icon on a note calls `DELETE /api/docs/{slug}/flags/{id}`. Only notes can be deleted; AI and rule flags can only be resolved.
- **Undo save:** after *Save & next*, a bar offers *Undo* for 10 s. Undo:
  1. writes back the row's previous values, which restores `corrections` exactly;
  2. reopens the flags the save resolved (`POST /api/docs/{slug}/flags/reopen`);
  3. re-selects the row.

**Live updates:** `review.pending` reloads the queue. `doc.status` for the open file reloads its flags, rows and detail.

### Document (`/:docSlug`)
- Header: filename, batch, page count, status, and *Annotate* (opens `/review/file/:slug`).
- The read-only pipeline viewer, starting on step 3.

## Realtime

`Realtime` (root service) keeps a single WebSocket open to `/api/ws`. It reconnects with exponential backoff, up to 30 s.
- `events$`: every server event (`doc.status`, `batch.created`, `batch.updated`, `review.pending`).
- `pending`: latest `{files_left, flags_left}`. The server also sends this on connect.
- `progress`: per doc slug `{step, page, pages}` while the doc is `processing`.

## i18n

- `de.ts` is the source dictionary and defines `TranslationKey`. `fr.ts` and `en.ts` are typed `Dictionary`, so a missing key is a compile error.
- Use `{{ 'key' | t }}` or `{{ 'key' | t: { n: 3 } }}`. Placeholders are `{name}`. The pipe is impure so it follows language changes. Dynamic keys (`'col.' + c`) are accepted as plain strings.
- Singular/plural uses separate keys (`batches.fileOne` / `batches.files`, …).
- The language is stored in `localStorage` (`docdig.lang`) and defaults to the browser language, falling back to `de`.

## Theme

`Theme.mode` is `system | light | dark`, stored in `localStorage` (`docdig.theme`). It toggles `.dark` on `<html>`. `system` follows `prefers-color-scheme` live.

## Auth (prepared)

`core/auth/auth.ts` contains a pass-through `authInterceptor` and an always-true `authGuard`. Both are already wired in. Nextcloud login (username + app password → session cookie) will fill them in and add a `/login` route.
