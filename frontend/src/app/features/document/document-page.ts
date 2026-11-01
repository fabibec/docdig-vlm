import { ChangeDetectionStrategy, Component, inject, input, linkedSignal, signal } from '@angular/core';
import { httpResource } from '@angular/common/http';
import { RouterLink } from '@angular/router';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideArrowLeft } from '@ng-icons/lucide';

import { Api } from '../../core/api/api.service';
import { DocDetail, PageRows } from '../../core/api/api.types';
import { TPipe } from '../../core/i18n/i18n';
import { EmptyState } from '../../shared/ui/empty-state';
import { PipelineViewer, ViewerStep } from '../../shared/ui/pipeline-viewer';
import { StatusBadge } from '../../shared/ui/status-badge';

/** Read-only 3-step view for one document (`/:docSlug`). */
@Component({
  selector: 'app-document-page',
  imports: [TPipe, RouterLink, NgIcon, EmptyState, PipelineViewer, StatusBadge],
  providers: [provideIcons({ lucideArrowLeft })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="mx-auto max-w-5xl">
      @if (detail.hasValue()) {
        @let d = detail.value();
        <div class="mb-4 flex flex-wrap items-center gap-3">
          <a routerLink="/" class="btn-icon" [attr.aria-label]="'review.backHome' | t">
            <ng-icon name="lucideArrowLeft" />
          </a>
          <h1 class="truncate text-lg font-semibold">{{ d.filename }}</h1>
          <span class="text-sm text-muted-foreground">{{ 'doc.batch' | t }}: {{ d.batch_name }}</span>
          <span class="text-sm text-muted-foreground">· {{ 'doc.pages' | t: { n: d.pages.length } }}</span>
          <span class="flex-1"></span>
          <app-status-badge [status]="d.status" />
          @if (d.status === 'review' || d.status === 'done') {
            <a class="btn-outline" [routerLink]="['/review/file', d.slug]">{{ 'doc.annotate' | t }}</a>
          }
        </div>

        <section class="card p-4">
          <app-pipeline-viewer [slug]="d.slug" [pages]="d.pages" [rows]="rows.hasValue() ? rows.value() : null"
                               [(page)]="page" [(step)]="step" />
        </section>
      } @else if (detail.error()) {
        <app-empty-state icon="lucideTriangleAlert" [title]="'doc.notFound' | t">
          <a routerLink="/" class="btn-outline">{{ 'review.backHome' | t }}</a>
        </app-empty-state>
      } @else {
        <p class="text-sm text-muted-foreground">{{ 'common.loading' | t }}</p>
      }
    </div>
  `,
})
export class DocumentPage {
  private api = inject(Api);

  readonly docSlug = input.required<string>();
  protected detail = httpResource<DocDetail>(() => this.api.docUrl(this.docSlug()));
  protected readonly page = linkedSignal(() => (this.docSlug() ? 1 : 1));
  protected readonly step = signal<ViewerStep>('processed');
  protected rows = httpResource<PageRows>(() => this.api.rowsUrl(this.docSlug(), this.page()));
}
