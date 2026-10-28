import { ChangeDetectionStrategy, Component, computed, inject, input, output } from '@angular/core';

import { ColumnKey, COLUMNS, FlagOut } from '../../core/api/api.types';
import { I18n, TPipe } from '../../core/i18n/i18n';

/** One flagged table row (record) = all open flags sharing (page, row). */
export interface ReviewRecord { key: string; page: number; row: number; flags: FlagOut[] }

/** Human readable flag text: translated rule codes, LLM messages as-is. */
export function flagText(i18n: I18n, f: FlagOut): string {
  const key = `flag.${f.code}`;
  if (f.source === 'rule' && f.code && i18n.has(key)) return i18n.t(key, f.params);
  return f.message;
}

/** Edits the 9 columns of one record; shows one message block per open flag. */
@Component({
  selector: 'app-record-editor',
  imports: [TPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="mb-3 flex items-baseline gap-2">
      <h2 class="font-semibold">{{ 'review.editor' | t }}</h2>
      <span class="text-sm text-muted-foreground">
        {{ 'review.page' | t: { n: record().page } }} · {{ 'review.row' | t: { n: record().row } }}
      </span>
    </div>

    <div class="mb-4 flex flex-col gap-2">
      @for (f of record().flags; track f.id) {
        <div class="rounded-md border px-3 py-2 text-sm"
             [class]="f.severity === 'error'
               ? 'border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/40'
               : 'border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40'">
          <p>
            @if (f.column) { <span class="font-medium">{{ 'col.' + f.column | t }}:</span> }
            {{ text(f) }}
          </p>
          @if (f.source === 'llm' && (f.params['value'] !== undefined || f.confidence !== null)) {
            <p class="mt-1 text-xs text-muted-foreground">
              @if (f.params['value'] !== undefined) { {{ 'review.read' | t: { value: f.params['value'] } }} }
              @if (f.confidence !== null) { · {{ 'review.confidence' | t: { pct: pct(f.confidence) } }} }
            </p>
          }
          @if (f.suggestion && f.column) {
            <button class="mt-1 text-xs underline underline-offset-2 hover:text-primary"
                    (click)="changed.emit({ column: f.column, value: f.suggestion })">
              {{ 'review.suggestion' | t }} „{{ f.suggestion }}“
            </button>
          }
        </div>
      }
    </div>

    <form class="grid grid-cols-2 gap-3" (submit)="$event.preventDefault(); save.emit()">
      @for (c of columns; track c) {
        <label class="flex flex-col gap-1 text-sm" [class.col-span-2]="c === 'additional_remark' || c === 'species'">
          <span [class]="flagged().has(c) ? 'font-medium text-amber-700 dark:text-amber-400' : 'text-muted-foreground'">
            {{ 'col.' + c | t }}
          </span>
          @if (c === 'additional_remark') {
            <textarea class="input min-h-20" [class.ring-2]="flagged().has(c)" [class.ring-amber-400]="flagged().has(c)"
                      [value]="values()[c] ?? ''" (input)="emit(c, $event)"></textarea>
          } @else {
            <input class="input" [class.ring-2]="flagged().has(c)" [class.ring-amber-400]="flagged().has(c)"
                   [value]="values()[c] ?? ''" (input)="emit(c, $event)" />
          }
        </label>
      }
      @if (error()) { <p class="col-span-2 text-sm text-red-600">{{ 'review.saveFailed' | t }}</p> }
      <div class="col-span-2 mt-1 flex gap-2">
        <button type="submit" class="btn-primary flex-1" [disabled]="saving()">{{ 'review.save' | t }}</button>
        <button type="button" class="btn-outline" [disabled]="saving()" (click)="skip.emit()">{{ 'review.skip' | t }}</button>
      </div>
    </form>
  `,
})
export class RecordEditor {
  private i18n = inject(I18n);

  record = input.required<ReviewRecord>();
  values = input.required<Partial<Record<ColumnKey, string>>>();
  saving = input(false);
  error = input(false);
  changed = output<{ column: ColumnKey; value: string }>();
  save = output<void>();
  skip = output<void>();

  protected columns = COLUMNS;
  protected flagged = computed(() => new Set(this.record().flags.map((f) => f.column).filter((c) => c !== null)));

  protected text(f: FlagOut): string {
    return flagText(this.i18n, f);
  }

  protected pct(c: number): number {
    return Math.round(c * 100);
  }

  protected emit(column: ColumnKey, ev: Event): void {
    this.changed.emit({ column, value: (ev.target as HTMLInputElement).value });
  }
}
