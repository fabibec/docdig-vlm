import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { DocStatus } from '../../core/api/api.types';
import { TPipe } from '../../core/i18n/i18n';

const STYLES: Record<DocStatus, string> = {
  queued: 'bg-muted text-muted-foreground',
  processing: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200',
  review: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200',
  done: 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200',
  error: 'bg-destructive/15 text-destructive',
};

@Component({
  selector: 'app-status-badge',
  imports: [TPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="whitespace-nowrap rounded-full px-2 py-0.5 text-xs" [class]="styles[status()]">
    {{ 'status.' + status() | t }}@if (count() !== null) { · {{ count() }} }</span>`,
})
export class StatusBadge {
  readonly status = input.required<DocStatus>();
  readonly count = input<number | null>(null);
  protected styles = STYLES;
}
