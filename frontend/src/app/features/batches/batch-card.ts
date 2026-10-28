import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, input, signal } from '@angular/core';
import { httpResource } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideChevronDown, lucideChevronRight, lucideLoaderCircle } from '@ng-icons/lucide';
import { debounceTime, filter } from 'rxjs';

import { Api } from '../../core/api/api.service';
import { BatchOut, DocOut, DocStatus, Paginated } from '../../core/api/api.types';
import { I18n, TPipe } from '../../core/i18n/i18n';
import { Realtime } from '../../core/realtime/realtime.service';
import { Paginator } from '../../shared/ui/paginator';
import { StatusBadge } from '../../shared/ui/status-badge';

const PAGE_SIZE = 10;
const STATUSES: DocStatus[] = ['review', 'processing', 'queued', 'done', 'error'];

@Component({
  selector: 'app-batch-card',
  imports: [RouterLink, NgTemplateOutlet, NgIcon, TPipe, StatusBadge, Paginator],
  providers: [provideIcons({ lucideChevronDown, lucideChevronRight, lucideLoaderCircle })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @let b = batch();
    <section class="card mb-3">
      <div class="flex flex-wrap items-center gap-3 p-3">
        <button class="btn-icon" (click)="expanded.set(!expanded())"
                [attr.aria-label]="(expanded() ? 'batches.hideFiles' : 'batches.showFiles') | t">
          <ng-icon [name]="expanded() ? 'lucideChevronDown' : 'lucideChevronRight'" />
        </button>
        <a [routerLink]="['/review/batch', b.slug]" class="font-semibold hover:underline"
           [attr.title]="'batches.review' | t">{{ b.name }}</a>
        <span class="text-sm text-muted-foreground">{{ created() }} · {{ (b.num_docs === 1 ? 'batches.fileOne' : 'batches.files') | t: { n: b.num_docs } }}</span>
        <span class="flex-1"></span>
        @for (s of statuses; track s) {
          @if (b.counts[s]) { <app-status-badge [status]="s" [count]="b.counts[s]" /> }
        }
        @if (b.open_flags) {
          <span class="whitespace-nowrap text-sm text-muted-foreground">{{ (b.open_flags === 1 ? 'batches.flagOne' : 'batches.flags') | t: { n: b.open_flags } }}</span>
        }
      </div>

      @if (expanded()) {
        @if (docs.hasValue()) {
          @let d = docs.value();
          <ul>
            @for (doc of d.items; track doc.slug) {
              <li class="border-t border-border">
                @if (link(doc); as target) {
                  <a [routerLink]="target" class="flex items-center gap-3 px-4 py-2 text-sm hover:bg-muted">
                    <ng-container *ngTemplateOutlet="row; context: { $implicit: doc }" />
                  </a>
                } @else {
                  <div class="flex items-center gap-3 px-4 py-2 text-sm text-muted-foreground" [attr.title]="doc.error">
                    <ng-container *ngTemplateOutlet="row; context: { $implicit: doc }" />
                  </div>
                }
              </li>
            }
          </ul>
          <app-paginator class="block border-t border-border px-3 py-2" [page]="page()" [size]="size" [total]="d.total"
                         (pageChange)="page.set($event)" />
        } @else {
          <p class="border-t border-border px-4 py-2 text-sm text-muted-foreground">{{ 'common.loading' | t }}</p>
        }
      }
    </section>

    <ng-template #row let-doc>
      <span class="min-w-0 flex-1 truncate">{{ doc.filename }}</span>
      @if (rt.progress()[doc.slug]; as p) {
        <span class="flex items-center gap-1 text-xs text-muted-foreground">
          <ng-icon name="lucideLoaderCircle" class="animate-spin" />
          {{ 'step.' + p.step | t }} {{ p.page }}/{{ p.pages }}
        </span>
      }
      <app-status-badge [status]="doc.status" />
      <span class="min-w-32 whitespace-nowrap text-right text-muted-foreground">{{ doc.open_flags ? ((doc.open_flags === 1 ? 'batches.flagOne' : 'batches.flags') | t: { n: doc.open_flags }) : '–' }}</span>
    </ng-template>
  `,
})
export class BatchCard {
  private api = inject(Api);
  private i18n = inject(I18n);
  protected rt = inject(Realtime);

  batch = input.required<BatchOut>();
  protected size = PAGE_SIZE;
  protected statuses = STATUSES;
  protected expanded = signal(false);
  protected page = signal(1);
  protected docs = httpResource<Paginated<DocOut>>(() =>
    this.expanded() ? this.api.batchDocsUrl(this.batch().slug, this.page(), PAGE_SIZE) : undefined,
  );
  protected created = computed(() =>
    new Intl.DateTimeFormat(this.i18n.locale(), { dateStyle: 'medium', timeStyle: 'short' })
      .format(new Date(this.batch().created_at)),
  );

  constructor() {
    inject(Realtime).events$
      .pipe(filter((e) => e.type === 'doc.status' && e.doc.batch_slug === this.batch().slug && this.expanded()),
            debounceTime(300), takeUntilDestroyed(inject(DestroyRef)))
      .subscribe(() => this.docs.reload());
  }

  /** Only finished docs are clickable: review -> review queue, done -> detail page. */
  protected link(doc: DocOut): string[] | null {
    if (doc.status === 'review') return ['/review/file', doc.slug];
    if (doc.status === 'done') return ['/', doc.slug];
    return null;
  }
}
