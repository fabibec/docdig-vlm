import {
  ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, linkedSignal, signal, untracked,
} from '@angular/core';
import { httpResource } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { debounceTime } from 'rxjs';

import { Api } from '../../core/api/api.service';
import { ColumnKey, DocDetail, FlagOut, PageRows, ReviewQueue } from '../../core/api/api.types';
import { I18n, TPipe } from '../../core/i18n/i18n';
import { Realtime } from '../../core/realtime/realtime.service';
import { EmptyState } from '../../shared/ui/empty-state';
import { CellRef, PipelineViewer, ViewerStep } from '../../shared/ui/pipeline-viewer';
import { flagText, RecordEditor, ReviewRecord } from './record-editor';

type Draft = Partial<Record<ColumnKey, string>>;

/**
 * Review queue for `/review` (all), `/review/batch/:batchSlug` and `/review/file/:docSlug`.
 * Files come oldest first from the backend; within a file the flagged records are worked through in order.
 */
@Component({
  selector: 'app-review-page',
  imports: [TPipe, RouterLink, EmptyState, PipelineViewer, RecordEditor],
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
                      <div class="flex gap-2">
                        <span class="text-muted-foreground">#{{ r.row }}</span>
                        <span class="truncate font-medium">{{ species(r) }}</span>
                        @if (r.flags.length > 1) {
                          <span class="ml-auto rounded-full bg-amber-100 px-1.5 text-xs text-amber-800 dark:bg-amber-900/50 dark:text-amber-300">
                            {{ r.flags.length }}
                          </span>
                        }
                      </div>
                      <p class="truncate text-xs text-muted-foreground">{{ summary(r) }}</p>
                    </button>
                  }
                } @empty {
                  <p class="text-sm text-muted-foreground">{{ 'review.noRecords' | t }}</p>
                }
              </div>
            </section>

            <!-- editor -->
            <section class="card p-4">
              @if (selected(); as rec) {
                <app-record-editor [record]="rec" [values]="editorValues()" [saving]="saving()" [error]="saveError()"
                                   (changed)="edit($event.column, $event.value)" (save)="save()" (skip)="skip()" />
              } @else {
                <p class="text-sm text-muted-foreground">{{ 'review.selectRecord' | t }}</p>
              }
            </section>
          </div>

          <!-- viewer -->
          <section class="card min-w-0 p-4">
            @if (detail.hasValue()) {
              <app-pipeline-viewer [slug]="slug()!" [pages]="detail.value().pages" [rows]="viewRows()"
                                   [selectedRow]="selected()?.page === page() ? selected()!.row : null"
                                   [flagged]="flaggedCells()" [(page)]="page" [(step)]="step"
                                   (cellClick)="selectRow($event)" />
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
  `,
})
export class ReviewPage {
  private api = inject(Api);
  private i18n = inject(I18n);

  // route params (withComponentInputBinding)
  batchSlug = input<string>();
  docSlug = input<string>();

  protected queue = httpResource<ReviewQueue>(() => this.api.reviewUrl({ batch: this.batchSlug(), doc: this.docSlug() }));

  /** File picked in the selector; falls back to the oldest file in the queue. */
  protected chosen = signal<string | null>(null);
  protected slug = computed(() => {
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
  protected selected = computed(() => this.records().find((r) => r.key === this.selectedKey()) ?? null);

  protected page = linkedSignal<number>(() => (this.slug() ? 1 : 1));
  protected step = signal<ViewerStep>('cropped');
  protected rows = httpResource<PageRows>(() => (this.slug() ? this.api.rowsUrl(this.slug()!, this.page()) : undefined));

  /** Unsaved edits of the selected record; merged into the viewer rows for a live step-3 preview. */
  protected draft = signal<Draft>({});
  protected saving = signal(false);
  protected saveError = signal(false);

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
        if (e.type === 'doc.status' && e.doc.slug === this.slug() && !this.saving()) {
          this.detail.reload();
          this.flags.reload();
        }
      });
  }

  protected select(r: ReviewRecord): void {
    this.selectedKey.set(r.key);
    this.draft.set({});
    this.saveError.set(false);
    this.page.set(r.page);
    this.step.set('cropped');
  }

  /** Click on a highlighted cell in the result table. */
  protected selectRow(row: number): void {
    const r = this.records().find((x) => x.page === this.page() && x.row === row);
    if (r) this.select(r);
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

  protected save(): void {
    const cur = this.selected(), slug = this.slug();
    if (!cur || !slug || this.saving()) return;
    const next = this.nextAfter(cur);
    this.saving.set(true);
    this.saveError.set(false);
    this.api.resolveRow(slug, cur.page, cur.row, { values: this.draft() }).subscribe({
      next: () => {
        this.saving.set(false);
        if (next) {
          this.select(next);
          this.flags.reload();
          this.detail.reload();
          this.rows.reload();
        } else {
          // File finished: the queue drops it and slug() moves on to the next file.
          this.selectedKey.set(null);
          this.draft.set({});
          this.chosen.set(null);
        }
        this.queue.reload();
      },
      error: () => {
        this.saving.set(false);
        this.saveError.set(true);
      },
    });
  }

  protected species(r: ReviewRecord): string {
    const rows = this.rows.hasValue() && this.rows.value().num === r.page ? this.rows.value().rows : [];
    return rows.find((x) => x.idx === r.row)?.values.species ?? '';
  }

  protected summary(r: ReviewRecord): string {
    return r.flags.map((f) => flagText(this.i18n, f)).join(' · ');
  }
}
