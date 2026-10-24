import { Component, inject, signal } from '@angular/core';
import { Store } from './store';

type Step = 'orig' | 'cut' | 'res';
type Mode = 'render' | 'md' | 'html';

@Component({
  selector: 'app-pipeline',
  template: `
    <div class="mb-3 flex flex-wrap items-center gap-3"><b>mettnau_p03.pdf</b>
      <div class="inline-flex rounded-full border border-border bg-muted p-0.5">
        @for (st of steps; track st.id) {
          <button class="rounded-full px-3 py-1.5 text-sm" [class]="step() === st.id ? 'bg-primary font-medium text-primary-foreground' : 'text-muted-foreground'"
                  (click)="step.set(st.id)">{{ st.label }}</button>
        }
      </div>
    </div>

    @if (step() !== 'res') {
      <!-- Page image. In the real app: <img> of the rendered PDF page with an SVG overlay for the detected table box. -->
      <div class="relative overflow-hidden rounded border border-border bg-stone-50 p-5 font-[cursive] text-indigo-900 dark:bg-stone-800 dark:text-indigo-200">
        <div class="text-2xl font-bold">Beringungsliste</div>
        <div>Station: Mettnau · Datum: 18.10.2026 · Beringer: J. Meier</div>
        <div class="relative my-8 bg-stone-50 p-1 dark:bg-stone-800"
             [class]="step() === 'cut' ? 'z-10 outline-2 outline-dashed outline-primary shadow-[0_0_0_2000px_rgba(20,28,25,.4)]' : ''">
          @if (step() === 'cut') { <span class="absolute -top-7 left-0 rounded bg-primary px-2 py-0.5 font-sans text-xs text-primary-foreground">Table 1 · 98% confidence</span> }
          <table class="w-full text-lg">
            <tr class="font-sans text-xs text-muted-foreground">@for (c of s.cols; track c) { <th class="p-1 text-left font-normal">{{ c }}</th> }</tr>
            @for (r of s.sheet(); track $index) {
              <tr [class]="$index === s.current().row ? 'bg-yellow-200/70 outline outline-2 outline-amber-600' : ''">
                @for (v of r; track $index) { <td class="border-b border-slate-400 px-1">{{ v }}</td> }</tr>
            }
          </table>
        </div>
        <div>Bemerkungen: Nebel bis 08:00, Netz 4 geschlossen. – J. Meier</div>
      </div>
      @if (step() === 'cut') {
        <div class="mt-3 flex gap-2"><button class="rounded-md border border-border px-3 py-1.5 text-sm">Adjust box</button>
          <button class="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground" (click)="step.set('res')">Extract table</button></div>
      }
    } @else {
      <div class="mb-3 flex flex-wrap gap-1">
        @for (m of modes; track m.id) {
          <button class="rounded-md px-3 py-1 text-sm" [class]="mode() === m.id ? 'bg-muted font-semibold' : 'text-muted-foreground'" (click)="mode.set(m.id)">{{ m.label }}</button>
        }
        <span class="flex-1"></span>
        @if (mode() !== 'render') { <button class="rounded-md border border-border px-3 py-1 text-sm" (click)="copy()">Copy</button> }
      </div>
      @if (mode() === 'render') {
        <div class="overflow-auto"><table class="w-full min-w-[520px] text-sm">
          <thead><tr class="border-b-2 border-border bg-muted">@for (c of s.cols; track c) { <th class="p-2 text-left">{{ c }}</th> }</tr></thead>
          <tbody>@for (r of s.sheet(); track $index) { @let ri = $index;
            <tr class="border-b border-border">@for (v of r; track $index) {
              @if (s.isFlagged(ri, $index) >= 0) {
                <td class="cursor-pointer bg-amber-100 p-2 shadow-[inset_0_-2px_#b9770e]" (click)="s.selected.set(s.isFlagged(ri, $index))">{{ v }}</td>
              } @else { <td class="p-2">{{ v }}</td> } }</tr> }</tbody></table></div>
      } @else {
        <pre class="max-h-[470px] overflow-auto rounded-md border border-border bg-muted p-3 text-xs">{{ mode() === 'md' ? s.markdown() : s.html() }}</pre>
      }
    }`,
})
export class Pipeline {
  s = inject(Store);
  step = signal<Step>('orig');
  mode = signal<Mode>('render');
  steps = [{ id: 'orig', label: '1  Original' }, { id: 'cut', label: '2  Table detection' }, { id: 'res', label: '3  Result' }] as const;
  modes = [{ id: 'render', label: 'Rendered' }, { id: 'md', label: 'Markdown' }, { id: 'html', label: 'HTML' }] as const;
  copy() { navigator.clipboard?.writeText(this.mode() === 'md' ? this.s.markdown() : this.s.html()); }
}
