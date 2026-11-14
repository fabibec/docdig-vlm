import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { By } from '@angular/platform-browser';
import { signal } from '@angular/core';
import { Subject, of } from 'rxjs';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { BatchCard } from './batch-card';
import { BatchesPage } from './batches-page';
import { Api } from '../../core/api/api.service';
import { BatchOut, DocOut, Paginated, Progress, ServerEvent } from '../../core/api/api.types';
import { Realtime } from '../../core/realtime/realtime.service';
import { ZardAlertDialogService } from '@/shared/components/alert-dialog';

function mockConfirm(ok: boolean) {
  return vi.spyOn(TestBed.inject(ZardAlertDialogService), 'confirm').mockImplementation((cfg) => {
    if (ok) (cfg.zOnOk as () => void)?.();
    return {} as ReturnType<ZardAlertDialogService['confirm']>;
  });
}


class MockRealtime {
  readonly events$ = new Subject<ServerEvent>();
  readonly pending = signal({ files_left: 0, flags_left: 0 });
  readonly connected = signal(true);
  readonly progress = signal<Record<string, Progress>>({});
}

describe('Milestone M3 Empirical Challenge: Cost Display & Batch Deletion', () => {
  const mockBatch: BatchOut = {
    slug: 'empirical-batch',
    name: 'Empirical Batch Test',
    created_at: new Date('2026-11-05T12:00:00Z').toISOString(),
    num_docs: 3,
    counts: { queued: 0, processing: 0, review: 1, done: 2, error: 0 },
    open_flags: 0,
  };

  describe('1. Cost Display Empirical Tests (BatchCard)', () => {
    let fixture: ComponentFixture<BatchCard>;
    let component: BatchCard;
    let httpMock: HttpTestingController;

    const testDocs: Paginated<DocOut> = {
      items: [
        {
          slug: 'doc-standard',
          batch_slug: 'empirical-batch',
          filename: 'standard_cost.pdf',
          status: 'done',
          num_pages: 5,
          open_flags: 0,
          error: null,
          updated_at: new Date().toISOString(),
          total_cost: 0.0034,
        },
        {
          slug: 'doc-sub-cent',
          batch_slug: 'empirical-batch',
          filename: 'micro_cost.pdf',
          status: 'done',
          num_pages: 10,
          open_flags: 0,
          error: null,
          updated_at: new Date().toISOString(),
          total_cost: 0.00004,
        },
        {
          slug: 'doc-threshold',
          batch_slug: 'empirical-batch',
          filename: 'threshold_cost.pdf',
          status: 'done',
          num_pages: 1,
          open_flags: 0,
          error: null,
          updated_at: new Date().toISOString(),
          total_cost: 0.0001,
        },
        {
          slug: 'doc-zero',
          batch_slug: 'empirical-batch',
          filename: 'zero_cost.pdf',
          status: 'done',
          num_pages: 2,
          open_flags: 0,
          error: null,
          updated_at: new Date().toISOString(),
          total_cost: 0,
        },
        {
          slug: 'doc-null-cost',
          batch_slug: 'empirical-batch',
          filename: 'legacy_null.pdf',
          status: 'done',
          num_pages: 4,
          open_flags: 0,
          error: null,
          updated_at: new Date().toISOString(),
          total_cost: null,
        },
        {
          slug: 'doc-undefined-cost',
          batch_slug: 'empirical-batch',
          filename: 'legacy_undefined.pdf',
          status: 'review',
          num_pages: 3,
          open_flags: 0,
          error: null,
          updated_at: new Date().toISOString(),
          total_cost: undefined,
        },
      ],
      total: 6,
      page: 1,
      size: 10,
    };

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

      httpMock = TestBed.inject(HttpTestingController);
      fixture = TestBed.createComponent(BatchCard);
      component = fixture.componentInstance;
      fixture.componentRef.setInput('batch', mockBatch);
      fixture.detectChanges();
    });

    afterEach(() => {
      httpMock.verify();
      vi.restoreAllMocks();
    });

    it('formatCost helper shows total document cost and page count', () => {
      expect(component.formatCost(0.0034, 5)).toMatch(/^\$0\.0034 \(5 (pages|Seiten)\)$/);
      expect(component.formatCost(0.0001, 1)).toMatch(/^\$0\.0001 \(1 (page|Seite)\)$/);
      expect(component.formatCost(0, 2)).toMatch(/^\$0\.00 \(2 (pages|Seiten)\)$/);
      expect(component.formatCost(1.5, 10)).toMatch(/^\$1\.50 \(10 (pages|Seiten)\)$/);
      expect(component.formatCost(0.02, null)).toBe('$0.02');
    });

    it('when expanded() === false, asserts NO per-page cost element or text is rendered in the DOM', () => {
      const rootText = fixture.nativeElement.textContent;
      // Ensure no cost numbers or formatting indicators leak into the collapsed card
      expect(rootText).not.toContain('$');
      expect(rootText).not.toMatch(/\((\d+) (pages?|Seiten?)\)/);

      // Ensure no doc rows exist in DOM
      const listItems = fixture.nativeElement.querySelectorAll('li');
      expect(listItems.length).toBe(0);
    });

    it('when expanded() === true, renders per-page cost for valid costs and handles null/undefined gracefully', async () => {
      // 1. Expand card
      const chevronBtn = fixture.nativeElement.querySelector('button.btn-icon');
      expect(chevronBtn).toBeTruthy();
      chevronBtn.click();
      fixture.detectChanges();

      // 2. Respond with documents containing varied cost profiles
      const req = httpMock.expectOne('/api/batches/empirical-batch/docs?page=1&size=10');
      req.flush(testDocs);
      TestBed.flushEffects();
      await fixture.whenStable();
      fixture.detectChanges();

      const rootText = fixture.nativeElement.textContent;

      // Total document cost followed by the page count
      expect(rootText).toMatch(/\$0\.0034 \(5 (pages|Seiten)\)/);
      expect(rootText).toMatch(/\$0\.0000 \(10 (pages|Seiten)\)/);
      expect(rootText).toMatch(/\$0\.0001 \(1 (page|Seite)\)/);
      expect(rootText).toMatch(/\$0\.00 \(2 (pages|Seiten)\)/);

      // Query all list items and check individual rows
      const rows = fixture.nativeElement.querySelectorAll('li');
      expect(rows.length).toBe(6);

      // Row 4: doc-null-cost (total_cost = null)
      const nullRowText = rows[4].textContent;
      expect(nullRowText).toContain('legacy_null.pdf');
      expect(nullRowText).not.toMatch(/(pages?|Seiten?)\)/);
      expect(nullRowText).not.toContain('$');

      // Row 5: doc-undefined-cost (total_cost = undefined)
      const undefRowText = rows[5].textContent;
      expect(undefRowText).toContain('legacy_undefined.pdf');
      expect(undefRowText).not.toMatch(/(pages?|Seiten?)\)/);
      expect(undefRowText).not.toContain('$');

      // 3. Re-collapse the card: cost elements must disappear from DOM
      chevronBtn.click();
      fixture.detectChanges();

      const collapsedText = fixture.nativeElement.textContent;
      expect(collapsedText).not.toContain('$0.0034');
      expect(collapsedText).not.toMatch(/\((\d+) (pages?|Seiten?)\)/);
      expect(fixture.nativeElement.querySelectorAll('li').length).toBe(0);
    });
  });

  describe('2. Batch Deletion Empirical Tests (BatchCard & BatchesPage)', () => {
    let cardFixture: ComponentFixture<BatchCard>;
    let cardComponent: BatchCard;
    let cardApi: Api;
    let cardHttpMock: HttpTestingController;

    beforeEach(async () => {
      await TestBed.configureTestingModule({
        imports: [BatchCard, BatchesPage],
        providers: [
          provideRouter([]),
          provideHttpClient(),
          provideHttpClientTesting(),
          { provide: Realtime, useClass: MockRealtime },
        ],
      }).compileComponents();

      cardApi = TestBed.inject(Api);
      cardHttpMock = TestBed.inject(HttpTestingController);
      cardFixture = TestBed.createComponent(BatchCard);
      cardComponent = cardFixture.componentInstance;
      cardFixture.componentRef.setInput('batch', mockBatch);
      cardFixture.detectChanges();
    });

    afterEach(() => {
      cardHttpMock.verify();
      vi.restoreAllMocks();
    });

    it('inspects trash button in BatchCard and verifies attributes and icon', () => {
      const trashBtn = cardFixture.nativeElement.querySelector('button.hover\\:text-destructive');
      expect(trashBtn).toBeTruthy();
      expect(trashBtn.getAttribute('type')).toBe('button');
      // Accessible label in German or English
      const ariaLabel = trashBtn.getAttribute('aria-label');
      expect(ariaLabel).toMatch(/löschen|delete/i);

      const trashIcon = trashBtn.querySelector('ng-icon[name="lucideTrash2"]');
      expect(trashIcon).toBeTruthy();
    });

    it('clicking trash button opens the Zard confirm dialog and stops event propagation', () => {
      const confirmSpy = mockConfirm(false);
      const deleteSpy = vi.spyOn(cardApi, 'deleteBatch');

      const trashBtn = cardFixture.nativeElement.querySelector('button.hover\\:text-destructive');
      const clickEvent = new MouseEvent('click', { bubbles: true, cancelable: true });
      const stopPropagationSpy = vi.spyOn(clickEvent, 'stopPropagation');

      trashBtn.dispatchEvent(clickEvent);

      expect(confirmSpy).toHaveBeenCalledOnce();
      expect(confirmSpy.mock.calls[0][0].zDescription).toMatch(/löschen|delete/i);
      expect(stopPropagationSpy).toHaveBeenCalled();
      expect(deleteSpy).not.toHaveBeenCalled();
    });

    it('confirming deletion calls api.deleteBatch and emits deleted event', () => {
      mockConfirm(true);
      const deleteSpy = vi.spyOn(cardApi, 'deleteBatch').mockReturnValue(of(undefined as unknown as void));
      let deletedEmitted = false;
      cardComponent.deleted.subscribe(() => {
        deletedEmitted = true;
      });

      const trashBtn = cardFixture.nativeElement.querySelector('button.hover\\:text-destructive');
      trashBtn.click();

      expect(deleteSpy).toHaveBeenCalledWith('empirical-batch');
      expect(deletedEmitted).toBe(true);
    });

    it('cancelling deletion leaves batch untouched, does NOT call api.deleteBatch, and does NOT emit deleted', () => {
      mockConfirm(false);
      const deleteSpy = vi.spyOn(cardApi, 'deleteBatch');
      let deletedEmitted = false;
      cardComponent.deleted.subscribe(() => {
        deletedEmitted = true;
      });

      const trashBtn = cardFixture.nativeElement.querySelector('button.hover\\:text-destructive');
      trashBtn.click();

      expect(deleteSpy).not.toHaveBeenCalled();
      expect(deletedEmitted).toBe(false);
      // Batch card remains rendered
      expect(cardFixture.nativeElement.textContent).toContain('Empirical Batch Test');
    });

    it('non-emitting API response during deletion does not emit deleted event', () => {
      mockConfirm(true);
      vi.spyOn(cardApi, 'deleteBatch').mockReturnValue(of());
      let deletedEmitted = false;
      cardComponent.deleted.subscribe(() => {
        deletedEmitted = true;
      });

      const trashBtn = cardFixture.nativeElement.querySelector('button.hover\\:text-destructive');
      trashBtn.click();

      expect(deletedEmitted).toBe(false);
    });

    it('in BatchesPage, (deleted) event from BatchCard triggers batches.reload()', async () => {
      const pageFixture = TestBed.createComponent(BatchesPage);
      const pageHttpMock = TestBed.inject(HttpTestingController);

      pageFixture.detectChanges();

      // Initial page load request
      const initialReq = pageHttpMock.expectOne('/api/batches?page=1&size=10');
      initialReq.flush({
        items: [mockBatch],
        total: 1,
        page: 1,
        size: 10,
      });

      TestBed.flushEffects();
      await pageFixture.whenStable();
      pageFixture.detectChanges();

      // Batch card should be rendered
      const cardDebugEl = pageFixture.debugElement.query(By.directive(BatchCard));
      expect(cardDebugEl).toBeTruthy();
      expect(pageFixture.nativeElement.textContent).toContain('Empirical Batch Test');

      // Trigger the deleted event from child BatchCard
      cardDebugEl.componentInstance.deleted.emit();
      TestBed.flushEffects();
      pageFixture.detectChanges();

      // Reload must trigger a new HTTP GET to /api/batches?page=1&size=10
      const reloadReq = pageHttpMock.expectOne('/api/batches?page=1&size=10');
      // Simulate that the batch is soft-deleted on backend and now absent
      reloadReq.flush({
        items: [],
        total: 0,
        page: 1,
        size: 10,
      });

      TestBed.flushEffects();
      await pageFixture.whenStable();
      pageFixture.detectChanges();

      // Check empty state
      const emptyStateEl = pageFixture.nativeElement.querySelector('app-empty-state');
      expect(emptyStateEl).toBeTruthy();
    });

    it('in BatchesPage, Realtime event batch.deleted triggers batches.reload()', async () => {
      const pageFixture = TestBed.createComponent(BatchesPage);
      const pageHttpMock = TestBed.inject(HttpTestingController);
      const rt = TestBed.inject(Realtime) as unknown as MockRealtime;

      pageFixture.detectChanges();

      const initialReq = pageHttpMock.expectOne('/api/batches?page=1&size=10');
      initialReq.flush({
        items: [mockBatch],
        total: 1,
        page: 1,
        size: 10,
      });

      TestBed.flushEffects();
      await pageFixture.whenStable();
      pageFixture.detectChanges();

      // Emit realtime batch.deleted event
      rt.events$.next({ type: 'batch.deleted', batch: mockBatch });

      // Wait for 400ms debounce
      await new Promise((resolve) => setTimeout(resolve, 450));
      TestBed.flushEffects();
      pageFixture.detectChanges();

      // Expect reload request
      const reloadReq = pageHttpMock.expectOne('/api/batches?page=1&size=10');
      reloadReq.flush({
        items: [],
        total: 0,
        page: 1,
        size: 10,
      });

      TestBed.flushEffects();
      await pageFixture.whenStable();
      pageFixture.detectChanges();

      const emptyStateEl = pageFixture.nativeElement.querySelector('app-empty-state');
      expect(emptyStateEl).toBeTruthy();
    });
  });
});
