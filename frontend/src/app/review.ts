import { Component, inject } from '@angular/core';
import { Store } from './store';
import { Pipeline } from './pipeline';

@Component({
  selector: 'app-review',
  imports: [Pipeline],
  // Mobile: pipeline first, editor second, queue last. Desktop: queue | editor | pipeline.
  template: `
    <div class="grid items-start gap-3.5 lg:grid-cols-[250px_330px_1fr]">
      <section class="order-last overflow-auto rounded-lg border border-border bg-card lg:order-none lg:max-h-[560px]">
        <div class="border-b border-border p-3"><b>Flagged records</b>
          <p class="text-xs text-muted-foreground">{{ s.pending() }} of {{ s.flags().length }} open</p></div>
        @for (f of s.flags(); track $index) {
          <button class="block w-full border-b border-l-[3px] border-border p-3 text-left text-sm"
                  [class]="$index === s.selected() ? 'border-l-amber-600 bg-muted' : 'border-l-transparent'" [class.opacity-50]="f.done"
                  (click)="s.selected.set($index)">
            <b>{{ f.done ? '✓ ' : '' }}Row {{ f.row + 1 }} · {{ s.sheet()[f.row][1] }}</b>
            <small class="block text-muted-foreground">{{ f.msg }}</small>
          </button>
        }
      </section>

      <section class="order-2 rounded-lg border border-border bg-card p-4 lg:order-none">
        <b>Record editor</b>
        @let f = s.current();
        <div class="my-3 rounded-md p-2.5 text-sm" [class]="f.kind === 'bad' ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-800'">
          <b>{{ f.msg }}</b><br>{{ f.why }}</div>
        @for (c of s.cols; track $index) {
          <label class="mt-2.5 block text-xs text-muted-foreground">{{ c }}</label>
          <input class="w-full rounded-md border bg-background px-3 py-1.5 text-sm"
                 [class]="$index === f.col && !f.done ? 'border-amber-600 bg-amber-50' : 'border-input'"
                 [value]="s.sheet()[f.row][$index]" (input)="s.setCell(f.row, $index, $any($event.target).value)">
          @if ($index === f.col && !f.done) {
            <p class="mt-1 text-xs text-muted-foreground">Suggestion:
              <a href="#" class="underline" (click)="$event.preventDefault(); s.setCell(f.row, f.col, f.suggestion)">{{ f.suggestion }}</a></p>
          }
        }
        <div class="mt-4 flex gap-2">
          <button class="rounded-md bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground" (click)="s.resolveCurrent()">Save and next</button>
          <button class="rounded-md border border-border px-3.5 py-2 text-sm" (click)="s.selected.set((s.selected() + 1) % s.flags().length)">Skip</button>
        </div>
      </section>

      <app-pipeline class="order-first rounded-lg border border-border bg-card p-4 lg:order-none" />
    </div>`,
})
export class Review { s = inject(Store); }
