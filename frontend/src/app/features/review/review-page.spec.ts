import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { signal } from '@angular/core';
import { Subject } from 'rxjs';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { ReviewPage } from './review-page';
import {
  ColumnKey, COLUMNS, DocDetail, DocOut, FlagCreate, FlagOut, PageRows, Progress, ReviewQueue, RowOut, ServerEvent,
} from '../../core/api/api.types';
import { I18n } from '../../core/i18n/i18n';
import { Realtime } from '../../core/realtime/realtime.service';

class MockRealtime {
  readonly events$ = new Subject<ServerEvent>();
  readonly pending = signal({ files_left: 0, flags_left: 0 });
  readonly connected = signal(true);
  readonly progress = signal<Record<string, Progress>>({});
}

type Values = Partial<Record<ColumnKey, string>>;

function flag(over: Partial<FlagOut>): FlagOut {
  return {
    id: 0, page: 1, row: 1, column: 'species', source: 'llm', severity: 'warning', code: null, params: {},
    message: 'unclear', suggestion: null, confidence: null, resolved: false, ...over,
  };
}

function row(idx: number, values: Values): RowOut {
  const empty = Object.fromEntries(COLUMNS.map((c) => [c, ''])) as Record<ColumnKey, string>;
  return { idx, y0: (idx - 1) * 10, y1: idx * 10, values: { ...empty, ...values }, corrected: [] };
}

interface Write { method: string; url: string; body: unknown }

/**
 * In-memory backend behind HttpTestingController: answers the reads from its state
 * and applies the writes (resolve, notes, reopen) like the real API does.
 */
class FakeBackend {
  docs: Record<string, { filename: string; pages: number; flags: FlagOut[]; rows: Record<number, RowOut[]> }> = {};
  reads: string[] = [];
  writes: Write[] = [];
  failWrites = false;
  private nextId = 100;

  open(slug: string) { return this.docs[slug].flags.filter((f) => !f.resolved); }

  docOut(slug: string): DocOut {
    const d = this.docs[slug], open = this.open(slug).length;
    return {
      slug, batch_slug: 'b1', filename: d.filename, status: open ? 'review' : 'done', num_pages: d.pages,
      open_flags: open, error: null, updated_at: '',
    };
  }

  queue(doc?: string): ReviewQueue {
    const slugs = doc ? [doc] : Object.keys(this.docs).filter((s) => this.open(s).length);
    const docs = slugs.map((s) => ({ slug: s, batch_slug: 'b1', filename: this.docs[s].filename, open_flags: this.open(s).length }));
    return {
      scope: doc ? 'doc' : 'all', docs,
      files_left: docs.filter((d) => d.open_flags).length, flags_left: docs.reduce((n, d) => n + d.open_flags, 0),
    };
  }

  detail(slug: string): DocDetail {
    const d = this.docs[slug];
    const pages = Array.from({ length: d.pages }, (_, i) => ({
      num: i + 1, open_flags: this.open(slug).filter((f) => f.page === i + 1).length, page_confidence: null,
    }));
    return { ...this.docOut(slug), batch_name: 'Batch 1', pages, artifact_url: '' };
  }

  rows(slug: string, page: number): PageRows {
    const column_x = Object.fromEntries(COLUMNS.map((c, i) => [c, [i * 10, i * 10 + 10]])) as Record<ColumnKey, [number, number]>;
    const rows = this.docs[slug].rows[page] ?? [];
    return { num: page, width: 90, height: rows.length * 10, columns: COLUMNS, column_x, rows };
  }

  private applyValues(slug: string, page: number, idx: number, values: Values) {
    const r = this.docs[slug].rows[page]?.find((x) => x.idx === idx);
    if (r) r.values = { ...r.values, ...values };
  }

  private resolve(slug: string, match: (f: FlagOut) => boolean) {
    const ids: number[] = [];
    for (const f of this.open(slug)) if (match(f)) { f.resolved = true; ids.push(f.id); }
    return { doc: this.docOut(slug), resolved_flag_ids: ids };
  }

  handle(req: TestRequest): void {
    const { method, body } = req.request;
    const url = req.request.urlWithParams;
    let m: RegExpMatchArray | null;
    if (method === 'GET') this.reads.push(url);
    else {
      this.writes.push({ method, url, body });
      if (this.failWrites) {
        req.flush({ detail: 'nope' }, { status: 500, statusText: 'Server Error' });
        return;
      }
    }
    if (method === 'GET' && (m = url.match(/^\/api\/review(?:\?(doc|batch)=(.+))?$/))) {
      return req.flush(this.queue(m[1] === 'doc' ? m[2] : undefined));
    }
    if (method === 'GET' && (m = url.match(/^\/api\/docs\/([^/]+)$/))) return req.flush(this.detail(m[1]));
    if (method === 'GET' && (m = url.match(/^\/api\/docs\/([^/]+)\/flags$/))) return req.flush(this.docs[m[1]].flags.map((f) => ({ ...f })));
    if (method === 'GET' && (m = url.match(/^\/api\/docs\/([^/]+)\/page\/(\d+)\/rows$/))) return req.flush(this.rows(m[1], +m[2]));
    if (method === 'POST' && (m = url.match(/^\/api\/docs\/([^/]+)\/page\/(\d+)\/rows\/(\d+)\/resolve$/))) {
      const [slug, page, idx] = [m[1], +m[2], +m[3]];
      this.applyValues(slug, page, idx, (body as { values: Values }).values);
      return req.flush(this.resolve(slug, (f) => f.page === page && f.row === idx));
    }
    if (method === 'POST' && (m = url.match(/^\/api\/docs\/([^/]+)\/page\/(\d+)\/resolve$/))) {
      const [slug, page] = [m[1], +m[2]];
      for (const [idx, values] of Object.entries((body as { rows: Record<number, Values> }).rows)) this.applyValues(slug, page, +idx, values);
      return req.flush(this.resolve(slug, (f) => f.page === page));
    }
    if (method === 'POST' && (m = url.match(/^\/api\/docs\/([^/]+)\/flags\/reopen$/))) {
      const ids = (body as { ids: number[] }).ids;
      for (const f of this.docs[m[1]].flags) if (ids.includes(f.id)) f.resolved = false;
      return req.flush({ doc: this.docOut(m[1]), flags: this.docs[m[1]].flags });
    }
    if (method === 'POST' && (m = url.match(/^\/api\/docs\/([^/]+)\/flags$/))) {
      const b = body as FlagCreate;
      this.docs[m[1]].flags.push(flag({ id: this.nextId++, page: b.page, row: b.row, column: b.column, source: 'user', message: b.message }));
      this.docs[m[1]].flags.sort((a, c) => a.page - c.page || a.row - c.row);
      return req.flush({ doc: this.docOut(m[1]), flags: this.docs[m[1]].flags });
    }
    if (method === 'DELETE' && (m = url.match(/^\/api\/docs\/([^/]+)\/flags\/(\d+)$/))) {
      this.docs[m[1]].flags = this.docs[m[1]].flags.filter((f) => f.id !== +m![2]);
      return req.flush({ doc: this.docOut(m[1]), flags: this.docs[m[1]].flags });
    }
    throw new Error(`unexpected request ${method} ${url}`);
  }
}

describe('ReviewPage', () => {
  let fixture: ComponentFixture<ReviewPage>;
  let http: HttpTestingController;
  let rt: MockRealtime;
  let server: FakeBackend;
  let t: I18n['t'];

  const el = () => fixture.nativeElement as HTMLElement;
  const text = () => el().textContent!.replace(/\s+/g, ' ');
  const buttonByText = (label: string, root: ParentNode = el()) =>
    [...root.querySelectorAll('button')].find((b) => b.textContent?.trim() === label);
  const recordsSection = () => el().querySelector('section')!;
  const recordButtons = () => [...recordsSection().querySelectorAll<HTMLButtonElement>('button')];
  const recordButton = (rowNo: number) => recordButtons().find((b) => b.querySelector('span')?.textContent === `#${rowNo}`)!;
  const editor = () => el().querySelector('app-record-editor')!;
  const editorHeading = () => editor().querySelector('.text-sm.text-muted-foreground')!.textContent!.replace(/\s+/g, ' ').trim();
  /** The file picker in the header (the viewer has its own page select). */
  const fileSelect = () => [...el().querySelectorAll('label')]
    .find((l) => l.querySelector('span')?.textContent?.trim() === t('review.file'))?.querySelector('select') ?? null;
  const fileOptions = () => [...fileSelect()!.options];
  const field = (c: ColumnKey) => editor().querySelector<HTMLInputElement>(`#field-${c}`)!;

  /** Answers every pending request (and the requests they trigger) until the page is idle. */
  async function settle() {
    // whenStable() waits for pending HTTP requests, so answer them first.
    for (let i = 0; i < 50; i++) {
      fixture.detectChanges();
      const pending = http.match(() => true);
      if (pending.length) {
        for (const req of pending) server.handle(req);
        await Promise.resolve();
        continue;
      }
      await fixture.whenStable();
      fixture.detectChanges();
      const late = http.match(() => true); // match() removes them, so answer them right away
      if (!late.length) return;
      for (const req of late) server.handle(req);
    }
    throw new Error('page did not settle');
  }

  async function start(inputs: { docSlug?: string; batchSlug?: string } = {}) {
    fixture = TestBed.createComponent(ReviewPage);
    for (const [k, v] of Object.entries(inputs)) fixture.componentRef.setInput(k, v);
    await settle();
  }

  function type(input: HTMLInputElement, value: string) {
    input.value = value;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  }

  async function save() {
    buttonByText(t('review.save'), editor())!.click();
    await settle();
  }

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [ReviewPage],
      providers: [
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: Realtime, useClass: MockRealtime },
      ],
    });
    const i18n = TestBed.inject(I18n);
    i18n.lang.set('en');
    t = i18n.t.bind(i18n);
    http = TestBed.inject(HttpTestingController);
    rt = TestBed.inject(Realtime) as unknown as MockRealtime;

    server = new FakeBackend();
    server.docs = {
      d1: {
        filename: 'scan1.pdf',
        pages: 2,
        flags: [
          flag({ id: 1, page: 1, row: 2, column: 'species', message: 'Hard to read', suggestion: 'Amsel', params: { value: 'Amsl' }, confidence: 0.4 }),
          flag({ id: 2, page: 1, row: 2, column: 'weight_g', source: 'rule', severity: 'error', code: 'weight_out_of_range', params: { value: '80', min: 5, max: 40 } }),
          flag({ id: 3, page: 1, row: 5, column: null, source: 'user', message: 'check row' }),
          flag({ id: 4, page: 2, row: 1, column: 'sex', message: 'sex?' }),
        ],
        rows: {
          1: [1, 2, 3, 4, 5].map((i) => row(i, { species: `Sp${i}`, ...(i === 2 ? { species: 'Amsl', weight_g: '80' } : {}) })),
          2: [row(1, { species: 'Kohlmeise', sex: 'X' })],
        },
      },
      d2: {
        filename: 'scan2.pdf',
        pages: 1,
        flags: [flag({ id: 5, page: 1, row: 3, column: 'age', message: 'age?' })],
        rows: { 1: [1, 2, 3].map((i) => row(i, { species: `Other${i}` })) },
      },
    };
  });

  afterEach(() => {
    http.verify();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  describe('header', () => {
    it('offers a file selector with the open flags per file, plus files and flags left', async () => {
      await start();
      const options = fileOptions().map((o) => o.textContent?.trim());
      expect(options).toEqual(['scan1.pdf (4)', 'scan2.pdf (1)']);
      expect(text()).toContain(`2 ${t('review.filesLeft')}`);
      expect(text()).toContain(`5 ${t('review.flagsLeft')}`);
    });

    it('uses the singular texts for one file / one flag', async () => {
      delete server.docs['d1'];
      await start();
      expect(text()).toContain(`1 ${t('review.fileLeftOne')}`);
      expect(text()).toContain(`1 ${t('review.flagLeftOne')}`);
    });

    it('switches the file with the selector', async () => {
      await start();
      const select = fileSelect()!;
      select.value = 'd2';
      select.dispatchEvent(new Event('change'));
      await settle();
      expect(editorHeading()).toBe(`${t('review.page', { n: 1 })} · ${t('review.row', { n: 3 })}`);
    });

    it('limits the queue to one batch', async () => {
      await start({ batchSlug: 'b1' });
      expect(server.reads[0]).toBe('/api/review?batch=b1');
      expect(fileSelect()).toBeTruthy();
    });

    it('shows the filename instead of a selector in file scope', async () => {
      await start({ docSlug: 'd2' });
      expect(el().querySelector('h1')!.textContent).toBe('scan2.pdf');
      expect(server.reads[0]).toBe('/api/review?doc=d2');
      expect(fileSelect()).toBeNull();
    });
  });

  describe('records list', () => {
    it('groups the open flags by (page, row) and lists them per page', async () => {
      await start();
      const headings = [...recordsSection().querySelectorAll('h3')].map((h) => h.textContent?.trim());
      expect(headings).toEqual([t('review.page', { n: 1 }), t('review.page', { n: 2 })]);
      expect(recordButtons().map((b) => b.querySelector('span')!.textContent)).toEqual(['#2', '#5', '#1']);
    });

    it('shows species, a summary, a count only for several flags and a dot for notes', async () => {
      await start();
      const r2 = recordButton(2), r5 = recordButton(5);
      expect(r2.textContent).toContain('Amsl');
      expect(r2.querySelector('p')!.textContent).toBe(`Hard to read · ${t('flag.weight_out_of_range', { value: '80', min: 5, max: 40 })}`);
      expect(r2.querySelector('.rounded-full.bg-amber-100')!.textContent?.trim()).toBe('2');
      expect(r2.querySelector('.bg-sky-500')).toBeNull();
      expect(r5.querySelector('.rounded-full.bg-amber-100')).toBeNull();
      expect(r5.querySelector('.bg-sky-500')!.getAttribute('title')).toBe(t('source.user'));
    });

    it('selects the first record and highlights it', async () => {
      await start();
      expect(editorHeading()).toBe(`${t('review.page', { n: 1 })} · ${t('review.row', { n: 2 })}`);
      expect(recordButton(2).className).toContain('border-primary');
      expect(field('species').value).toBe('Amsl');
    });

    it('opens a record on click and jumps to its page', async () => {
      await start();
      recordButton(1).click();
      await settle();
      expect(editorHeading()).toBe(`${t('review.page', { n: 2 })} · ${t('review.row', { n: 1 })}`);
      expect(field('sex').value).toBe('X');
    });

    it('shows a green check when nothing is open', async () => {
      for (const f of server.docs['d1'].flags) f.resolved = true;
      await start({ docSlug: 'd1' });
      expect(recordsSection().querySelector('ng-icon[name="lucideCircleCheck"]')).toBeTruthy();
      expect(recordsSection().textContent).toContain(t('review.doneTitle'));
    });

    it('shows the done state when the queue is empty', async () => {
      server.docs = {};
      await start();
      expect(el().querySelector('app-empty-state')!.textContent).toContain(t('review.doneTitle'));
    });
  });

  describe('Save & next', () => {
    it('posts the changed cells to /rows/{idx}/resolve and moves to the next record', async () => {
      await start();
      type(field('weight_g'), '8.0');
      await save();

      expect(server.writes).toEqual([
        { method: 'POST', url: '/api/docs/d1/page/1/rows/2/resolve', body: { values: { weight_g: '8.0' } } },
      ]);
      expect(editorHeading()).toBe(`${t('review.page', { n: 1 })} · ${t('review.row', { n: 5 })}`);
      expect(recordButtons().map((b) => b.querySelector('span')!.textContent)).toEqual(['#5', '#1']);
      expect(text()).toContain(t('review.saved', { n: 2 }));
    });

    it('sends no values when nothing was changed', async () => {
      await start();
      await save();
      expect(server.writes[0].body).toEqual({ values: {} });
    });

    it('the AI suggestion fills the field and is saved', async () => {
      await start();
      buttonByText(`${t('review.suggestion')} „Amsel“`, editor())!.click();
      fixture.detectChanges();
      expect(field('species').value).toBe('Amsel');
      await save();
      expect(server.writes[0].body).toEqual({ values: { species: 'Amsel' } });
    });

    it('continues with the next file after the last record of a file', async () => {
      await start();
      await save(); // p1 r2
      await save(); // p1 r5
      expect(editorHeading()).toBe(`${t('review.page', { n: 2 })} · ${t('review.row', { n: 1 })}`);
      await save(); // p2 r1 = last record of d1

      expect(server.writes.map((w) => w.url)).toEqual([
        '/api/docs/d1/page/1/rows/2/resolve', '/api/docs/d1/page/1/rows/5/resolve', '/api/docs/d1/page/2/rows/1/resolve',
      ]);
      expect(fileOptions().map((o) => o.value)).toEqual(['d2']);
      expect(editorHeading()).toBe(`${t('review.page', { n: 1 })} · ${t('review.row', { n: 3 })}`);
      expect(text()).toContain(`1 ${t('review.fileLeftOne')}`);
    });

    it('shows an error and stays on the record when saving fails', async () => {
      await start();
      server.failWrites = true;
      await save();
      expect(editor().textContent).toContain(t('review.saveFailed'));
      expect(editorHeading()).toBe(`${t('review.page', { n: 1 })} · ${t('review.row', { n: 2 })}`);
      expect(text()).not.toContain(t('review.undo'));
    });
  });

  describe('Skip', () => {
    it('moves to the next record without saving and wraps around', async () => {
      await start();
      type(field('species'), 'discarded');
      buttonByText(t('review.skip'), editor())!.click();
      await settle();
      expect(editorHeading()).toBe(`${t('review.page', { n: 1 })} · ${t('review.row', { n: 5 })}`);

      buttonByText(t('review.skip'), editor())!.click();
      await settle();
      buttonByText(t('review.skip'), editor())!.click();
      await settle();
      expect(editorHeading()).toBe(`${t('review.page', { n: 1 })} · ${t('review.row', { n: 2 })}`);
      expect(field('species').value).toBe('Amsl');
      expect(server.writes).toEqual([]);
    });
  });

  describe('Undo', () => {
    it('writes the previous values back, reopens the resolved flags and reselects the row', async () => {
      await start();
      type(field('species'), 'Amsel');
      await save();
      server.writes = [];

      buttonByText(t('review.undo'))!.click();
      await settle();

      const before = server.writes[0].body as { values: Values };
      expect(server.writes[0].url).toBe('/api/docs/d1/page/1/rows/2/resolve');
      expect(before.values.species).toBe('Amsl');
      expect(before.values.weight_g).toBe('80');
      expect(Object.keys(before.values).sort()).toEqual([...COLUMNS].sort());
      expect(server.writes[1]).toEqual({ method: 'POST', url: '/api/docs/d1/flags/reopen', body: { ids: [1, 2] } });
      expect(server.writes.length).toBe(2);

      expect(editorHeading()).toBe(`${t('review.page', { n: 1 })} · ${t('review.row', { n: 2 })}`);
      expect(field('species').value).toBe('Amsl');
      expect(recordButtons().map((b) => b.querySelector('span')!.textContent)).toEqual(['#2', '#5', '#1']);
      expect(text()).not.toContain(t('review.undo'));
    });

    it('is offered for 10 s only', async () => {
      const timeouts = vi.spyOn(globalThis, 'setTimeout');
      await start();
      await save();
      expect(text()).toContain(t('review.undo'));

      const call = timeouts.mock.calls.find(([, ms]) => ms === 10_000);
      expect(call).toBeDefined();
      (call![0] as () => void)();
      fixture.detectChanges();
      expect(text()).not.toContain(t('review.undo'));
    });
  });

  describe('notes', () => {
    it('Add note creates a reviewer flag on the selected row', async () => {
      await start();
      buttonByText(t('review.addNote'), editor())!.click();
      fixture.detectChanges();
      const select = editor().querySelector<HTMLSelectElement>('form select')!;
      select.value = 'ring_no';
      select.dispatchEvent(new Event('change'));
      const textarea = editor().querySelector<HTMLTextAreaElement>('textarea[placeholder]')!;
      type(textarea as unknown as HTMLInputElement, 'Ring looks off');
      editor().querySelector('form')!.dispatchEvent(new Event('submit'));
      await settle();

      expect(server.writes).toEqual([{
        method: 'POST', url: '/api/docs/d1/flags', body: { page: 1, row: 2, column: 'ring_no', message: 'Ring looks off' },
      }]);
      expect(recordButton(2).querySelector('.bg-sky-500')).toBeTruthy();
      expect(editor().textContent).toContain('Ring looks off');
    });

    it('Delete note removes the note and keeps the row selected', async () => {
      await start();
      recordButton(5).click();
      await settle();
      editor().querySelector<HTMLButtonElement>(`button[aria-label="${t('review.deleteNote')}"]`)!.click();
      await settle();

      expect(server.writes).toEqual([{ method: 'DELETE', url: '/api/docs/d1/flags/3', body: null }]);
      expect(editorHeading()).toBe(`${t('review.page', { n: 1 })} · ${t('review.row', { n: 5 })}`);
      expect(editor().textContent).toContain(t('review.noFlags'));
      expect(recordButtons().map((b) => b.querySelector('span')!.textContent)).toEqual(['#2', '#1']);
    });

    it('shows a notice when an action fails', async () => {
      await start();
      recordButton(5).click();
      await settle();
      server.failWrites = true;
      editor().querySelector<HTMLButtonElement>(`button[aria-label="${t('review.deleteNote')}"]`)!.click();
      await settle();
      expect(text()).toContain(t('review.actionFailed'));
    });
  });

  describe('viewer', () => {
    it('opens any row clicked in the result table, even without flags', async () => {
      await start();
      const viewer = el().querySelector('app-pipeline-viewer')!;
      buttonByText(t('viewer.result'), viewer)!.click();
      fixture.detectChanges();
      const row4 = [...viewer.querySelectorAll('tbody tr')].find((tr) => tr.querySelector('td')?.textContent?.trim() === '4')!;
      row4.querySelectorAll('td')[1].click(); // ring_no
      await settle();

      expect(editorHeading()).toBe(`${t('review.page', { n: 1 })} · ${t('review.row', { n: 4 })}`);
      expect(editor().textContent).toContain(t('review.noFlags'));
      expect(field('species').value).toBe('Sp4');
    });

    it('shows unsaved edits in the result table', async () => {
      await start();
      const viewer = el().querySelector('app-pipeline-viewer')!;
      buttonByText(t('viewer.result'), viewer)!.click();
      type(field('species'), 'Live edit');
      expect(viewer.querySelector('tbody')!.textContent).toContain('Live edit');
    });
  });

  describe('split view', () => {
    async function openSplit() {
      el().querySelector<HTMLButtonElement>(`button[aria-label="${t('review.splitView')}"]`)!.click();
      await settle();
      return el().querySelector('app-split-review')!;
    }

    it('toggles between the column and the split view', async () => {
      await start();
      const split = await openSplit();
      expect(split).toBeTruthy();
      expect(el().querySelector('app-record-editor')).toBeNull();
      split.querySelector<HTMLButtonElement>(`button[aria-label="${t('review.columnView')}"]`)!.click();
      await settle();
      expect(el().querySelector('app-split-review')).toBeNull();
      expect(el().querySelector('app-record-editor')).toBeTruthy();
    });

    it('Save & next resolves the whole page after the unresolved-flags warning', async () => {
      await start();
      const split = await openSplit();
      const ring = split.querySelector<HTMLInputElement>('#split-cell-3-ring_no')!;
      type(ring, 'AB1');

      buttonByText(t('review.save'), split)!.click();
      fixture.detectChanges();
      expect(split.textContent).toContain(t('review.unresolvedWarn', { n: 3 }));
      expect(server.writes).toEqual([]);

      buttonByText(t('review.saveAnyway'), split)!.click();
      await settle();

      expect(server.writes).toEqual([
        { method: 'POST', url: '/api/docs/d1/page/1/resolve', body: { rows: { 3: { ring_no: 'AB1' } } } },
      ]);
      expect(text()).toContain(t('review.pageSaved', { n: 1 }));
      // moves on to the next page with open flags
      expect(split.querySelector<HTMLSelectElement>('select')!.value).toBe('2');
    });

    it('Undo after a page save restores the edited rows and reopens the flags', async () => {
      await start();
      const split = await openSplit();
      type(split.querySelector<HTMLInputElement>('#split-cell-3-ring_no')!, 'AB1');
      buttonByText(t('review.save'), split)!.click();
      fixture.detectChanges();
      buttonByText(t('review.saveAnyway'), split)!.click();
      await settle();
      server.writes = [];

      buttonByText(t('review.undo'))!.click();
      await settle();
      const restore = server.writes[0];
      expect(restore.url).toBe('/api/docs/d1/page/1/resolve');
      expect((restore.body as { rows: Record<number, Values> }).rows[3].ring_no).toBe('');
      expect(server.writes[1]).toEqual({ method: 'POST', url: '/api/docs/d1/flags/reopen', body: { ids: [1, 2, 3] } });
    });

    it('Skip goes to the next page with open flags, wrapping within the file', async () => {
      await start();
      const split = await openSplit();
      buttonByText(t('review.skip'), split)!.click();
      await settle();
      expect(split.querySelector<HTMLSelectElement>('select')!.value).toBe('2');

      buttonByText(t('review.skip'), split)!.click();
      await settle();
      expect(server.writes).toEqual([]);
      // page 1 of d1 still has open flags, so skipping wraps to it before switching files
      expect(split.querySelector<HTMLSelectElement>('select')!.value).toBe('1');
    });

    it('Skip on the only flagged page of a file moves on to the next file', async () => {
      await start();
      fileSelect()!.value = 'd2';
      fileSelect()!.dispatchEvent(new Event('change'));
      await settle();
      const split = await openSplit();
      expect(split.querySelector('img')!.getAttribute('src')).toBe('/api/docs/d2/artifacts/cropped/page/1');

      buttonByText(t('review.skip'), split)!.click();
      await settle();
      expect(fileSelect()!.value).toBe('d1');
      expect(el().querySelector('app-split-review img')!.getAttribute('src')).toBe('/api/docs/d1/artifacts/cropped/page/1');
      expect(server.writes).toEqual([]);
    });
  });

  describe('live updates', () => {
    function emit(ev: ServerEvent) {
      vi.useFakeTimers();
      rt.events$.next(ev);
      vi.advanceTimersByTime(500);
      vi.useRealTimers();
    }

    it('reloads the queue on review.pending', async () => {
      await start();
      server.docs['d2'].flags.push(flag({ id: 50, page: 1, row: 1, column: 'time' }));
      emit({ type: 'review.pending', files_left: 2, flags_left: 6 });
      await settle();
      expect(text()).toContain(`6 ${t('review.flagsLeft')}`);
    });

    it('reloads the open file on its doc.status', async () => {
      await start();
      server.docs['d1'].flags.push(flag({ id: 51, page: 1, row: 4, column: 'time', message: 'new from elsewhere' }));
      server.docs['d1'].flags.sort((a, b) => a.page - b.page || a.row - b.row); // the API returns flags ordered by (page, row)
      emit({ type: 'doc.status', doc: server.docOut('d1'), progress: null });
      fixture.detectChanges();
      const urls = http.match(() => true).map((r) => {
        const u = r.request.urlWithParams;
        server.handle(r);
        return u;
      }).sort();
      expect(urls).toEqual(['/api/docs/d1', '/api/docs/d1/flags', '/api/docs/d1/page/1/rows']);
      await settle();
      expect(recordButtons().map((b) => b.querySelector('span')!.textContent)).toEqual(['#2', '#4', '#5', '#1']);
    });

    it('ignores doc.status of other files', async () => {
      await start();
      emit({ type: 'doc.status', doc: server.docOut('d2'), progress: null });
      fixture.detectChanges();
      http.expectNone(() => true);
    });
  });
});
