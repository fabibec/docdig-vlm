import {
  ChangeDetectionStrategy, Component, ElementRef, computed, effect, inject, input, model, output, signal, viewChild,
} from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideCheck, lucideColumns, lucideTriangleAlert } from '@ng-icons/lucide';

import { Api } from '../../core/api/api.service';
import { ColumnKey, COLUMNS, FlagOut, PageInfo, PageRows } from '../../core/api/api.types';
import { I18n, TPipe } from '../../core/i18n/i18n';
import { flagText } from './record-editor';

export type PageDraft = Record<number, Partial<Record<ColumnKey, string>>>;
export type CellState = 'default' | 'yellow' | 'green' | 'blue';

interface RectBox { x: number; y: number; w: number; h: number }

/**
 * Side-by-side split view for reviewing an entire page:
 * - Slim top bar: Save & next, Skip on the left; back-to-columns button (lucideColumns) and page selector on the right.
 * - Left pane: Cropped table image with SVG highlights (yellow = open flag, green = resolved flag, blue = user edited).
 * - Right pane: Editable rendered markdown table with hover tooltips on flagged cells and inline editing.
 */
@Component({
  selector: 'app-split-review',
  imports: [TPipe, NgIcon],
  providers: [provideIcons({ lucideColumns, lucideCheck, lucideTriangleAlert })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'flex flex-col xl:min-h-0 xl:flex-1' },
  template: `
    <!-- Slim Top Bar -->
    <div class="card mb-3 flex shrink-0 flex-wrap items-center gap-2 px-3 py-1.5">
      <button type="button" class="btn-primary py-1.5" [disabled]="busy()" (click)="requestSave()">
        {{ 'review.save' | t }}
      </button>
      <button type="button" class="btn-outline py-1.5" [disabled]="busy()" (click)="skipPage()">
        {{ 'review.skip' | t }}
      </button>

      @if (confirmWarn()) {
        <div class="flex flex-wrap items-center gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-1 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-200">
          <ng-icon name="lucideTriangleAlert" size="0.95rem" class="shrink-0 text-amber-600" />
          <span>{{ 'review.unresolvedWarn' | t: { n: unresolvedCount() } }}</span>
          <button type="button" class="rounded bg-amber-600 px-2 py-0.5 font-medium text-white hover:bg-amber-700"
                  [disabled]="busy()" (click)="confirmSave()">
            {{ 'review.saveAnyway' | t }}
          </button>
          <button type="button" class="underline underline-offset-2" (click)="confirmWarn.set(false)">
            {{ 'upload.cancel' | t }}
          </button>
        </div>
      }

      @if (error()) {
        <span class="text-xs text-red-600">{{ 'review.saveFailed' | t }}</span>
      }

      <div class="ml-auto flex items-center gap-2">
        <button type="button" class="btn-outline px-2.5 py-1.5" (click)="closeSplit.emit()"
                [attr.aria-label]="'review.columnView' | t" [attr.title]="'review.columnView' | t">
          <ng-icon name="lucideColumns" size="1.1rem" />
        </button>
        <label class="flex items-center gap-2 text-sm text-muted-foreground">
          {{ 'viewer.page' | t }}
          <select class="input w-auto py-1" [value]="page()" (change)="changePage(+$any($event.target).value)">
            @for (p of pages(); track p.num) {
              <option [value]="p.num" [selected]="p.num === page()">{{ p.num }}{{ p.open_flags ? ' (' + p.open_flags + ')' : '' }}</option>
            }
          </select>
        </label>
      </div>
    </div>

    <!-- Split Panes -->
    <div class="grid gap-4 lg:grid-cols-2 xl:min-h-0 xl:flex-1 xl:grid-rows-[minmax(0,1fr)]">
      <!-- Left: Cropped table image -->
      <section class="card flex min-w-0 flex-col p-3 xl:min-h-0">
        <div #frame class="viewer-frame overscroll-contain xl:max-h-none xl:min-h-0">
          <!-- eslint-disable-next-line @angular-eslint/template/click-events-have-key-events, @angular-eslint/template/interactive-supports-focus -->
          <div class="relative cursor-crosshair" (click)="imageClick($event)">
            <img class="block w-full" [src]="api.artifactUrl(slug(), 'cropped', page())" alt="" />
            @if (rows(); as r) {
              <svg class="pointer-events-none absolute inset-0 h-full w-full" [attr.viewBox]="'0 0 ' + r.width + ' ' + r.height"
                   preserveAspectRatio="none">
                @for (c of yellowRects(); track $index) {
                  <rect [attr.x]="c.x" [attr.y]="c.y" [attr.width]="c.w" [attr.height]="c.h"
                        class="fill-amber-400/25 stroke-amber-600" stroke-width="3" />
                }
                @for (c of greenRects(); track $index) {
                  <rect [attr.x]="c.x" [attr.y]="c.y" [attr.width]="c.w" [attr.height]="c.h"
                        class="fill-emerald-400/25 stroke-emerald-600" stroke-width="3" />
                }
                @for (c of blueRects(); track $index) {
                  <rect [attr.x]="c.x" [attr.y]="c.y" [attr.width]="c.w" [attr.height]="c.h"
                        class="fill-sky-400/25 stroke-sky-600" stroke-width="3" />
                }
                @if (activeRowRect(); as s) {
                  <rect [attr.x]="s.x" [attr.y]="s.y" [attr.width]="s.w" [attr.height]="s.h"
                        class="fill-sky-400/10 stroke-sky-600" stroke-width="4" />
                }
              </svg>
            }
          </div>
        </div>
      </section>

      <!-- Right: Interactive Rendered Markdown Table -->
      <section class="card flex min-w-0 flex-col p-3 xl:min-h-0">
        @if (rows(); as r) {
          <div #tableWrap class="max-h-[75vh] overflow-auto overscroll-contain rounded-md border border-border xl:max-h-none xl:min-h-0">
            <table class="w-full border-collapse text-sm">
              <thead class="sticky top-0 z-20 bg-muted">
                <tr>
                  <th class="p-2 text-right text-xs text-muted-foreground">#</th>
                  @for (c of columns; track c) {
                    <th class="whitespace-nowrap p-2 text-left font-medium" [class]="colWidthClass(c)">{{ 'col.' + c | t }}</th>
                  }
                </tr>
              </thead>
              <tbody>
                @for (row of r.rows; track row.idx) {
                  <tr class="border-t border-border"
                      [class.bg-muted/40]="activeRow() === row.idx">
                    <td class="p-2 text-right text-xs text-muted-foreground">{{ row.idx }}</td>
                    @for (c of columns; track c) {
                      @let state = cellState(row.idx, c);
                      @let cFlags = cellFlags(row.idx, c);
                      @let val = cellValue(row.idx, c);
                      <td class="group relative p-0 align-middle transition-colors"
                          [class]="cellClass(state) + ' ' + colWidthClass(c)">
                        <div class="inline-grid w-full items-center">
                          <span class="invisible col-start-1 row-start-1 whitespace-pre px-2 py-1.5 text-sm" aria-hidden="true">{{ val || ' ' }}&#8203;</span>
                          <input [id]="'split-cell-' + row.idx + '-' + c"
                                 class="col-start-1 row-start-1 w-full min-w-0 bg-transparent px-2 py-1.5 text-sm outline-none focus:bg-background/80 focus:ring-2 focus:ring-primary"
                                 [value]="val"
                                 [attr.aria-label]="('col.' + c | t) + ' #' + row.idx"
                                 (focus)="onCellFocus(row.idx)"
                                 (input)="onCellInput(row.idx, c, $event)"
                                 (keydown.enter)="$event.preventDefault(); onCellEnter(row.idx, c)" />
                        </div>

                        @if (cFlags.length > 0) {
                          <div class="pointer-events-none hidden absolute z-30 w-64 rounded-md border border-border bg-popover p-2.5 text-xs text-popover-foreground shadow-lg group-hover:pointer-events-auto group-hover:block group-focus-within:pointer-events-auto group-focus-within:block"
                               [class]="popoverPosClass(row.idx, r.rows.length, c)">
                            @for (f of cFlags; track f.id) {
                              <div class="mb-1.5 last:mb-0">
                                <div class="flex items-start justify-between gap-2">
                                  <p class="font-medium">
                                    <span class="mr-1 rounded bg-muted px-1 py-0.5 text-[0.65rem] uppercase tracking-wide">{{ 'source.' + f.source | t }}</span>
                                    {{ text(f) }}
                                  </p>
                                  @if (state === 'yellow') {
                                    <button type="button"
                                            class="inline-flex size-5 shrink-0 items-center justify-center rounded bg-emerald-600 text-white hover:bg-emerald-700"
                                            [attr.title]="'review.resolveCell' | t"
                                            [attr.aria-label]="'review.resolveCell' | t"
                                            (click)="markResolved(row.idx, c)">
                                      <ng-icon name="lucideCheck" size="0.75rem" />
                                    </button>
                                  }
                                </div>
                                @if (f.source === 'llm' && (f.params['value'] !== undefined || f.confidence !== null)) {
                                  <p class="mt-0.5 text-[0.7rem] text-muted-foreground">
                                    @if (f.params['value'] !== undefined) { {{ 'review.read' | t: { value: f.params['value'] } }} }
                                    @if (f.confidence !== null) { · {{ 'review.confidence' | t: { pct: pct(f.confidence) } }} }
                                  </p>
                                }
                                @if (f.suggestion) {
                                  <button type="button"
                                          class="mt-1 font-medium text-primary underline underline-offset-2"
                                          (click)="applySuggestion(row.idx, c, f.suggestion)">
                                    {{ 'review.suggestion' | t }} „{{ f.suggestion }}“
                                  </button>
                                }
                              </div>
                            }
                          </div>
                        }
                      </td>
                    }
                  </tr>
                }
              </tbody>
            </table>
          </div>
        } @else {
          <p class="text-sm text-muted-foreground">{{ 'viewer.notReady' | t }}</p>
        }
      </section>
    </div>
  `,
})
export class SplitReview {
  protected api = inject(Api);
  private i18n = inject(I18n);

  readonly slug = input.required<string>();
  readonly pages = input.required<PageInfo[]>();
  readonly rows = input<PageRows | null>(null);
  readonly flags = input<FlagOut[]>([]);
  readonly busy = input(false);
  readonly error = input(false);
  readonly page = model.required<number>();

  closeSplit = output<void>();
  savePage = output<PageDraft>();
  skip = output<void>();

  protected columns = COLUMNS;
  protected readonly draft = signal<PageDraft>({});
  /** Cells whose flags were explicitly confirmed/edited in the current session (`${row}:${col}`). */
  protected readonly resolvedKeys = signal<Set<string>>(new Set());
  protected readonly activeRow = signal<number | null>(null);
  protected readonly confirmWarn = signal(false);

  private readonly frame = viewChild<ElementRef<HTMLElement>>('frame');
  private readonly tableWrap = viewChild<ElementRef<HTMLElement>>('tableWrap');

  /** Flags on the current page. */
  protected readonly pageFlags = computed(() => this.flags().filter((f) => f.page === this.page()));

  /** Count of open flags on the current page that have not been resolved locally yet. */
  protected readonly unresolvedCount = computed(() => {
    const resolved = this.resolvedKeys();
    return this.pageFlags().filter((f) => {
      if (f.resolved) return false;
      if (f.column) return !resolved.has(`${f.row}:${f.column}`);
      return !COLUMNS.some((c) => resolved.has(`${f.row}:${c}`));
    }).length;
  });

  protected readonly activeRowRect = computed<RectBox | null>(() => {
    const r = this.rows(), sel = this.activeRow();
    const row = sel === null ? undefined : r?.rows.find((x) => x.idx === sel);
    if (!r || !row) return null;
    const x0 = r.column_x.ring_no[0], x1 = r.column_x.additional_remark[1];
    return { x: x0, y: row.y0, w: x1 - x0, h: row.y1 - row.y0 };
  });

  protected readonly yellowRects = computed(() => this.rectsByState('yellow'));
  protected readonly greenRects = computed(() => this.rectsByState('green'));
  protected readonly blueRects = computed(() => this.rectsByState('blue'));

  constructor() {
    // Reset local draft & warning when switching page or document.
    effect(() => {
      this.slug();
      this.page();
      this.draft.set({});
      this.resolvedKeys.set(new Set());
      this.confirmWarn.set(false);
    });

    // Scroll the cropped image when activeRow changes.
    effect(() => {
      const rect = this.activeRowRect(), frame = this.frame()?.nativeElement, r = this.rows();
      if (!rect || !frame || !r) return;
      const img = frame.querySelector('img');
      const scroll = () => {
        const scale = (img?.clientHeight ?? 0) / r.height;
        if (scale) frame.scrollTo({ top: rect.y * scale - frame.clientHeight / 3, behavior: 'smooth' });
      };
      if (img?.complete) scroll(); else img?.addEventListener('load', scroll, { once: true });
    });
  }

  private rectsByState(target: CellState): RectBox[] {
    const r = this.rows();
    if (!r) return [];
    const out: RectBox[] = [];
    for (const row of r.rows) {
      for (const col of COLUMNS) {
        if (this.cellState(row.idx, col) === target) {
          const [x0, x1] = r.column_x[col];
          out.push({ x: x0, y: row.y0, w: x1 - x0, h: row.y1 - row.y0 });
        }
      }
    }
    return out;
  }

  protected cellFlags(rowIdx: number, col: ColumnKey): FlagOut[] {
    return this.pageFlags().filter((f) => f.row === rowIdx && (f.column === col || f.column === null));
  }

  protected cellValue(rowIdx: number, col: ColumnKey): string {
    const d = this.draft()[rowIdx]?.[col];
    if (d !== undefined) return d;
    return this.rows()?.rows.find((r) => r.idx === rowIdx)?.values[col] ?? '';
  }

  protected cellState(rowIdx: number, col: ColumnKey): CellState {
    const cFlags = this.cellFlags(rowIdx, col);
    const key = `${rowIdx}:${col}`;
    const locallyResolved = this.resolvedKeys().has(key);
    const row = this.rows()?.rows.find((r) => r.idx === rowIdx);
    const dVal = this.draft()[rowIdx]?.[col];
    const hasDraftChange = dVal !== undefined && row !== undefined && dVal.trim() !== (row.values[col] ?? '');
    const wasCorrected = row?.corrected.includes(col) ?? false;

    if (cFlags.length > 0) {
      const allBackendResolved = cFlags.every((f) => f.resolved);
      if (allBackendResolved || locallyResolved || hasDraftChange) return 'green';
      return 'yellow';
    }
    if (hasDraftChange || wasCorrected) return 'blue';
    return 'default';
  }

  protected cellClass(state: CellState): string {
    switch (state) {
      case 'yellow':
        return 'bg-amber-100 shadow-[inset_0_-2px_#b9770e] dark:bg-amber-900/40';
      case 'green':
        return 'bg-emerald-100 shadow-[inset_0_-2px_#10b981] dark:bg-emerald-900/40';
      case 'blue':
        return 'bg-sky-100 shadow-[inset_0_-2px_#0284c7] dark:bg-sky-900/40';
      default:
        return 'hover:bg-muted/50';
    }
  }

  protected colWidthClass(col: ColumnKey): string {
    switch (col) {
      case 'species':
        return 'min-w-[11rem]';
      case 'ring_no':
        return 'min-w-[6.5rem]';
      case 'additional_remark':
        return 'min-w-[8rem]';
      case 'sex':
      case 'age':
        return 'min-w-[3.25rem]';
      default:
        return 'min-w-[4.5rem]';
    }
  }

  protected popoverPosClass(rowIdx: number, totalRows: number, col: ColumnKey): string {
    const vPos = rowIdx <= 2 && totalRows > 2 ? 'top-full mt-1' : 'bottom-full mb-1';
    const hPos = col === 'additional_remark' || col === 'weight_g' || col === 'wing_length_mm' ? 'right-0' : 'left-0';
    return `${vPos} ${hPos}`;
  }

  protected onCellFocus(rowIdx: number): void {
    this.activeRow.set(rowIdx);
  }

  protected onCellInput(rowIdx: number, col: ColumnKey, ev: Event): void {
    const value = (ev.target as HTMLInputElement).value;
    this.setCellDraft(rowIdx, col, value);
  }

  private setCellDraft(rowIdx: number, col: ColumnKey, value: string): void {
    this.draft.update((d) => ({
      ...d,
      [rowIdx]: { ...(d[rowIdx] ?? {}), [col]: value },
    }));
    if (this.cellFlags(rowIdx, col).length > 0) {
      this.markResolved(rowIdx, col);
    }
  }

  protected onCellEnter(rowIdx: number, col: ColumnKey): void {
    if (this.cellFlags(rowIdx, col).length > 0) {
      this.markResolved(rowIdx, col);
    }
  }

  protected markResolved(rowIdx: number, col: ColumnKey): void {
    this.resolvedKeys.update((set) => {
      const next = new Set(set);
      next.add(`${rowIdx}:${col}`);
      return next;
    });
    if (this.unresolvedCount() === 0) {
      this.confirmWarn.set(false);
    }
  }

  protected applySuggestion(rowIdx: number, col: ColumnKey, suggestion: string): void {
    this.setCellDraft(rowIdx, col, suggestion);
    this.markResolved(rowIdx, col);
  }

  protected imageClick(ev: MouseEvent): void {
    const r = this.rows(), el = ev.currentTarget as HTMLElement;
    if (!r || !el.clientWidth) return;
    const x = (ev.offsetX / el.clientWidth) * r.width, y = (ev.offsetY / el.clientHeight) * r.height;
    const row = r.rows.find((x0) => y >= x0.y0 && y < x0.y1);
    if (!row) return;
    const col = COLUMNS.find((c) => x >= r.column_x[c][0] && x < r.column_x[c][1]) ?? COLUMNS[0];
    this.activeRow.set(row.idx);
    const inputEl = this.tableWrap()?.nativeElement.querySelector<HTMLInputElement>(`#split-cell-${row.idx}-${col}`);
    inputEl?.focus();
    inputEl?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  protected changePage(num: number): void {
    this.page.set(num);
  }

  protected requestSave(): void {
    if (this.unresolvedCount() > 0) {
      this.confirmWarn.set(true);
      return;
    }
    this.confirmSave();
  }

  protected confirmSave(): void {
    this.confirmWarn.set(false);
    this.savePage.emit(this.draft());
  }

  protected skipPage(): void {
    this.draft.set({});
    this.resolvedKeys.set(new Set());
    this.confirmWarn.set(false);
    this.skip.emit();
  }

  protected text(f: FlagOut): string {
    return flagText(this.i18n, f);
  }

  protected pct(c: number): number {
    return Math.round(c * 100);
  }
}
