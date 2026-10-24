import { Component, inject, output, signal } from '@angular/core';
import { Store } from './store';

@Component({
  selector: 'app-new-batch-dialog',
  template: `
    <div class="fixed inset-0 z-20 flex items-center justify-center bg-black/50 p-4" (click)="close.emit()">
      <div class="w-full max-w-lg rounded-lg border border-border bg-card p-5" (click)="$event.stopPropagation()">
        <h3 class="text-base font-semibold">New batch</h3>
        <label class="mt-3 block text-xs text-muted-foreground">Batch name</label>
        <input class="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm" [value]="name()" (input)="name.set($any($event.target).value)">

        <div class="mt-4 cursor-pointer rounded-lg border-2 border-dashed p-6 text-center text-sm text-muted-foreground"
             [class]="over() ? 'border-primary bg-muted' : 'border-border'"
             (click)="fi.click()" (dragover)="$event.preventDefault(); over.set(true)" (dragleave)="over.set(false)"
             (drop)="$event.preventDefault(); over.set(false); add($any($event).dataTransfer.files)">
          Drag PDF pages here or <u>select files</u>
          <input #fi type="file" multiple accept=".pdf,image/*" hidden (change)="add($any($event.target).files)">
        </div>
        <button class="mt-2 rounded-md border border-border px-3 py-1.5 text-sm" (click)="fd.click()">Select a whole folder</button>
        <input #fd type="file" webkitdirectory hidden (change)="add($any($event.target).files)">

        <ul class="mt-3">
          @for (f of files(); track $index) {
            <li class="flex items-center gap-2 border-b border-border py-1 text-sm">
              <span class="flex-1 truncate">{{ f.name }}</span>
              <span class="text-xs text-muted-foreground">{{ (f.size / 1048576).toFixed(1) }} MB</span>
              <button class="px-2" (click)="remove($index)" aria-label="Remove">×</button></li>
          } @empty { <li class="text-xs text-muted-foreground">No files added yet</li> }
        </ul>
        <div class="mt-4 flex justify-end gap-2">
          <button class="rounded-md border border-border px-3 py-1.5 text-sm" (click)="close.emit()">Cancel</button>
          <button class="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
                  [disabled]="!files().length" (click)="send()">Send {{ files().length }} file{{ files().length === 1 ? '' : 's' }}</button>
        </div>
      </div>
    </div>`,
})
export class NewBatchDialog {
  store = inject(Store);
  close = output<void>();
  name = signal('Batch ' + new Date().toLocaleDateString('de-DE'));
  files = signal<File[]>([]);
  over = signal(false);

  add(list: FileList) {
    const ok = [...list].filter(f => /\.(pdf|jpe?g|png|tiff?)$/i.test(f.name));
    this.files.update(cur => [...cur, ...ok].sort((a, b) => a.name.localeCompare(b.name)));
  }
  remove(i: number) { this.files.update(f => f.filter((_, j) => j !== i)); }
  send() {
    // Real app: upload files (multipart) to the API, then show the batch id it returns.
    this.store.addBatch(this.name().trim() || 'Untitled batch', this.files().map(f => f.name));
    this.close.emit();
  }
}
