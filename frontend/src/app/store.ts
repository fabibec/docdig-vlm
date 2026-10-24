import { Injectable, signal, computed } from '@angular/core';

export type Status = 'queued' | 'processing' | 'review' | 'done';
export interface Doc { name: string; status: Status; flags: number }
export interface Batch { name: string; fromFolder?: boolean; docs: Doc[] }
export interface Flag { row: number; col: number; kind: 'bad' | 'warn'; msg: string; why: string; suggestion: string; done: boolean }

export const COLS = ['Ring no.', 'Species', 'Age', 'Sex', 'Wing mm', 'Weight g', 'Time'];

@Injectable({ providedIn: 'root' })
export class Store {
  cols = COLS;
  batches = signal<Batch[]>([
    { name: 'Mettnau 2026-10-18', docs: [
      { name: 'mettnau_p01.pdf', status: 'done', flags: 0 },
      { name: 'mettnau_p02.pdf', status: 'done', flags: 0 },
      { name: 'mettnau_p03.pdf', status: 'review', flags: 4 }] },
    { name: 'Ismaning 2026-10-21', docs: [
      { name: 'ismaning_p01.pdf', status: 'processing', flags: 0 },
      { name: 'ismaning_p02.pdf', status: 'queued', flags: 0 }] },
  ]);

  // Mock result of the OCR/table-extraction backend for one sheet.
  sheet = signal<string[][]>([
    ['HA 48201', 'Phylloscopus collybita', '4', 'F', '56', '7.8', '06:40'],
    ['HA 48202', 'Phylloscopus collybita', '4', 'M', '58', '8.1', '06:52'],
    ['HA 48203', 'Erithacus rubecula', '3', 'U', '71', '18.7', '07:05'],
    ['HA 48204', 'Sylvia atricapilla', '4', 'M', '73', '187', '07:20'],
    ['HA 48205', 'Parus major', '3', 'F', '69', '17.2', '07:31'],
    ['HA 48206', 'Sylvia atricapila?', '4', 'F', '72', '17.9', '07:48'],
    ['HA 4B207', 'Turdus merula', '4', 'M', '128', '96.0', '08:02'],
    ['HA 48208', 'Cyanistes caeruleus', '3', 'U', '64', '10.9', '08:15'],
  ]);

  flags = signal<Flag[]>([
    { row: 3, col: 5, kind: 'bad', msg: 'Weight out of range for Sylvia atricapilla (usual 14–25 g)', why: 'Value 187 is implausible. Likely a missed decimal point.', suggestion: '18.7', done: false },
    { row: 6, col: 0, kind: 'warn', msg: 'Ring number: character unclear', why: 'Character 4 read as "B", could be "8". Ring numbers are sequential.', suggestion: 'HA 48207', done: false },
    { row: 5, col: 1, kind: 'warn', msg: 'Species not in reference list', why: 'Trailing question mark and spelling variant.', suggestion: 'Sylvia atricapilla', done: false },
    { row: 7, col: 6, kind: 'warn', msg: 'Low confidence on time', why: 'Digit 1 may be a 7.', suggestion: '08:15', done: false },
  ]);

  selected = signal(0);
  current = computed(() => this.flags()[this.selected()]);
  pending = computed(() => this.flags().filter(f => !f.done).length);

  setCell(r: number, c: number, v: string) {
    this.sheet.update(s => s.map((row, i) => i === r ? row.map((x, j) => j === c ? v : x) : row));
  }
  resolveCurrent() {
    this.flags.update(f => f.map((x, i) => i === this.selected() ? { ...x, done: true } : x));
    const next = this.flags().findIndex(f => !f.done);
    if (next >= 0) this.selected.set(next);
  }
  isFlagged(r: number, c: number) { return this.flags().findIndex(f => !f.done && f.row === r && f.col === c); }

  /** Fake async processing. Replace with HTTP calls / websocket updates. */
  addBatch(name: string, files: string[], fromFolder = false) {
    const docs: Doc[] = files.map(n => ({ name: n, status: 'queued', flags: 0 }));
    this.batches.update(b => [{ name, fromFolder, docs }, ...b]);
    let i = 0;
    const t = setInterval(() => {
      this.batches.update(all => all.map(b => b.docs === docs ? b : b)); // keep reference
      if (i > 0) docs[i - 1].status = 'done';
      if (i < docs.length) docs[i].status = 'processing';
      this.batches.update(b => [...b]);
      if (++i > docs.length) clearInterval(t);
    }, 1400);
  }

  markdown() {
    const rows = [this.cols, ...this.sheet()];
    const w = this.cols.map((_, i) => Math.max(...rows.map(r => r[i].length)));
    const line = (r: string[]) => '| ' + r.map((v, i) => v.padEnd(w[i])).join(' | ') + ' |';
    return [line(rows[0]), '|' + w.map(x => '-'.repeat(x + 2)).join('|') + '|', ...rows.slice(1).map(line)].join('\n');
  }
  html() {
    return '<table>\n  <thead><tr>' + this.cols.map(c => `<th>${c}</th>`).join('') + '</tr></thead>\n  <tbody>\n' +
      this.sheet().map(r => '    <tr>' + r.map(v => `<td>${v}</td>`).join('') + '</tr>').join('\n') + '\n  </tbody>\n</table>';
  }
}
