import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { Subject, of, throwError } from 'rxjs';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { BatchCard } from './batch-card';
import { Api } from '../../core/api/api.service';
import { BatchOut, DocOut, Paginated, Progress, ServerEvent } from '../../core/api/api.types';
import { I18n } from '../../core/i18n/i18n';
import { Realtime } from '../../core/realtime/realtime.service';
import { ZardAlertDialogService } from '@/shared/components/alert-dialog';

class MockRealtime {
  readonly events$ = new Subject<ServerEvent>();
  readonly pending = signal({ files_left: 0, flags_left: 0 });
  readonly connected = signal(true);
  readonly progress = signal<Record<string, Progress>>({});
}

/** Replaces the zard confirm dialog: `ok` simulates the user pressing the OK button. */
function mockConfirm(ok: boolean) {
  return vi.spyOn(TestBed.inject(ZardAlertDialogService), 'confirm').mockImplementation((cfg) => {
    if (ok) (cfg.zOnOk as () => void)?.();
    return {} as ReturnType<ZardAlertDialogService['confirm']>;
  });
}

function doc(over: Partial<DocOut>): DocOut {
  return {
    slug: 'doc', batch_slug: 'test-batch', filename: 'doc.pdf', status: 'done', num_pages: 1,
    open_flags: 0, error: null, updated_at: '2026-11-05T12:00:00Z', total_cost: null, ...over,
  };
}

const DOCS_URL = '/api/batches/test-batch/docs?page=1&size=10';

function httpError(status: number, detail?: string) {
  return throwError(() => new HttpErrorResponse({ status, statusText: 'Conflict', error: detail ? { detail } : null }));
}

describe('BatchCard', () => {
  let fixture: ComponentFixture<BatchCard>;
  let component: BatchCard;
  let api: Api;
  let httpMock: HttpTestingController;
  let rt: MockRealtime;
  let t: I18n['t'];

  const batch: BatchOut = {
    slug: 'test-batch',
    name: 'Test Batch',
    created_at: '2026-11-05T12:00:00Z',
    num_docs: 2,
    counts: { queued: 0, processing: 0, review: 1, done: 1, error: 0 },
    open_flags: 1,
  };

  const docs: Paginated<DocOut> = {
    items: [
      doc({ slug: 'doc-1', filename: 'scan1.pdf', status: 'done', num_pages: 5, total_cost: 0.0034 }),
      doc({ slug: 'doc-2', filename: 'scan2.pdf', status: 'review', num_pages: 1, open_flags: 1, total_cost: 0.00005 }),
      doc({ slug: 'doc-3', filename: 'zero.pdf', status: 'done', num_pages: 2, total_cost: 0 }),
      doc({ slug: 'doc-4', filename: 'legacy_null.pdf', status: 'done', num_pages: 4, total_cost: null }),
      doc({ slug: 'doc-5', filename: 'legacy_undefined.pdf', status: 'review', num_pages: 3, total_cost: undefined }),
      doc({ slug: 'doc-6', filename: 'busy.pdf', status: 'processing', num_pages: null, error: null }),
      doc({ slug: 'doc-7', filename: 'broken.pdf', status: 'error', num_pages: null, error: 'PDF corrupt' }),
    ],
    total: 7,
    page: 1,
    size: 10,
  };

  const el = () => fixture.nativeElement as HTMLElement;
  const button = (key: string) => el().querySelector<HTMLButtonElement>(`button[aria-label="${t(key)}"]`);
  const buttons = (key: string) => el().querySelectorAll<HTMLButtonElement>(`button[aria-label="${t(key)}"]`);

  async function render() {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  async function expand(data: Paginated<DocOut> = docs) {
    button('batches.showFiles')!.click();
    fixture.detectChanges();
    httpMock.expectOne(DOCS_URL).flush(data);
    await render();
  }

  function type(input: HTMLInputElement, value: string) {
    input.value = value;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BatchCard],
      providers: [
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: Realtime, useClass: MockRealtime },
      ],
    }).compileComponents();

    const i18n = TestBed.inject(I18n);
    i18n.lang.set('en');
    t = i18n.t.bind(i18n);
    api = TestBed.inject(Api);
    httpMock = TestBed.inject(HttpTestingController);
    rt = TestBed.inject(Realtime) as unknown as MockRealtime;
    fixture = TestBed.createComponent(BatchCard);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('batch', batch);
    fixture.detectChanges();
  });

  afterEach(() => {
    httpMock.verify();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  describe('header', () => {
    it('shows the name as link to the batch review queue, the file count, status badges and open flags', () => {
      const link = el().querySelector<HTMLAnchorElement>('a')!;
      expect(link.textContent?.trim()).toBe('Test Batch');
      expect(link.getAttribute('href')).toBe('/review/batch/test-batch');

      const text = el().textContent!;
      expect(text).toContain(t('batches.files', { n: 2 }));
      expect(text).toContain(t('batches.flagOne', { n: 1 }));
      const badges = [...el().querySelectorAll('app-status-badge')].map((b) => b.textContent?.replace(/\s+/g, ' ').trim());
      expect(badges).toEqual([`${t('status.review')} · 1`, `${t('status.done')} · 1`]);
    });

    it('uses the singular texts for one file and one flag and hides zero counts', () => {
      fixture.componentRef.setInput('batch', {
        ...batch, num_docs: 1, open_flags: 0, counts: { queued: 0, processing: 0, review: 0, done: 1, error: 0 },
      });
      fixture.detectChanges();
      const text = el().textContent!;
      expect(text).toContain(t('batches.fileOne'));
      expect(text).not.toContain(t('batches.flags', { n: 0 }));
      expect(el().querySelectorAll('app-status-badge').length).toBe(1);
    });

    it('does not load the file list while collapsed', () => {
      httpMock.expectNone(DOCS_URL);
      expect(el().querySelectorAll('li').length).toBe(0);
    });
  });

  describe('formatCost', () => {
    it('shows the whole document cost with 2 decimals and the page count', () => {
      expect(component.formatCost(0.02, 5)).toBe('$0.02 (5 pages)');
      expect(component.formatCost(1.5, 1)).toBe('$1.50 (1 page)');
      expect(component.formatCost(0, 2)).toBe('$0.00 (2 pages)');
    });

    it('keeps 4 decimals for sub-cent documents', () => {
      expect(component.formatCost(0.0034, 2)).toBe('$0.0034 (2 pages)');
      expect(component.formatCost(0.0001, 1)).toBe('$0.0001 (1 page)');
    });

    it('omits the page count when unknown', () => {
      expect(component.formatCost(0, null)).toBe('$0.00');
      expect(component.formatCost(0.02, null)).toBe('$0.02');
    });

    it('follows the language', () => {
      TestBed.inject(I18n).lang.set('de');
      expect(component.formatCost(0.02, 5)).toBe('$0.02 (5 Seiten)');
    });
  });

  describe('cost display', () => {
    it('shows no cost while collapsed', () => {
      const text = el().textContent!;
      expect(text).not.toContain('$');
      expect(text).not.toMatch(/\(\d+ pages?\)/);
    });

    it('shows each file cost in the expanded list and skips files without a cost', async () => {
      await expand();
      const rows = [...el().querySelectorAll('li')];
      expect(rows.length).toBe(7);
      expect(rows[0].textContent).toContain('$0.0034 (5 pages)');
      expect(rows[1].textContent).toContain('$0.0001 (1 page)');
      expect(rows[2].textContent).toContain('$0.00 (2 pages)');
      for (const row of [rows[3], rows[4]]) {
        expect(row.textContent).not.toContain('$');
      }
      expect(el().textContent).toContain('legacy_null.pdf');
      expect(el().textContent).toContain('legacy_undefined.pdf');
    });

    it('hides the costs again when collapsed', async () => {
      await expand();
      button('batches.hideFiles')!.click();
      fixture.detectChanges();
      expect(el().textContent).not.toContain('$');
      expect(el().querySelectorAll('li').length).toBe(0);
    });
  });

  describe('file list', () => {
    it('links review files to the review queue and done files to the document page', async () => {
      await expand();
      const rows = [...el().querySelectorAll('li')];
      expect(rows[0].querySelector('a')?.getAttribute('href')).toBe('/doc-1');
      expect(rows[1].querySelector('a')?.getAttribute('href')).toBe('/review/file/doc-2');
    });

    it('does not link processing or failed files and shows the error as tooltip', async () => {
      await expand();
      const rows = [...el().querySelectorAll('li')];
      expect(rows[5].querySelector('a')).toBeNull();
      expect(rows[6].querySelector('a')).toBeNull();
      expect(rows[6].querySelector('span[title="PDF corrupt"]')?.textContent?.trim()).toBe('broken.pdf');
    });

    it('shows live processing progress from Realtime', async () => {
      await expand();
      rt.progress.set({ 'doc-6': { step: 'cropped', page: 2, pages: 5 } });
      fixture.detectChanges();
      const row = el().querySelectorAll('li')[5];
      expect(row.textContent).toContain(`${t('step.cropped')} 2/5`);
    });

    it('requests the next page through the paginator', async () => {
      await expand({ ...docs, total: 25 });
      button('pager.next')!.click();
      fixture.detectChanges();
      httpMock.expectOne('/api/batches/test-batch/docs?page=2&size=10').flush({ ...docs, page: 2, total: 25 });
      await render();
    });

    it('reloads the expanded list on doc.status events of this batch (debounced)', async () => {
      await expand();
      vi.useFakeTimers();
      rt.events$.next({ type: 'doc.status', doc: doc({ slug: 'doc-6', status: 'review' }), progress: null });
      rt.events$.next({ type: 'doc.status', doc: doc({ slug: 'doc-6', status: 'done' }), progress: null });
      vi.advanceTimersByTime(300);
      vi.useRealTimers();
      fixture.detectChanges();
      httpMock.expectOne(DOCS_URL).flush(docs);
      await render();
    });

    it('ignores doc.status events of other batches', async () => {
      await expand();
      vi.useFakeTimers();
      rt.events$.next({ type: 'doc.status', doc: doc({ slug: 'x', batch_slug: 'other' }), progress: null });
      vi.advanceTimersByTime(1000);
      vi.useRealTimers();
      fixture.detectChanges();
      httpMock.expectNone(DOCS_URL);
    });

    it('ignores doc.status events while collapsed', () => {
      vi.useFakeTimers();
      rt.events$.next({ type: 'doc.status', doc: doc({ slug: 'doc-6' }), progress: null });
      vi.advanceTimersByTime(1000);
      vi.useRealTimers();
      fixture.detectChanges();
      httpMock.expectNone(DOCS_URL);
    });
  });

  describe('delete', () => {
    it('asks for confirmation, then deletes the batch and emits deleted', () => {
      const confirmSpy = mockConfirm(true);
      const deleteSpy = vi.spyOn(api, 'deleteBatch').mockReturnValue(of(undefined));
      const deleted = vi.fn();
      component.deleted.subscribe(deleted);

      button('batches.delete')!.click();

      expect(confirmSpy).toHaveBeenCalledOnce();
      expect(confirmSpy.mock.calls[0][0].zDescription).toBe(t('batches.deleteConfirm'));
      expect(confirmSpy.mock.calls[0][0].zOkDestructive).toBe(true);
      expect(deleteSpy).toHaveBeenCalledWith('test-batch');
      expect(deleted).toHaveBeenCalledOnce();
    });

    it('sends DELETE /api/batches/{slug} on confirm', () => {
      mockConfirm(true);
      button('batches.delete')!.click();
      const req = httpMock.expectOne('/api/batches/test-batch');
      expect(req.request.method).toBe('DELETE');
      req.flush(null);
    });

    it('shows an error and keeps the card when the delete fails', () => {
      mockConfirm(true);
      vi.spyOn(api, 'deleteBatch').mockReturnValue(httpError(500, 'disk full'));
      const deleted = vi.fn();
      component.deleted.subscribe(deleted);

      button('batches.delete')!.click();
      fixture.detectChanges();

      expect(deleted).not.toHaveBeenCalled();
      expect(el().querySelector('[role="alert"]')?.textContent).toContain(t('batches.deleteFailed', { msg: 'disk full' }));
    });

    it('does nothing when the confirmation is cancelled', () => {
      const confirmSpy = mockConfirm(false);
      const deleteSpy = vi.spyOn(api, 'deleteBatch');
      const deleted = vi.fn();
      component.deleted.subscribe(deleted);

      button('batches.delete')!.click();

      expect(confirmSpy).toHaveBeenCalled();
      expect(deleteSpy).not.toHaveBeenCalled();
      expect(deleted).not.toHaveBeenCalled();
      expect(el().textContent).toContain('Test Batch');
    });

    it('stops the click from propagating', () => {
      mockConfirm(false);
      const click = new MouseEvent('click', { bubbles: true, cancelable: true });
      const stop = vi.spyOn(click, 'stopPropagation');
      button('batches.delete')!.dispatchEvent(click);
      expect(stop).toHaveBeenCalled();
    });
  });

  describe('batch rename', () => {
    function startRename() {
      button('batches.rename')!.click();
      fixture.detectChanges();
      return el().querySelector<HTMLInputElement>('form input')!;
    }

    it('opens an inline form prefilled with the current name', () => {
      const input = startRename();
      expect(input.value).toBe('Test Batch');
    });

    it('saves with PATCH and shows the new name', () => {
      const input = startRename();
      type(input, '  Renamed Batch  ');
      el().querySelector('form')!.dispatchEvent(new Event('submit'));
      fixture.detectChanges();

      const req = httpMock.expectOne('/api/batches/test-batch');
      expect(req.request.method).toBe('PATCH');
      expect(req.request.body).toEqual({ name: 'Renamed Batch' });
      req.flush({ ...batch, name: 'Renamed Batch' });
      fixture.detectChanges();

      expect(el().querySelector('form')).toBeNull();
      expect(el().querySelector('a')?.textContent?.trim()).toBe('Renamed Batch');
    });

    it('saves with the ✓ button', () => {
      const spy = vi.spyOn(api, 'updateBatch').mockReturnValue(of({ ...batch, name: 'Via button' }));
      type(startRename(), 'Via button');
      button('batches.save')!.click();
      fixture.detectChanges();
      expect(spy).toHaveBeenCalledWith('test-batch', { name: 'Via button' });
      expect(el().textContent).toContain('Via button');
    });

    it('shows an error and stays in edit mode when the rename fails', () => {
      vi.spyOn(api, 'updateBatch').mockReturnValue(httpError(409, 'name taken'));
      type(startRename(), 'Taken');
      button('batches.save')!.click();
      fixture.detectChanges();

      expect(el().querySelector('form')).not.toBeNull();
      expect(el().querySelector('[role="alert"]')?.textContent).toContain(t('batches.renameFailed', { msg: 'name taken' }));

      button('batches.cancel')!.click();
      fixture.detectChanges();
      expect(el().querySelector('[role="alert"]')).toBeNull();
    });

    it('follows a later rename from another tab after renaming here', () => {
      vi.spyOn(api, 'updateBatch').mockReturnValue(of({ ...batch, name: 'Mine' }));
      type(startRename(), 'Mine');
      button('batches.save')!.click();
      fixture.detectChanges();
      expect(el().querySelector('a')?.textContent?.trim()).toBe('Mine');

      fixture.componentRef.setInput('batch', { ...batch, name: 'From other tab' });
      fixture.detectChanges();
      expect(el().querySelector('a')?.textContent?.trim()).toBe('From other tab');
    });

    it('cancels with Esc without saving', () => {
      const spy = vi.spyOn(api, 'updateBatch');
      const input = startRename();
      type(input, 'Unsaved Name');
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      fixture.detectChanges();

      expect(el().querySelector('form')).toBeNull();
      expect(spy).not.toHaveBeenCalled();
      expect(el().textContent).toContain('Test Batch');
      expect(el().textContent).not.toContain('Unsaved Name');
    });

    it('cancels with the ✕ button', () => {
      const spy = vi.spyOn(api, 'updateBatch');
      startRename();
      button('batches.cancel')!.click();
      fixture.detectChanges();
      expect(el().querySelector('form')).toBeNull();
      expect(spy).not.toHaveBeenCalled();
    });

    it('does not call the API for an unchanged or empty name', () => {
      const spy = vi.spyOn(api, 'updateBatch');
      startRename();
      el().querySelector('form')!.dispatchEvent(new Event('submit'));
      fixture.detectChanges();
      expect(el().querySelector('form')).toBeNull();

      type(startRename(), '   ');
      el().querySelector('form')!.dispatchEvent(new Event('submit'));
      fixture.detectChanges();
      expect(el().querySelector('form')).toBeNull();
      expect(spy).not.toHaveBeenCalled();
    });
  });

  describe('file rename', () => {
    async function startRename(index = 0) {
      await expand();
      buttons('batches.renameDoc')[index].click();
      fixture.detectChanges();
      return el().querySelector<HTMLInputElement>('li form input')!;
    }

    it('offers a pencil per file and opens an inline form with the filename', async () => {
      await expand();
      expect(buttons('batches.renameDoc').length).toBe(docs.items.length);
      buttons('batches.renameDoc')[1].click();
      fixture.detectChanges();
      expect(el().querySelector<HTMLInputElement>('li form input')!.value).toBe('scan2.pdf');
    });

    it('saves with PATCH /api/docs/{slug}, appends .pdf and reloads the list', async () => {
      const input = await startRename();
      type(input, 'scan1_new');
      el().querySelector('li form')!.dispatchEvent(new Event('submit'));
      fixture.detectChanges();

      const req = httpMock.expectOne('/api/docs/doc-1');
      expect(req.request.method).toBe('PATCH');
      expect(req.request.body).toEqual({ filename: 'scan1_new.pdf' });
      req.flush({ ...docs.items[0], filename: 'scan1_new.pdf' });
      fixture.detectChanges();

      httpMock.expectOne(DOCS_URL).flush({ ...docs, items: [{ ...docs.items[0], filename: 'scan1_new.pdf' }, ...docs.items.slice(1)] });
      await render();
      expect(el().querySelector('li form')).toBeNull();
      expect(el().textContent).toContain('scan1_new.pdf');
    });

    it('shows an error and stays in edit mode when the file rename fails', async () => {
      vi.spyOn(api, 'updateDoc').mockReturnValue(httpError(409, 'still processing'));
      type(await startRename(), 'other');
      el().querySelector('li form')!.dispatchEvent(new Event('submit'));
      fixture.detectChanges();

      expect(el().querySelector('li form')).not.toBeNull();
      expect(el().querySelector('[role="alert"]')?.textContent).toContain(t('batches.renameFailed', { msg: 'still processing' }));
    });

    it('does not append .pdf twice (any case)', async () => {
      const spy = vi.spyOn(api, 'updateDoc').mockReturnValue(of(docs.items[0]));
      let input = await startRename();
      type(input, 'renamed.pdf');
      el().querySelector('li form')!.dispatchEvent(new Event('submit'));
      fixture.detectChanges();
      httpMock.expectOne(DOCS_URL).flush(docs);
      await render();

      buttons('batches.renameDoc')[0].click();
      fixture.detectChanges();
      input = el().querySelector<HTMLInputElement>('li form input')!;
      type(input, 'renamed.PDF');
      el().querySelector('li form')!.dispatchEvent(new Event('submit'));
      fixture.detectChanges();
      httpMock.expectOne(DOCS_URL).flush(docs);
      await render();

      expect(spy.mock.calls).toEqual([['doc-1', { filename: 'renamed.pdf' }], ['doc-1', { filename: 'renamed.PDF' }]]);
    });

    it('cancels with Esc without saving', async () => {
      const spy = vi.spyOn(api, 'updateDoc');
      const input = await startRename();
      type(input, 'cancelled_name');
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      fixture.detectChanges();

      expect(el().querySelector('li form')).toBeNull();
      expect(spy).not.toHaveBeenCalled();
      expect(el().textContent).toContain('scan1.pdf');
    });

    it('cancels with the ✕ button', async () => {
      const spy = vi.spyOn(api, 'updateDoc');
      await startRename();
      button('batches.cancel')!.click();
      fixture.detectChanges();
      expect(el().querySelector('li form')).toBeNull();
      expect(spy).not.toHaveBeenCalled();
    });

    it('does not call the API for an unchanged or empty name', async () => {
      const spy = vi.spyOn(api, 'updateDoc');
      await startRename();
      el().querySelector('li form')!.dispatchEvent(new Event('submit'));
      fixture.detectChanges();
      expect(el().querySelector('li form')).toBeNull();

      buttons('batches.renameDoc')[0].click();
      fixture.detectChanges();
      type(el().querySelector<HTMLInputElement>('li form input')!, '  ');
      el().querySelector('li form')!.dispatchEvent(new Event('submit'));
      fixture.detectChanges();
      expect(el().querySelector('li form')).toBeNull();

      // "scan1" becomes "scan1.pdf" = unchanged
      buttons('batches.renameDoc')[0].click();
      fixture.detectChanges();
      type(el().querySelector<HTMLInputElement>('li form input')!, 'scan1');
      el().querySelector('li form')!.dispatchEvent(new Event('submit'));
      fixture.detectChanges();

      expect(spy).not.toHaveBeenCalled();
    });
  });
});
