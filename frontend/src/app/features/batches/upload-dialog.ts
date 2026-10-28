import { ChangeDetectionStrategy, Component, computed, inject, output, signal } from '@angular/core';
import { HttpErrorResponse, HttpEventType } from '@angular/common/http';
import { NgIcon, provideIcons } from '@ng-icons/core';
import { lucideFolderOpen, lucideUpload, lucideX } from '@ng-icons/lucide';

import { Api } from '../../core/api/api.service';
import { DuplicateFiles } from '../../core/api/api.types';
import { I18n, TPipe } from '../../core/i18n/i18n';
import { FileSizePipe } from '../../shared/ui/file-size.pipe';
import { Paginator } from '../../shared/ui/paginator';

const PREVIEW_PAGE = 10;

@Component({
  selector: 'app-upload-dialog',
  imports: [TPipe, NgIcon, FileSizePipe, Paginator],
  providers: [provideIcons({ lucideFolderOpen, lucideUpload, lucideX })],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(document:keydown.escape)': 'cancel()' },
  template: `
    <div class="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" (click)="cancel()">
      <div class="card w-full max-w-lg p-5 shadow-xl" (click)="$event.stopPropagation()" role="dialog" aria-modal="true">
        <div class="mb-4 flex items-center">
          <h2 class="text-lg font-semibold">{{ 'upload.title' | t }}</h2>
          <button class="btn-icon ml-auto" (click)="cancel()" [attr.aria-label]="'upload.cancel' | t"><ng-icon name="lucideX" /></button>
        </div>

        <label class="mb-1 block text-xs text-muted-foreground" for="batch-name">{{ 'upload.name' | t }}</label>
        <input id="batch-name" class="input mb-4" [value]="name()" (input)="name.set($any($event.target).value)" />

        <div class="rounded-lg border-2 border-dashed p-6 text-center text-sm transition-colors"
             [class]="over() ? 'border-primary bg-muted' : 'border-border'"
             (dragover)="$event.preventDefault(); over.set(true)" (dragleave)="over.set(false)"
             (drop)="$event.preventDefault(); over.set(false); add($event.dataTransfer?.files)">
          <ng-icon name="lucideUpload" size="1.5rem" class="mb-1 text-muted-foreground" />
          <p>{{ 'upload.drop' | t }}
            <button class="underline" (click)="fileInput.click()">{{ 'upload.select' | t }}</button></p>
          <input #fileInput type="file" multiple accept=".pdf,application/pdf" hidden
                 (change)="add(fileInput.files); fileInput.value = ''" />
        </div>
        <button class="btn-outline mt-2" (click)="dirInput.click()">
          <ng-icon name="lucideFolderOpen" /> {{ 'upload.folder' | t }}
        </button>
        <input #dirInput type="file" webkitdirectory hidden (change)="add(dirInput.files); dirInput.value = ''" />

        @if (skippedDupes().length) {
          <p class="mt-3 rounded-md bg-amber-100 p-2 text-xs text-amber-900 dark:bg-amber-900/40 dark:text-amber-100">
            {{ 'upload.duplicates' | t: { names: skippedDupes().join(', ') } }}</p>
        }

        <ul class="mt-3">
          @for (f of visible(); track f.name) {
            <li class="flex items-center gap-2 border-b border-border py-1 text-sm">
              <span class="min-w-0 flex-1 truncate">{{ f.name }}</span>
              <span class="text-xs text-muted-foreground">{{ f.size | fileSize }}</span>
              <button class="btn-icon size-6" (click)="remove(f)" [attr.aria-label]="'upload.remove' | t">
                <ng-icon name="lucideX" size="0.9rem" /></button>
            </li>
          } @empty {
            <li class="text-xs text-muted-foreground">{{ 'upload.noFiles' | t }}</li>
          }
        </ul>
        <app-paginator class="mt-2 block" [page]="page()" [size]="pageSize" [total]="files().length" (pageChange)="page.set($event)" />

        @if (error()) {
          <p class="mt-3 rounded-md bg-destructive/15 p-2 text-xs text-destructive">{{ error() }}</p>
        }

        <div class="mt-4 flex items-center justify-end gap-2">
          @if (progress() !== null) {
            <span class="mr-auto text-xs text-muted-foreground">{{ 'upload.uploading' | t: { pct: progress()! } }}</span>
          }
          <button class="btn-outline" (click)="cancel()">{{ 'upload.cancel' | t }}</button>
          <button class="btn-primary" [disabled]="!files().length || progress() !== null" (click)="send()">
            {{ 'upload.send' | t: { n: files().length } }}</button>
        </div>
      </div>
    </div>
  `,
})
export class UploadDialog {
  private api = inject(Api);
  private i18n = inject(I18n);

  closed = output<void>();
  created = output<void>();

  protected pageSize = PREVIEW_PAGE;
  protected name = signal(this.i18n.t('upload.defaultName', { date: new Date().toLocaleDateString(this.i18n.locale()) }));
  protected files = signal<File[]>([]);
  protected page = signal(1);
  protected over = signal(false);
  protected skippedDupes = signal<string[]>([]);
  protected progress = signal<number | null>(null);
  protected error = signal('');
  protected visible = computed(() => this.files().slice((this.page() - 1) * PREVIEW_PAGE, this.page() * PREVIEW_PAGE));

  protected add(list: FileList | null | undefined): void {
    if (!list) return;
    const known = new Set(this.files().map((f) => f.name));
    const dupes: string[] = [], ok: File[] = [];
    for (const f of Array.from(list)) {
      if (!/\.pdf$/i.test(f.name)) continue;
      if (known.has(f.name)) dupes.push(f.name);
      else { known.add(f.name); ok.push(f); }
    }
    this.skippedDupes.set(dupes);
    this.files.update((cur) => [...cur, ...ok].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })));
  }

  protected remove(file: File): void {
    this.files.update((cur) => cur.filter((f) => f !== file));
    const last = Math.max(1, Math.ceil(this.files().length / PREVIEW_PAGE));
    if (this.page() > last) this.page.set(last);
  }

  protected cancel(): void {
    if (this.progress() === null) this.closed.emit();
  }

  protected send(): void {
    this.error.set('');
    this.progress.set(0);
    this.api.createBatch(this.name().trim() || 'Batch', this.files()).subscribe({
      next: (ev) => {
        if (ev.type === HttpEventType.UploadProgress && ev.total) this.progress.set(Math.round((100 * ev.loaded) / ev.total));
        if (ev.type === HttpEventType.Response) this.created.emit();
      },
      error: (err: HttpErrorResponse) => {
        this.progress.set(null);
        const body = err.error as DuplicateFiles | { detail?: unknown } | null;
        if (err.status === 409 && body && 'filenames' in body) {
          this.error.set(this.i18n.t('upload.serverDuplicates', { names: body.filenames.join(', ') }));
        } else {
          const msg = typeof body?.detail === 'string' ? body.detail : err.statusText || String(err.status);
          this.error.set(this.i18n.t('upload.failed', { msg }));
        }
      },
    });
  }
}
