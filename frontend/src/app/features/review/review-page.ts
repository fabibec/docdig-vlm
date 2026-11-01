import {
  ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, linkedSignal, signal, untracked,
} from '@angular/core';
import { httpResource } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideCircleCheck } from '@ng-icons/lucide';
import { Observable, concatMap, debounceTime, of } from 'rxjs';

import { Api } from '../../core/api/api.service';
import { ColumnKey, DocDetail, FlagOut, PageRows, ReviewQueue } from '../../core/api/api.types';
import { I18n, TPipe } from '../../core/i18n/i18n';
import { Realtime } from '../../core/realtime/realtime.service';
import { EmptyState } from '../../shared/ui/empty-state';
import { CellRef, PipelineViewer, ViewerStep } from '../../shared/ui/pipeline-viewer';
import { flagText, NoteDraft, RecordEditor, ReviewRecord } from './record-editor';

type Draft = Partial<Record<ColumnKey, string>>;

/** What the last "Save & next" changed, so it can be undone. */
interface LastSave { slug: string; page: number; row: number; before: Draft; flagIds: number[] }

const UNDO_MS = 10_000;

/**
 * Review queue for `/review` (all), `/review/batch/:batchSlug` and `/review/file/:docSlug`.
 * Files come oldest first from the backend; within a file the flagged records are worked through in order.
 * Any other row can be picked in the viewer to correct it or attach a reviewer note (a flag with source=user).
 */
@Component({
  selector: 'app-review-page',
  imports: [TPipe, RouterLink, NgIcon, EmptyState, PipelineViewer, RecordEditor],
  providers: [provideIcons({ lucideCircleCheck })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (queue.hasValue()) {
      @let q = queue.value();
      @if (!slug()) {
        <app-empty-state icon="lucideCircleCheck" tone="text-green-600" size="4rem"
                         [title]="'review.doneTitle' | t" [text]="'review.doneText' | t">
          <a routerLink="/" class="btn-outline">{{ 'review.backHome' | t }}</a>
        </app-empty-state>
      } @else {
        <div class="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2">
          @if (q.scope !== 'doc') {
            <label class="flex min-w-0 items-center gap-2 text-sm">
              <span class="text-muted-foreground">{{ 'review.file' | t }}</span>
              <select class="input w-auto max-w-[24rem]" (change)="chosen.set($any($event.target).value)">
                @for (d of q.docs; track d.slug) {
                  <option [value]="d.slug" [selected]="d.slug === slug()">{{ d.filename }} ({{ d.open_flags }})</option>
                }
              </select>
            </label>
          } @else if (detail.hasValue()) {
            <h1 class="truncate text-lg font-semibold">{{ detail.value().filename }}</h1>
          }
          <span class="flex-1"></span>
          <div class="flex gap-4 text-sm">
            <span><b class="text-base">{{ q.files_left }}</b> <span class="text-muted-foreground"> {{ (q.files_left === 1 ? 'review.fileLeftOne' : 'review.filesLeft') | t }}</span></span>
            <span><b class="text-base">{{ q.flags_left }}</b> <span class="text-muted-foreground"> {{ (q.flags_left === 1 ? 'review.flagLeftOne' : 'review.flagsLeft') | t }}</span></span>
          </div>
        </div>

        <div class="grid gap-4 md:grid-cols-[minmax(0,340px)_minmax(0,1fr)] xl:grid-cols-[260px_340px_minmax(0,1fr)]">
          <div class="flex min-w-0 flex-col gap-4 xl:contents">
            <!-- flagged records -->
            <section class="card p-3">
              <h2 class="mb-2 font-semibold">{{ 'review.records' | t }}</h2>
              <div class="max-h-[40vh] overflow-auto pr-2 xl:max-h-[calc(100vh-14rem)]">
                @for (g of byPage(); track g.page) {
                  <h3 class="sticky top-0 bg-card py-1 text-xs font-medium uppercase text-muted-foreground">
                    {{ 'review.page' | t: { n: g.page } }}
                  </h3>
                  @for (r of g.records; track r.key) {
                    <button class="mb-1 w-full rounded-md border px-2 py-1.5 text-left text-sm transition-colors"
                            [class]="r.key === selected()?.key ? 'border-primary bg-primary/10' : 'border-transparent hover:bg-muted'"
                            (click)="select(r)">
                      <div class="flex items-center gap-2">
                        <span class="text-muted-foreground">#{{ r.row }}</span>
                        <span class="truncate font-medium">{{ species(r) }}</span>
                        <span class="ml-auto flex shrink-0 items-center gap-1">
                          @if (hasNote(r)) { <span class="size-2 rounded-full bg-sky-500" [attr.title]="'source.user' | t"></span> }
                          @if (r.flags.length > 1) {
                            <span class="rounded-full bg-amber-100 px-1.5 text-xs text-amber-800 dark:bg-amber-900/50 dark:text-amber-300">
                              {{ r.flags.length }}
                            </span>
                          }
                        </span>
                      </div>
                      <p class="truncate text-xs text-muted-foreground">{{ summary(r) }}</p>
                    </button>
                  }
                } @empty {
                  <div class="flex flex-col items-center gap-2 py-6 text-center">
                    <ng-icon name="lucideCircleCheck" size="2rem" class="text-green-600" />
                    <p class="text-sm font-medium">{{ 'review.doneTitle' | t }}</p>
                  </div>
                }
                <p class="mt-2 text-xs text-muted-foreground">{{ 'review.clickHint' | t }}</p>
              </div>
            </section>

            <!-- editor -->
            <section class="card p-4">
              @if (selected(); as rec) {
                <app-record-editor [record]="rec" [values]="editorValues()" [busy]="busy()" [error]="saveError()"
                                   [(noteColumn)]="noteColumn" [(noteOpen)]="noteOpen"
                                   (changed)="edit($event.column, $event.value)" (save)="save()" (skip)="skip()"
                                   (addNote)="addNote(rec, $event)" (deleteNote)="deleteNote($event)" />
              } @else {
                <p class="text-sm text-muted-foreground">{{ 'review.clickHint' | t }}</p>
              }
            </section>
          </div>

          <!-- viewer -->
          <section class="card min-w-0 p-4">
            @if (detail.hasValue()) {
              <app-pipeline-viewer [slug]="slug()!" [pages]="detail.value().pages" [rows]="viewRows()" [interactive]="true"
                                   [selectedRow]="selected()?.page === page() ? selected()!.row : null"
                                   [flagged]="flaggedCells()" [(page)]="page" [(step)]="step"
                                   (cellClick)="pick($event)" />
            }
          </section>
        </div>
      }
    } @else if (queue.error()) {
      <app-empty-state icon="lucideTriangleAlert" [title]="'common.error' | t">
        <button class="btn-outline" (click)="queue.reload()">{{ 'common.retry' | t }}</button>
      </app-empty-state>
    } @else {
      <p class="text-sm text-muted-foreground">{{ 'common.loading' | t }}</p>
    }

    <!-- undo bar -->
    @if (lastSave(); as ls) {
      <div class="fixed bottom-4 left-1/2 z-40 flex -translate-x-1/2 items-center gap-4 rounded-lg bg-foreground px-4 py-2 text-sm text-background shadow-lg">
        {{ 'review.saved' | t: { n: ls.row } }}
        <button class="font-semibold underline underline-offset-2" [disabled]="busy()" (click)="undo()">{{ 'review.undo' | t }}</button>
      </div>
    }
    @if (actionError()) {
      <div class="fixed bottom-4 right-4 z-40 rounded-lg bg-destructive px-4 py-2 text-sm text-white shadow-lg">{{ 'review.actionFailed' | t }}</div>
    }
  `,
})
export class ReviewPage {
  private api = inject(Api);
  private i18n = inject(I18n);

  // route params (withComponentInputBinding)
  batchSlug = input<string>();
  docSlug = input<string>();

  protected queue = httpResource<ReviewQueue>(() => this.api.reviewUrl({ batch: this.batchSlug(), doc: this.docSlug() }));

  /** File picked in the selector; falls back to the oldest file in the queue. A single-file route always shows its file. */
  protected chosen = signal<string | null>(null);
  protected slug = computed(() => {
    if (this.docSlug()) return this.docSlug()!;
    if (!this.queue.hasValue()) return null;
    const docs = this.queue.value().docs;
    return docs.find((d) => d.slug === this.chosen())?.slug ?? docs[0]?.slug ?? null;
  });

  protected detail = httpResource<DocDetail>(() => (this.slug() ? this.api.docUrl(this.slug()!) : undefined));
  protected flags = httpResource<FlagOut[]>(() => (this.slug() ? this.api.flagsUrl(this.slug()!) : undefined));

  protected records = computed<ReviewRecord[]>(() => {
    const map = new Map<string, ReviewRecord>();
    for (const f of this.flags.hasValue() ? this.flags.value() : []) {
      const key = `${f.page}:${f.row}`;
      if (!map.has(key)) map.set(key, { key, page: f.page, row: f.row, flags: [] });
      map.get(key)!.flags.push(f);
    }
    return [...map.values()];
  });
  protected byPage = computed(() => {
    const groups: { page: number; records: ReviewRecord[] }[] = [];
    for (const r of this.records()) {
      if (groups.at(-1)?.page !== r.page) groups.push({ page: r.page, records: [] });
      groups.at(-1)!.records.push(r);
    }
    return groups;
  });

  private selectedKey = signal<string | null>(null);
  /** Row picked in the viewer that has no open flags. */
  private picked = signal<ReviewRecord | null>(null);
  protected selected = computed(() => {
    const key = this.selectedKey();
    return this.records().find((r) => r.key === key) ?? (this.picked()?.key === key ? this.picked() : null);
  });

  protected page = linkedSignal<number>(() => (this.slug() ? 1 : 1));
  protected step = signal<ViewerStep>('cropped');
  protected rows = httpResource<PageRows>(() => (this.slug() ? this.api.rowsUrl(this.slug()!, this.page()) : undefined));

  /** Unsaved edits of the selected record; merged into the viewer rows for a live step-3 preview. */
  protected draft = signal<Draft>({});
  protected busy = signal(false);
  protected saveError = signal(false);
  protected actionError = signal(false);
  protected noteColumn = signal<ColumnKey | null>(null);
  protected noteOpen = signal(false);
  protected lastSave = signal<LastSave | null>(null);
  private undoTimer?: ReturnType<typeof setTimeout>;

  private selectedRowValues = computed(() => {
    const sel = this.selected();
    if (!sel || !this.rows.hasValue() || this.rows.value().num !== sel.page) return null;
    return this.rows.value().rows.find((r) => r.idx === sel.row)?.values ?? null;
  });
  protected editorValues = computed<Draft>(() => ({ ...(this.selectedRowValues() ?? {}), ...this.draft() }));

  protected viewRows = computed<PageRows | null>(() => {
    if (!this.rows.hasValue()) return null;
    const r = this.rows.value(), sel = this.selected(), draft = this.draft();
    if (!sel || sel.page !== r.num || !Object.keys(draft).length) return r;
    return { ...r, rows: r.rows.map((row) => (row.idx === sel.row ? { ...row, values: { ...row.values, ...draft } } : row)) };
  });

  protected flaggedCells = computed<CellRef[]>(() =>
    this.records().filter((r) => r.page === this.page()).flatMap((r) => r.flags.map((f) => ({ row: f.row, column: f.column }))),
  );

  constructor() {
    // Keep a valid selection: when records (re)load and nothing valid is selected, take the first one.
    effect(() => {
      const recs = this.records();
      untracked(() => {
        if (recs.length && !this.selected()) this.select(recs[0]);
      });
    });

    // Live updates (other tabs, finished processing): refresh queue + current file.
    inject(Realtime).events$
      .pipe(debounceTime(500), takeUntilDestroyed(inject(DestroyRef)))
      .subscribe((e) => {
        if (e.type === 'review.pending') this.queue.reload();
        if (e.type === 'doc.status' && e.doc.slug === this.slug() && !this.busy()) this.reloadDoc();
      });
    inject(DestroyRef).onDestroy(() => clearTimeout(this.undoTimer));
  }

  protected select(r: ReviewRecord, column: ColumnKey | null = null): void {
    this.selectedKey.set(r.key);
    this.draft.set({});
    this.saveError.set(false);
    this.noteColumn.set(column);
    this.noteOpen.set(false);
    this.page.set(r.page);
    this.step.set('cropped');
  }

  /** Click on any row/cell in the viewer: open its record, or an empty one for unflagged rows. */
  protected pick(cell: CellRef): void {
    const page = this.page(), key = `${page}:${cell.row}`;
    const existing = this.records().find((r) => r.key === key);
    if (existing) {
      if (existing.key !== this.selectedKey()) this.select(existing, cell.column);
      else this.noteColumn.set(cell.column);
      return;
    }
    this.picked.set({ key, page, row: cell.row, flags: [] });
    this.select(this.picked()!, cell.column);
  }

  protected edit(column: ColumnKey, value: string): void {
    this.draft.update((d) => ({ ...d, [column]: value }));
  }

  private nextAfter(current: ReviewRecord): ReviewRecord | null {
    const recs = this.records(), i = recs.findIndex((r) => r.key === current.key);
    return recs[i + 1] ?? recs.find((r) => r.key !== current.key) ?? null;
  }

  protected skip(): void {
    const cur = this.selected();
    const next = cur && this.nextAfter(cur);
    if (next) this.select(next);
  }

  private reloadDoc(): void {
    this.flags.reload();
    this.detail.reload();
    this.rows.reload();
  }

  /** Run a write call with the busy flag, then refresh the doc + queue. */
  private run<T>(call: Observable<T>, done: (res: T) => void = () => {}): void {
    this.busy.set(true);
    this.actionError.set(false);
    call.subscribe({
      next: (res) => {
        this.busy.set(false);
        done(res);
        this.reloadDoc();
        this.queue.reload();
      },
      error: () => {
        this.busy.set(false);
        this.actionError.set(true);
        setTimeout(() => this.actionError.set(false), 4000);
      },
    });
  }

  protected save(): void {
    const cur = this.selected(), slug = this.slug();
    if (!cur || !slug || this.busy()) return;
    const next = this.nextAfter(cur);
    const before = { ...(this.selectedRowValues() ?? {}) };
    this.saveError.set(false);
    this.busy.set(true);
    this.api.resolveRow(slug, cur.page, cur.row, { values: this.draft() }).subscribe({
      next: (res) => {
        this.busy.set(false);
        this.offerUndo({ slug, page: cur.page, row: cur.row, before, flagIds: res.resolved_flag_ids });
        this.picked.set(null);
        if (next) {
          this.select(next);
        } else {
          // File finished: in batch/all scope the queue drops it and slug() moves on to the next file.
          this.selectedKey.set(null);
          this.draft.set({});
          this.chosen.set(null);
        }
        this.reloadDoc();
        this.queue.reload();
      },
      error: () => {
        this.busy.set(false);
        this.saveError.set(true);
      },
    });
  }

  private offerUndo(ls: LastSave): void {
    clearTimeout(this.undoTimer);
    this.lastSave.set(ls);
    this.undoTimer = setTimeout(() => this.lastSave.set(null), UNDO_MS);
  }

  /** Undo the last save: restore the previous cell values, then reopen the flags it resolved. */
  protected undo(): void {
    const ls = this.lastSave();
    if (!ls) return;
    clearTimeout(this.undoTimer);
    this.lastSave.set(null);
    const call = this.api.resolveRow(ls.slug, ls.page, ls.row, { values: ls.before }).pipe(
      concatMap((res) => (ls.flagIds.length ? this.api.reopenFlags(ls.slug, ls.flagIds) : of(res))),
    );
    this.run(call, () => {
      if (ls.slug !== this.slug()) this.chosen.set(ls.slug);
      this.selectedKey.set(`${ls.page}:${ls.row}`);
      this.picked.set({ key: `${ls.page}:${ls.row}`, page: ls.page, row: ls.row, flags: [] });
      this.page.set(ls.page);
      this.draft.set({});
    });
  }

  protected addNote(rec: ReviewRecord, note: NoteDraft): void {
    const slug = this.slug();
    if (!slug) return;
    this.run(this.api.createFlag(slug, { page: rec.page, row: rec.row, column: note.column, message: note.message }), () => {
      this.selectedKey.set(rec.key); // the picked row now becomes a real record (same key)
    });
  }

  protected deleteNote(id: number): void {
    const slug = this.slug(), sel = this.selected();
    if (!slug) return;
    // Keep the row selected even if this was its last open flag.
    if (sel) this.picked.set({ ...sel, flags: [] });
    this.run(this.api.deleteFlag(slug, id));
  }

  protected hasNote(r: ReviewRecord): boolean {
    return r.flags.some((f) => f.source === 'user');
  }

  protected species(r: ReviewRecord): string {
    const rows = this.rows.hasValue() && this.rows.value().num === r.page ? this.rows.value().rows : [];
    return rows.find((x) => x.idx === r.row)?.values.species ?? '';
  }

  protected summary(r: ReviewRecord): string {
    return r.flags.map((f) => flagText(this.i18n, f)).join(' · ');
  }
}
