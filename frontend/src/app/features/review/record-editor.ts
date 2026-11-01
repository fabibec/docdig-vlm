import { ChangeDetectionStrategy, Component, computed, inject, input, model, output, signal } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucidePlus, lucideTrash2 } from '@ng-icons/lucide';

import { ColumnKey, COLUMNS, FlagOut } from '../../core/api/api.types';
import { I18n, TPipe } from '../../core/i18n/i18n';

/** One table row (record) under review = all open flags sharing (page, row); may have none (row picked by hand). */
export interface ReviewRecord { key: string; page: number; row: number; flags: FlagOut[] }

export interface NoteDraft { column: ColumnKey | null; message: string }

/** Human readable flag text: translated rule codes, LLM / user messages as-is. */
export function flagText(i18n: I18n, f: FlagOut): string {
  const key = `flag.${f.code}`;
  if (f.source === 'rule' && f.code && i18n.has(key)) return i18n.t(key, f.params);
  return f.message;
}

const TONE = {
  error: 'border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/40',
  warning: 'border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40',
  user: 'border-sky-300 bg-sky-50 dark:border-sky-900 dark:bg-sky-950/40',
};

/**
 * Edits the 9 columns of one record. Shows one block per open flag (LLM / rule / reviewer note)
 * and lets the reviewer add or delete own notes.
 */
@Component({
  selector: 'app-record-editor',
  imports: [TPipe, NgIcon],
  providers: [provideIcons({ lucidePlus, lucideTrash2 })],
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
        <div class="rounded-md border px-3 py-2 text-sm" [class]="tone(f)">
          <div class="flex items-start gap-2">
            <p class="min-w-0 flex-1">
              <span class="mr-1 rounded bg-black/5 px-1 text-[0.7rem] uppercase tracking-wide dark:bg-white/10">{{ 'source.' + f.source | t }}</span>
              @if (f.column) { <span class="font-medium">{{ 'col.' + f.column | t }}:</span> }
              {{ text(f) }}
            </p>
            @if (f.source === 'user') {
              <button class="btn-icon size-6 shrink-0" [disabled]="busy()" (click)="deleteNote.emit(f.id)"
                      [attr.aria-label]="'review.deleteNote' | t" [attr.title]="'review.deleteNote' | t">
                <ng-icon name="lucideTrash2" size="0.9rem" />
              </button>
            }
          </div>
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
      } @empty {
        <p class="text-sm text-muted-foreground">{{ 'review.noFlags' | t }}</p>
      }

      <!-- reviewer note -->
      @if (noteOpen()) {
        <form class="flex flex-col gap-2 rounded-md border border-sky-300 p-2 dark:border-sky-900"
              (submit)="$event.preventDefault(); submitNote()">
          <label class="flex items-center gap-2 text-sm">
            <span class="text-muted-foreground">{{ 'review.noteColumn' | t }}</span>
            <select class="input w-auto flex-1 py-1" (change)="noteColumn.set($any($event.target).value || null)">
              <option value="" [selected]="noteColumn() === null">{{ 'review.wholeRow' | t }}</option>
              @for (c of columns; track c) {
                <option [value]="c" [selected]="noteColumn() === c">{{ 'col.' + c | t }}</option>
              }
            </select>
          </label>
          <textarea class="input min-h-16 text-sm" [placeholder]="'review.notePlaceholder' | t"
                    [value]="noteText()" (input)="noteText.set($any($event.target).value)"></textarea>
          <div class="flex justify-end gap-2">
            <button type="button" class="btn-outline" (click)="noteOpen.set(false)">{{ 'upload.cancel' | t }}</button>
            <button type="submit" class="btn-primary" [disabled]="busy() || !noteText().trim()">{{ 'review.addNote' | t }}</button>
          </div>
        </form>
      } @else {
        <button class="self-start text-sm text-muted-foreground hover:text-foreground" (click)="noteOpen.set(true)">
          <ng-icon name="lucidePlus" size="0.9rem" class="align-[-2px]" /> {{ 'review.addNote' | t }}
        </button>
      }
    </div>

    <form class="grid grid-cols-2 gap-3" (submit)="$event.preventDefault(); save.emit()">
      @for (c of columns; track c) {
        <div class="flex flex-col gap-1 text-sm" [class.col-span-2]="c === 'additional_remark' || c === 'species'">
          <label [for]="'field-' + c"
                 [class]="flagged().has(c) ? 'font-medium text-amber-700 dark:text-amber-400' : 'text-muted-foreground'">
            {{ 'col.' + c | t }}
          </label>
          @if (c === 'additional_remark') {
            <textarea [id]="'field-' + c" class="input min-h-20" [class.ring-2]="flagged().has(c)" [class.ring-amber-400]="flagged().has(c)"
                      [value]="values()[c] ?? ''" (input)="emit(c, $event)"></textarea>
          } @else {
            <input [id]="'field-' + c" class="input" [class.ring-2]="flagged().has(c)" [class.ring-amber-400]="flagged().has(c)"
                   [value]="values()[c] ?? ''" (input)="emit(c, $event)" />
          }
        </div>
      }
      @if (error()) { <p class="col-span-2 text-sm text-red-600">{{ 'review.saveFailed' | t }}</p> }
      <div class="col-span-2 mt-1 flex gap-2">
        <button type="submit" class="btn-primary flex-1" [disabled]="busy()">{{ 'review.save' | t }}</button>
        <button type="button" class="btn-outline" [disabled]="busy()" (click)="skip.emit()">{{ 'review.skip' | t }}</button>
      </div>
    </form>
  `,
})
export class RecordEditor {
  private i18n = inject(I18n);

  readonly record = input.required<ReviewRecord>();
  readonly values = input.required<Partial<Record<ColumnKey, string>>>();
  readonly busy = input(false);
  readonly error = input(false);
  /** Column preselected for a new note (e.g. the clicked cell). */
  readonly noteColumn = model<ColumnKey | null>(null);
  /** Open the note form (set by the parent when an unflagged row is picked). */
  readonly noteOpen = model(false);

  changed = output<{ column: ColumnKey; value: string }>();
  save = output<void>();
  skip = output<void>();
  addNote = output<NoteDraft>();
  deleteNote = output<number>();

  protected columns = COLUMNS;
  protected readonly noteText = signal('');
  protected readonly flagged = computed(() => new Set(this.record().flags.map((f) => f.column).filter((c) => c !== null)));

  protected text(f: FlagOut): string {
    return flagText(this.i18n, f);
  }

  protected tone(f: FlagOut): string {
    return f.source === 'user' ? TONE.user : TONE[f.severity];
  }

  protected pct(c: number): number {
    return Math.round(c * 100);
  }

  protected emit(column: ColumnKey, ev: Event): void {
    this.changed.emit({ column, value: (ev.target as HTMLInputElement).value });
  }

  protected submitNote(): void {
    const message = this.noteText().trim();
    if (!message) return;
    this.addNote.emit({ column: this.noteColumn(), message });
    this.noteText.set('');
    this.noteOpen.set(false);
  }
}
