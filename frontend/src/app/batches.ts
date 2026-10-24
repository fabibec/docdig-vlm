import { Component, inject, output, signal } from '@angular/core';
import { Store } from './store';
import { NewBatchDialog } from './new-batch-dialog';

@Component({
  selector: 'app-batches',
  imports: [NewBatchDialog],
  template: `
    <div class="mb-4 flex items-center gap-3">
      <h2 class="text-lg font-semibold">Batches</h2><span class="flex-1"></span>
      <button class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground" (click)="dialog.set(true)">New batch</button>
    </div>

    <!-- Watched folder: the browser cannot watch a directory in the background.
         The real thing is a small desktop helper that POSTs new files to your API. -->
    <div class="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card p-4">
      <div class="flex-1"><b>Watched folder</b>
        <p class="text-xs text-muted-foreground">New PDFs are sent automatically as a batch (simulated here).</p></div>
      <input class="w-56 rounded-md border border-input bg-background px-3 py-1.5 text-sm" [value]="path()" (input)="path.set($any($event.target).value)">
      <button class="rounded-md border border-border px-3 py-1.5 text-sm" (click)="watching.set(!watching())">{{ watching() ? 'Stop watching' : 'Start watching' }}</button>
      @if (watching()) { <button class="rounded-md border border-border px-3 py-1.5 text-sm" (click)="simulate()">Simulate new files</button> }
    </div>

    @for (b of store.batches(); track b.name) {
      <section class="mb-3 rounded-lg border border-border bg-card">
        <div class="flex items-center gap-3 p-3"><b>{{ b.name }}</b>
          @if (b.fromFolder) { <span class="rounded-full bg-muted px-2 text-xs">from folder</span> }
          <span class="text-sm text-muted-foreground">{{ b.docs.length }} sheets</span></div>
        @for (d of b.docs; track d.name) {
          <button class="flex w-full items-center gap-3 border-t border-border px-3 py-2 text-left text-sm hover:bg-muted"
                  (click)="d.status === 'review' || d.status === 'done' ? open.emit() : null">
            <span class="flex-1">{{ d.name }}</span>
            <span class="rounded-full px-2 text-xs" [class]="badge[d.status]">{{ label[d.status] }}</span>
            <span class="w-20 text-muted-foreground">{{ d.flags ? d.flags + ' flagged' : '–' }}</span>
          </button>
        }
      </section>
    }
    @if (dialog()) { <app-new-batch-dialog (close)="dialog.set(false)" /> }`,
})
export class Batches {
  store = inject(Store);
  open = output<void>();
  dialog = signal(false);
  watching = signal(false);
  path = signal('~/Beringung/inbox');
  label = { queued: 'Queued', processing: 'Processing', review: 'Needs review', done: 'Done' };
  badge = { queued: 'bg-muted text-muted-foreground', processing: 'bg-amber-100 text-amber-800', review: 'bg-red-100 text-red-800', done: 'bg-green-100 text-green-800' };
  simulate() { this.store.addBatch('Auto: ' + (this.path().split('/').pop() || 'folder'), ['scan_001.pdf', 'scan_002.pdf', 'scan_003.pdf'], true); }
}
