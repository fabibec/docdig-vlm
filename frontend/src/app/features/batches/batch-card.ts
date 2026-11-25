import { NgTemplateOutlet } from '@angular/common';
import { httpResource } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  inject,
  input,
  linkedSignal,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideCheck,
  lucideChevronDown,
  lucideChevronRight,
  lucideLoaderCircle,
  lucidePencil,
  lucideTrash2,
  lucideX,
} from '@ng-icons/lucide';
import { debounceTime, filter } from 'rxjs';

import { ZardAlertDialogService } from '@/shared/components/alert-dialog';
import { Api } from '../../core/api/api.service';
import type { BatchOut, DocOut, DocStatus, Paginated } from '../../core/api/api.types';
import { httpErrorMessage } from '../../core/api/http-error';
import { I18n, TPipe } from '../../core/i18n/i18n';
import { Realtime } from '../../core/realtime/realtime.service';
import { Paginator } from '../../shared/ui/paginator';
import { StatusBadge } from '../../shared/ui/status-badge';

const PAGE_SIZE = 10;
const STATUSES: DocStatus[] = ['review', 'processing', 'queued', 'done', 'error'];

@Component({
  selector: 'app-batch-card',
  imports: [RouterLink, NgTemplateOutlet, NgIcon, TPipe, StatusBadge, Paginator],
  providers: [
    provideIcons({
      lucideChevronDown,
      lucideChevronRight,
      lucideLoaderCircle,
      lucideTrash2,
      lucidePencil,
      lucideCheck,
      lucideX,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @let b = batch();
    <section class="card mb-3">
      <div class="flex flex-wrap items-center gap-3 p-3">
        <button class="btn-icon" (click)="expanded.set(!expanded())"
                [attr.aria-label]="(expanded() ? 'batches.hideFiles' : 'batches.showFiles') | t">
          <ng-icon [name]="expanded() ? 'lucideChevronDown' : 'lucideChevronRight'" />
        </button>

        @if (editingBatch()) {
          <form class="inline-flex items-center gap-1" (submit)="saveBatchRename($event)">
            <input class="input h-7 w-48 px-2 py-0 text-sm font-semibold"
                   [value]="batchNameInput()"
                   (input)="batchNameInput.set($any($event.target).value)"
                   (keydown.escape)="cancelBatchRename()" />
            <button type="submit" class="btn-icon size-6 text-green-600 hover:text-green-700"
                    [attr.aria-label]="'batches.save' | t" [attr.title]="'batches.save' | t">
              <ng-icon name="lucideCheck" size="0.85rem" />
            </button>
            <button type="button" class="btn-icon size-6 text-muted-foreground hover:text-foreground"
                    (click)="cancelBatchRename()"
                    [attr.aria-label]="'batches.cancel' | t" [attr.title]="'batches.cancel' | t">
              <ng-icon name="lucideX" size="0.85rem" />
            </button>
          </form>
        } @else {
          <div class="inline-flex items-center gap-1">
            <a [routerLink]="['/review/batch', b.slug]" class="font-semibold hover:underline"
               [attr.title]="'batches.review' | t">{{ currentBatchName() }}</a>
            <button type="button" class="btn-icon size-6 text-muted-foreground hover:text-foreground"
                    (click)="startBatchRename($event)"
                    [attr.aria-label]="'batches.rename' | t" [attr.title]="'batches.rename' | t">
              <ng-icon name="lucidePencil" size="0.85rem" />
            </button>
          </div>
        }

        <span class="text-sm text-muted-foreground">{{ created() }} · {{ (b.num_docs === 1 ? 'batches.fileOne' : 'batches.files') | t: { n: b.num_docs } }}</span>
        <span class="flex-1"></span>
        @for (s of statuses; track s) {
          @if (b.counts[s]) { <app-status-badge [status]="s" [count]="b.counts[s]" /> }
        }
        @if (b.open_flags) {
          <span class="whitespace-nowrap text-sm text-muted-foreground">{{ (b.open_flags === 1 ? 'batches.flagOne' : 'batches.flags') | t: { n: b.open_flags } }}</span>
        }

        <button type="button" class="btn-icon size-8 text-muted-foreground hover:text-destructive transition-colors"
                (click)="onDelete($event)"
                [attr.aria-label]="'batches.delete' | t" [attr.title]="'batches.delete' | t">
          <ng-icon name="lucideTrash2" size="0.95rem" />
        </button>
      </div>

      @if (actionError(); as msg) {
        <p role="alert" class="mx-3 mb-3 rounded-md bg-destructive/15 p-2 text-xs text-destructive">{{ msg }}</p>
      }

      @if (expanded()) {
        @if (docs.hasValue()) {
          @let d = docs.value();
          <ul>
            @for (doc of d.items; track doc.slug) {
              <li class="relative border-t border-border flex items-center gap-3 px-4 py-2 text-sm hover:bg-muted/50"
                  [class.cursor-pointer]="link(doc)">
                <ng-container *ngTemplateOutlet="row; context: { $implicit: doc }" />
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
      @if (editingDocSlug() === doc.slug) {
        <form class="inline-flex items-center gap-1 min-w-0 flex-1" (submit)="saveDocRename(doc, $event)">
          <input class="input h-7 px-2 py-0 text-sm min-w-0 flex-1"
                 [value]="docNameInput()"
                 (input)="docNameInput.set($any($event.target).value)"
                 (keydown.escape)="cancelDocRename()" />
          <button type="submit" class="btn-icon size-6 text-green-600 hover:text-green-700"
                  [attr.aria-label]="'batches.save' | t" [attr.title]="'batches.save' | t">
            <ng-icon name="lucideCheck" size="0.85rem" />
          </button>
          <button type="button" class="btn-icon size-6 text-muted-foreground hover:text-foreground"
                  (click)="cancelDocRename()"
                  [attr.aria-label]="'batches.cancel' | t" [attr.title]="'batches.cancel' | t">
            <ng-icon name="lucideX" size="0.85rem" />
          </button>
        </form>
      } @else {
        <div class="inline-flex items-center gap-1.5 min-w-0 flex-1">
          @if (link(doc); as target) {
            <!-- after: overlay stretches the link over the whole row; controls sit above it (relative z-10) -->
            <a [routerLink]="target" class="min-w-0 truncate font-medium hover:underline after:absolute after:inset-0 after:content-['']">
              {{ doc.filename }}
            </a>
          } @else {
            <span class="min-w-0 truncate text-muted-foreground" [attr.title]="doc.error">
              {{ doc.filename }}
            </span>
          }
          <button type="button" class="btn-icon relative z-10 size-6 text-muted-foreground hover:text-foreground shrink-0"
                  (click)="startDocRename(doc, $event)"
                  [attr.aria-label]="'batches.renameDoc' | t" [attr.title]="'batches.renameDoc' | t">
            <ng-icon name="lucidePencil" size="0.85rem" />
          </button>
        </div>
      }

      @if (rt.progress()[doc.slug]; as p) {
        <span class="flex items-center gap-1 text-xs text-muted-foreground">
          <ng-icon name="lucideLoaderCircle" class="animate-spin" />
          {{ 'step.' + p.step | t }} {{ p.page }}/{{ p.pages }}
        </span>
      }

      @if (doc.total_cost !== null && doc.total_cost !== undefined) {
        <span class="text-xs text-muted-foreground whitespace-nowrap" [attr.title]="'batches.docCost' | t">
          {{ formatCost(doc.total_cost, doc.num_pages) }}
        </span>
      }

      <app-status-badge [status]="doc.status" />
      <span class="min-w-32 whitespace-nowrap text-right text-muted-foreground">
        {{ doc.open_flags ? ((doc.open_flags === 1 ? 'batches.flagOne' : 'batches.flags') | t: { n: doc.open_flags }) : '–' }}
      </span>
    </ng-template>
  `,
})
export class BatchCard {
  private api = inject(Api);
  private i18n = inject(I18n);
  private alertDialog = inject(ZardAlertDialogService);
  protected rt = inject(Realtime);

  readonly batch = input.required<BatchOut>();
  readonly deleted = output<void>();

  protected size = PAGE_SIZE;
  protected statuses = STATUSES;
  protected readonly expanded = signal(false);
  protected readonly page = signal(1);
  /** Shows our own rename at once; resets to the input when the list reloads (e.g. a rename from another tab). */
  protected readonly currentBatchName = linkedSignal(() => this.batch().name);
  /** Last failed delete/rename, shown below the header until the next action. */
  protected readonly actionError = signal<string | null>(null);
  protected readonly editingBatch = signal(false);
  protected readonly batchNameInput = signal('');

  protected readonly editingDocSlug = signal<string | null>(null);
  protected readonly docNameInput = signal('');

  protected docs = httpResource<Paginated<DocOut>>(() =>
    this.expanded() ? this.api.batchDocsUrl(this.batch().slug, this.page(), PAGE_SIZE) : undefined,
  );
  protected readonly created = computed(() =>
    new Intl.DateTimeFormat(this.i18n.locale(), { dateStyle: 'medium', timeStyle: 'short' }).format(
      new Date(this.batch().created_at),
    ),
  );

  constructor() {
    inject(Realtime)
      .events$.pipe(
        filter(
          (e) =>
            e.type === 'doc.status' && e.doc.batch_slug === this.batch().slug && this.expanded(),
        ),
        debounceTime(300),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe(() => this.docs.reload());
  }

  formatCost(total: number, pages: number | null): string {
    // 2 decimals like "$0.02"; cheaper documents keep 4 so they don't all read "$0.00".
    const cost = total > 0 && total < 0.01 ? `$${total.toFixed(4)}` : `$${total.toFixed(2)}`;
    if (!pages) return cost;
    return `${cost} (${this.i18n.t(pages === 1 ? 'batches.pageOne' : 'batches.pages', { n: pages })})`;
  }

  protected onDelete(event: Event): void {
    event.stopPropagation();
    this.actionError.set(null);
    this.alertDialog.confirm({
      zTitle: this.i18n.t('batches.delete'),
      zDescription: this.i18n.t('batches.deleteConfirm'),
      zOkText: this.i18n.t('batches.delete'),
      zCancelText: this.i18n.t('batches.cancel'),
      zOkDestructive: true,
      zOnOk: () => {
        this.api.deleteBatch(this.batch().slug).subscribe({
          next: () => this.deleted.emit(),
          error: (err: unknown) =>
            this.actionError.set(
              this.i18n.t('batches.deleteFailed', { msg: httpErrorMessage(err) }),
            ),
        });
      },
    });
  }

  protected startBatchRename(event?: Event): void {
    event?.stopPropagation();
    this.batchNameInput.set(this.currentBatchName());
    this.actionError.set(null);
    this.editingBatch.set(true);
  }

  protected cancelBatchRename(): void {
    this.actionError.set(null);
    this.editingBatch.set(false);
  }

  protected saveBatchRename(event?: Event): void {
    event?.preventDefault();
    event?.stopPropagation();
    const newName = this.batchNameInput().trim();
    if (!newName || newName === this.currentBatchName()) {
      this.cancelBatchRename();
      return;
    }
    this.api.updateBatch(this.batch().slug, { name: newName }).subscribe({
      next: (updated) => {
        this.currentBatchName.set(updated.name);
        this.cancelBatchRename();
      },
      error: (err: unknown) =>
        this.actionError.set(this.i18n.t('batches.renameFailed', { msg: httpErrorMessage(err) })),
    });
  }

  protected startDocRename(doc: DocOut, event?: Event): void {
    event?.stopPropagation();
    this.editingDocSlug.set(doc.slug);
    this.docNameInput.set(doc.filename);
    this.actionError.set(null);
  }

  protected cancelDocRename(): void {
    this.actionError.set(null);
    this.editingDocSlug.set(null);
    this.docNameInput.set('');
  }

  protected saveDocRename(doc: DocOut, event?: Event): void {
    event?.preventDefault();
    event?.stopPropagation();
    let newName = this.docNameInput().trim();
    if (!newName) {
      this.cancelDocRename();
      return;
    }
    if (!/\.pdf$/i.test(newName)) {
      newName += '.pdf';
    }
    if (newName === doc.filename) {
      this.cancelDocRename();
      return;
    }
    this.api.updateDoc(doc.slug, { filename: newName }).subscribe({
      next: () => {
        this.cancelDocRename();
        this.docs.reload();
      },
      error: (err: unknown) =>
        this.actionError.set(this.i18n.t('batches.renameFailed', { msg: httpErrorMessage(err) })),
    });
  }

  /** Only finished docs are clickable: review -> review queue, done -> detail page. */
  protected link(doc: DocOut): string[] | null {
    if (doc.status === 'review') return ['/review/file', doc.slug];
    if (doc.status === 'done') return ['/', doc.slug];
    return null;
  }
}
