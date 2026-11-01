import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideCircleCheck, lucideInbox, lucideTriangleAlert } from '@ng-icons/lucide';

@Component({
  selector: 'app-empty-state',
  imports: [NgIcon],
  providers: [provideIcons({ lucideCircleCheck, lucideInbox, lucideTriangleAlert })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-col items-center gap-3 px-4 py-16 text-center">
      <ng-icon [name]="icon()" [size]="size()" [class]="tone()" />
      <h2 class="text-lg font-semibold">{{ title() }}</h2>
      @if (text()) { <p class="max-w-md text-sm text-muted-foreground">{{ text() }}</p> }
      <ng-content />
    </div>
  `,
})
export class EmptyState {
  readonly icon = input<'lucideCircleCheck' | 'lucideInbox' | 'lucideTriangleAlert'>('lucideInbox');
  readonly tone = input('text-muted-foreground');
  readonly size = input('3rem');
  readonly title = input.required<string>();
  readonly text = input<string>('');
}
