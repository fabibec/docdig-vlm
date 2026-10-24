import { Component, signal, inject } from '@angular/core';
import { Store } from './store';
import { Batches } from './batches';
import { Review } from './review';

@Component({
  selector: 'app-root',
  imports: [Batches, Review],
  template: `
    <header class="flex flex-wrap items-center gap-4 border-b border-border bg-card px-5 py-3">
      <h1 class="font-semibold">Ringlist Review</h1>
      <nav class="flex gap-1">
        @for (v of views; track v.id) {
          <button (click)="view.set(v.id)"
            class="rounded-md px-3 py-1.5 text-sm"
            [class]="view() === v.id ? 'bg-muted font-semibold' : 'text-muted-foreground'">
            {{ v.label }}
            @if (v.id === 'review') { <span class="ml-1 rounded-full bg-amber-600 px-1.5 text-xs text-white">{{ store.pending() }}</span> }
          </button>
        }
      </nav>
    </header>
    <main class="mx-auto max-w-[1280px] p-5">
      @if (view() === 'batches') { <app-batches (open)="view.set('review')" /> } @else { <app-review /> }
    </main>`,
})
export class App {
  store = inject(Store);
  views = [{ id: 'batches', label: 'Batches' }, { id: 'review', label: 'Review queue' }] as const;
  view = signal<'batches' | 'review'>('batches');
}
