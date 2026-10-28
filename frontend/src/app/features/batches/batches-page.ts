import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { httpResource } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { debounceTime, filter } from 'rxjs';

import { Api } from '../../core/api/api.service';
import { BatchOut, Paginated } from '../../core/api/api.types';
import { TPipe } from '../../core/i18n/i18n';
import { Realtime } from '../../core/realtime/realtime.service';
import { EmptyState } from '../../shared/ui/empty-state';
import { Paginator } from '../../shared/ui/paginator';
import { BatchCard } from './batch-card';
import { UploadDialog } from './upload-dialog';

const PAGE_SIZE = 10;

@Component({
  selector: 'app-batches-page',
  imports: [TPipe, BatchCard, UploadDialog, Paginator, EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="mx-auto max-w-5xl">
      <div class="mb-4 flex items-center gap-3">
        <h1 class="text-xl font-semibold">{{ 'batches.title' | t }}</h1>
        <span class="flex-1"></span>
        <button class="btn-primary" (click)="dialog.set(true)">{{ 'batches.new' | t }}</button>
      </div>

      @if (batches.hasValue()) {
        @let data = batches.value();
        @for (b of data.items; track b.slug) {
          <app-batch-card [batch]="b" />
        } @empty {
          <app-empty-state [title]="'batches.empty' | t" />
        }
        <app-paginator class="mt-3 block" [page]="page()" [size]="size" [total]="data.total" (pageChange)="page.set($event)" />
      } @else if (batches.error()) {
        <app-empty-state icon="lucideTriangleAlert" [title]="'common.error' | t">
          <button class="btn-outline" (click)="batches.reload()">{{ 'common.retry' | t }}</button>
        </app-empty-state>
      } @else {
        <p class="text-sm text-muted-foreground">{{ 'common.loading' | t }}</p>
      }
    </div>

    @if (dialog()) {
      <app-upload-dialog (closed)="dialog.set(false)" (created)="dialog.set(false); page.set(1); batches.reload()" />
    }
  `,
})
export class BatchesPage {
  private api = inject(Api);
  protected size = PAGE_SIZE;
  protected page = signal(1);
  protected dialog = signal(false);
  protected batches = httpResource<Paginated<BatchOut>>(() => this.api.batchesUrl(this.page(), PAGE_SIZE));

  constructor() {
    // New batches / status counts: refetch the current page (debounced, events come in bursts).
    inject(Realtime).events$
      .pipe(filter((e) => e.type === 'batch.created' || e.type === 'batch.updated'), debounceTime(400),
            takeUntilDestroyed(inject(DestroyRef)))
      .subscribe(() => this.batches.reload());
  }
}
