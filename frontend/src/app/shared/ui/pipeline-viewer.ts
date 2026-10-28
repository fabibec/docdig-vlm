import {
  ChangeDetectionStrategy, Component, ElementRef, computed, effect, inject, input, model, output, signal, viewChild,
} from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideCopy, lucideCheck } from '@ng-icons/lucide';

import { Api } from '../../core/api/api.service';
import { ColumnKey, COLUMNS, PageInfo, PageRows } from '../../core/api/api.types';
import { I18n, TPipe } from '../../core/i18n/i18n';
import { toMarkdown } from './markdown';

export type ViewerStep = 'raw' | 'cropped' | 'processed';
export interface CellRef { row: number; column: ColumnKey | null }

/**
 * The 3-step view (original page / detected table / extracted result) for one document.
 * The parent owns the data (`rows` may already contain unsaved edits) and the selection.
 */
@Component({
  selector: 'app-pipeline-viewer',
  imports: [TPipe, NgIcon],
  providers: [provideIcons({ lucideCopy, lucideCheck })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="mb-3 flex flex-wrap items-center gap-2">
      <div class="inline-flex rounded-full border border-border bg-muted p-0.5">
        @for (s of steps; track s.id) {
          <button class="rounded-full px-3 py-1.5 text-sm transition-colors"
                  [class]="step() === s.id ? 'bg-primary font-medium text-primary-foreground' : 'text-muted-foreground hover:text-foreground'"
                  (click)="step.set(s.id)">{{ s.label | t }}</button>
        }
      </div>
      @if (pages().length > 1) {
        <label class="ml-auto flex items-center gap-2 text-sm text-muted-foreground">
          {{ 'viewer.page' | t }}
          <select class="input w-auto py-1" [value]="page()" (change)="page.set(+$any($event.target).value)">
            @for (p of pages(); track p.num) {
              <option [value]="p.num" [selected]="p.num === page()">{{ p.num }}{{ p.open_flags ? ' (' + p.open_flags + ')' : '' }}</option>
            }
          </select>
        </label>
      }
    </div>

    @switch (step()) {
      @case ('raw') {
        <div class="viewer-frame">
          <img class="w-full" [src]="api.artifactUrl(slug(), 'raw', page())" alt="" loading="lazy" />
        </div>
      }
      @case ('cropped') {
        <div #frame class="viewer-frame">
          <div class="relative">
            <img class="block w-full" [src]="api.artifactUrl(slug(), 'cropped', page())" alt="" />
            @if (rows(); as r) {
              <svg class="pointer-events-none absolute inset-0 h-full w-full" [attr.viewBox]="'0 0 ' + r.width + ' ' + r.height"
                   preserveAspectRatio="none">
                @for (c of flagRects(); track $index) {
                  <rect [attr.x]="c.x" [attr.y]="c.y" [attr.width]="c.w" [attr.height]="c.h"
                        class="fill-amber-400/25 stroke-amber-600" stroke-width="3" />
                }
                @if (selectedRect(); as s) {
                  <rect [attr.x]="s.x" [attr.y]="s.y" [attr.width]="s.w" [attr.height]="s.h"
                        class="fill-sky-400/20 stroke-sky-600" stroke-width="4" />
                }
              </svg>
            }
          </div>
        </div>
      }
      @case ('processed') {
        <div class="mb-2 flex items-center gap-1">
          @for (m of modes; track m) {
            <button class="rounded-md px-3 py-1 text-sm"
                    [class]="mode() === m ? 'bg-muted font-semibold' : 'text-muted-foreground'"
                    (click)="mode.set(m)">{{ 'viewer.' + m | t }}</button>
          }
          @if (mode() === 'markdown') {
            <button class="btn-outline ml-auto" (click)="copy()">
              <ng-icon [name]="copied() ? 'lucideCheck' : 'lucideCopy'" />
              {{ (copied() ? 'viewer.copied' : 'viewer.copy') | t }}
            </button>
          }
        </div>
        @if (rows(); as r) {
          @if (mode() === 'rendered') {
            <div class="max-h-[70vh] overflow-auto rounded-md border border-border">
              <table class="w-full min-w-[640px] text-sm">
                <thead class="sticky top-0 bg-muted">
                  <tr>
                    <th class="p-2 text-right text-xs text-muted-foreground">#</th>
                    @for (c of columns; track c) { <th class="p-2 text-left font-medium">{{ 'col.' + c | t }}</th> }
                  </tr>
                </thead>
                <tbody>
                  @for (row of r.rows; track row.idx) {
                    <tr class="border-t border-border" [class.bg-sky-50]="row.idx === selectedRow()"
                        [class.dark:bg-sky-950]="row.idx === selectedRow()">
                      <td class="p-2 text-right text-xs text-muted-foreground">{{ row.idx }}</td>
                      @for (c of columns; track c) {
                        @if (isFlagged(row.idx, c)) {
                          <td class="cursor-pointer bg-amber-100 p-2 shadow-[inset_0_-2px_#b9770e] dark:bg-amber-900/40"
                              (click)="cellClick.emit(row.idx)">{{ row.values[c] }}</td>
                        } @else {
                          <td class="p-2" [class.font-medium]="row.corrected.includes(c)">{{ row.values[c] }}</td>
                        }
                      }
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          } @else {
            <pre class="max-h-[70vh] overflow-auto rounded-md border border-border bg-muted p-3 text-xs">{{ markdown() }}</pre>
          }
        } @else {
          <p class="text-sm text-muted-foreground">{{ 'viewer.notReady' | t }}</p>
        }
      }
    }
  `,
})
export class PipelineViewer {
  protected api = inject(Api);
  private i18n = inject(I18n);

  slug = input.required<string>();
  pages = input.required<PageInfo[]>();
  rows = input<PageRows | null>(null);
  selectedRow = input<number | null>(null);
  flagged = input<CellRef[]>([]);
  page = model.required<number>();
  step = model<ViewerStep>('raw');
  cellClick = output<number>();

  protected steps = [
    { id: 'raw', label: 'viewer.original' },
    { id: 'cropped', label: 'viewer.table' },
    { id: 'processed', label: 'viewer.result' },
  ] as const;
  protected modes = ['rendered', 'markdown'] as const;
  protected columns = COLUMNS;
  protected mode = signal<'rendered' | 'markdown'>('rendered');
  protected copied = signal(false);
  private frame = viewChild<ElementRef<HTMLElement>>('frame');

  protected markdown = computed(() => {
    const r = this.rows();
    if (!r) return '';
    const labels = Object.fromEntries(COLUMNS.map((c) => [c, this.i18n.t(`col.${c}`)])) as Record<ColumnKey, string>;
    return toMarkdown(r.rows, labels);
  });

  private rowBox(idx: number) {
    return this.rows()?.rows.find((r) => r.idx === idx);
  }

  protected selectedRect = computed(() => {
    const r = this.rows(), sel = this.selectedRow();
    const row = sel === null ? undefined : this.rowBox(sel);
    if (!r || !row) return null;
    const x0 = r.column_x.ring_no[0], x1 = r.column_x.additional_remark[1];
    return { x: x0, y: row.y0, w: x1 - x0, h: row.y1 - row.y0 };
  });

  protected flagRects = computed(() => {
    const r = this.rows();
    if (!r) return [];
    return this.flagged().flatMap((f) => {
      const row = this.rowBox(f.row);
      if (!row || !f.column) return [];
      const [x0, x1] = r.column_x[f.column];
      return [{ x: x0, y: row.y0, w: x1 - x0, h: row.y1 - row.y0 }];
    });
  });

  constructor() {
    // Keep the highlighted row in view inside the (scrollable) table frame.
    effect(() => {
      const rect = this.selectedRect(), frame = this.frame()?.nativeElement, r = this.rows();
      if (!rect || !frame || !r) return;
      const img = frame.querySelector('img');
      const scroll = () => {
        const scale = (img?.clientHeight ?? 0) / r.height;
        if (scale) frame.scrollTo({ top: rect.y * scale - frame.clientHeight / 3, behavior: 'smooth' });
      };
      if (img?.complete) scroll(); else img?.addEventListener('load', scroll, { once: true });
    });
  }

  protected isFlagged(row: number, column: ColumnKey): boolean {
    return this.flagged().some((f) => f.row === row && f.column === column);
  }

  protected async copy(): Promise<void> {
    await navigator.clipboard?.writeText(this.markdown());
    this.copied.set(true);
    setTimeout(() => this.copied.set(false), 1500);
  }
}
