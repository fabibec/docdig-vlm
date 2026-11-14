import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { By } from '@angular/platform-browser';
import { signal } from '@angular/core';
import { Subject } from 'rxjs';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { BatchesPage } from './batches-page';
import { BatchCard } from './batch-card';
import { UploadDialog } from './upload-dialog';
import { BatchOut, Paginated, Progress, ServerEvent } from '../../core/api/api.types';
import { I18n } from '../../core/i18n/i18n';
import { Realtime } from '../../core/realtime/realtime.service';
import { ZardAlertDialogService } from '@/shared/components/alert-dialog';

class MockRealtime {
  readonly events$ = new Subject<ServerEvent>();
  readonly pending = signal({ files_left: 0, flags_left: 0 });
  readonly connected = signal(true);
  readonly progress = signal<Record<string, Progress>>({});
}

const PAGE1 = '/api/batches?page=1&size=10';

function batch(slug: string, name = slug): BatchOut {
  return {
    slug, name, created_at: '2026-11-05T12:00:00Z', num_docs: 1,
    counts: { queued: 0, processing: 0, review: 0, done: 1, error: 0 }, open_flags: 0,
  };
}

function page(items: BatchOut[], total = items.length, p = 1): Paginated<BatchOut> {
  return { items, total, page: p, size: 10 };
}

describe('BatchesPage', () => {
  let fixture: ComponentFixture<BatchesPage>;
  let httpMock: HttpTestingController;
  let rt: MockRealtime;
  let t: I18n['t'];

  const el = () => fixture.nativeElement as HTMLElement;

  async function render() {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  async function load(data: Paginated<BatchOut>, url = PAGE1) {
    fixture.detectChanges();
    httpMock.expectOne(url).flush(data);
    await render();
  }

  /** Lets the 400 ms debounce of the realtime listener elapse. */
  function emitAndDebounce(...events: ServerEvent[]) {
    vi.useFakeTimers();
    for (const e of events) rt.events$.next(e);
    vi.advanceTimersByTime(400);
    vi.useRealTimers();
    fixture.detectChanges();
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BatchesPage],
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
    httpMock = TestBed.inject(HttpTestingController);
    rt = TestBed.inject(Realtime) as unknown as MockRealtime;
    fixture = TestBed.createComponent(BatchesPage);
  });

  afterEach(() => {
    httpMock.verify();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('shows a loading text, then one card per batch', async () => {
    fixture.detectChanges();
    expect(el().textContent).toContain(t('common.loading'));
    httpMock.expectOne(PAGE1).flush(page([batch('a', 'Batch A'), batch('b', 'Batch B')]));
    await render();

    const cards = fixture.debugElement.queryAll(By.directive(BatchCard));
    expect(cards.length).toBe(2);
    expect(el().textContent).toContain('Batch A');
    expect(el().textContent).toContain('Batch B');
  });

  it('shows the empty state without batches', async () => {
    await load(page([]));
    expect(el().querySelector('app-empty-state')?.textContent).toContain(t('batches.empty'));
  });

  it('shows an error with a retry button', async () => {
    fixture.detectChanges();
    httpMock.expectOne(PAGE1).flush(null, { status: 500, statusText: 'Server Error' });
    await render();
    expect(el().textContent).toContain(t('common.error'));

    [...el().querySelectorAll('button')].find((b) => b.textContent?.trim() === t('common.retry'))!.click();
    fixture.detectChanges();
    httpMock.expectOne(PAGE1).flush(page([batch('a', 'Batch A')]));
    await render();
    expect(el().textContent).toContain('Batch A');
  });

  it('pages through the batches 10 at a time', async () => {
    await load(page(Array.from({ length: 10 }, (_, i) => batch(`b${i}`)), 15));
    el().querySelector<HTMLButtonElement>(`button[aria-label="${t('pager.next')}"]`)!.click();
    fixture.detectChanges();
    httpMock.expectOne('/api/batches?page=2&size=10').flush(page([batch('b10')], 15, 2));
    await render();
    expect(fixture.debugElement.queryAll(By.directive(BatchCard)).length).toBe(1);
  });

  describe('delete', () => {
    it('reloads the list when a card reports a deleted batch', async () => {
      await load(page([batch('a', 'Batch A')]));
      fixture.debugElement.query(By.directive(BatchCard)).componentInstance.deleted.emit();
      fixture.detectChanges();

      httpMock.expectOne(PAGE1).flush(page([]));
      await render();
      expect(el().querySelector('app-empty-state')).toBeTruthy();
    });

    it('confirm in the card deletes the batch and reloads the list', async () => {
      await load(page([batch('a', 'Batch A')]));
      vi.spyOn(TestBed.inject(ZardAlertDialogService), 'confirm').mockImplementation((cfg) => {
        (cfg.zOnOk as () => void)();
        return {} as ReturnType<ZardAlertDialogService['confirm']>;
      });

      el().querySelector<HTMLButtonElement>(`button[aria-label="${t('batches.delete')}"]`)!.click();
      httpMock.expectOne({ method: 'DELETE', url: '/api/batches/a' }).flush(null);
      fixture.detectChanges();
      httpMock.expectOne(PAGE1).flush(page([]));
      await render();
      expect(el().textContent).not.toContain('Batch A');
    });
  });

  describe('live updates', () => {
    it('reloads on batch.deleted', async () => {
      await load(page([batch('a', 'Batch A')]));
      emitAndDebounce({ type: 'batch.deleted', batch: batch('a') });
      httpMock.expectOne(PAGE1).flush(page([]));
      await render();
      expect(el().querySelector('app-empty-state')).toBeTruthy();
    });

    it('reloads once for a burst of batch.created / batch.updated events', async () => {
      await load(page([]));
      emitAndDebounce(
        { type: 'batch.created', batch: batch('n') },
        { type: 'batch.updated', batch: batch('n') },
        { type: 'batch.updated', batch: batch('n') },
      );
      httpMock.expectOne(PAGE1).flush(page([batch('n', 'New one')]));
      await render();
      expect(el().textContent).toContain('New one');
    });

    it('does not reload the list on doc.status or review.pending', async () => {
      await load(page([]));
      emitAndDebounce(
        { type: 'review.pending', files_left: 1, flags_left: 2 },
        { type: 'doc.status', doc: { slug: 'd', batch_slug: 'a', filename: 'd.pdf', status: 'done', num_pages: 1, open_flags: 0, error: null, updated_at: '' }, progress: null },
      );
      httpMock.expectNone(PAGE1);
    });
  });

  describe('upload dialog', () => {
    async function openDialog() {
      [...el().querySelectorAll('button')].find((b) => b.textContent?.trim() === t('batches.new'))!.click();
      await render();
      return fixture.debugElement.query(By.directive(UploadDialog));
    }

    it('opens with "New batch" and closes again', async () => {
      await load(page([]));
      const dialog = await openDialog();
      expect(dialog).toBeTruthy();

      (dialog.componentInstance as UploadDialog).closed.emit();
      await render();
      expect(fixture.debugElement.query(By.directive(UploadDialog))).toBeNull();
    });

    it('jumps back to page 1 and reloads after a batch was created', async () => {
      await load(page(Array.from({ length: 10 }, (_, i) => batch(`b${i}`)), 15));
      el().querySelector<HTMLButtonElement>(`button[aria-label="${t('pager.next')}"]`)!.click();
      fixture.detectChanges();
      httpMock.expectOne('/api/batches?page=2&size=10').flush(page([batch('b10')], 15, 2));
      await render();

      const dialog = await openDialog();
      (dialog.componentInstance as UploadDialog).created.emit();
      fixture.detectChanges();
      httpMock.expectOne(PAGE1).flush(page([batch('new', 'Fresh batch')], 16));
      await render();
      expect(fixture.debugElement.query(By.directive(UploadDialog))).toBeNull();
      expect(el().textContent).toContain('Fresh batch');
    });
  });
});
