import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideChevronLeft, lucideChevronRight } from '@ng-icons/lucide';

import { TPipe } from '../../core/i18n/i18n';

@Component({
  selector: 'app-paginator',
  imports: [NgIcon, TPipe],
  providers: [provideIcons({ lucideChevronLeft, lucideChevronRight })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (total() > size()) {
      <div class="flex items-center justify-end gap-2 text-xs text-muted-foreground">
        <span>{{ 'pager.range' | t: { from: from(), to: to(), total: total() } }}</span>
        <button class="btn-icon" [disabled]="page() <= 1" (click)="pageChange.emit(page() - 1)"
                [attr.aria-label]="'pager.prev' | t"><ng-icon name="lucideChevronLeft" /></button>
        <button class="btn-icon" [disabled]="page() >= pages()" (click)="pageChange.emit(page() + 1)"
                [attr.aria-label]="'pager.next' | t"><ng-icon name="lucideChevronRight" /></button>
      </div>
    }
  `,
})
export class Paginator {
  readonly page = input.required<number>();
  readonly size = input.required<number>();
  readonly total = input.required<number>();
  pageChange = output<number>();

  readonly pages = computed(() => Math.max(1, Math.ceil(this.total() / this.size())));
  readonly from = computed(() => (this.page() - 1) * this.size() + 1);
  readonly to = computed(() => Math.min(this.total(), this.page() * this.size()));
}
