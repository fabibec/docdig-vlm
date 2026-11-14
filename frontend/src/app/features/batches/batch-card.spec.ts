import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { signal } from '@angular/core';
import { Subject, of } from 'rxjs';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { BatchCard } from './batch-card';
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

describe('BatchCard', () => {
  let fixture: ComponentFixture<BatchCard>;
  let component: BatchCard;
  let api: Api;
  let httpMock: HttpTestingController;

  const mockBatch: BatchOut = {
    slug: 'test-batch',
    name: 'Test Batch',
    created_at: new Date().toISOString(),
    num_docs: 2,
    counts: { queued: 0, processing: 0, review: 1, done: 1, error: 0 },
    open_flags: 1,
  };

  const mockDocs: Paginated<DocOut> = {
    items: [
      {
        slug: 'doc-1',
        batch_slug: 'test-batch',
        filename: 'scan1.pdf',
        status: 'done',
        num_pages: 1,
        open_flags: 0,
        error: null,
        updated_at: new Date().toISOString(),
        total_cost: 0.0034,
      },
      {
        slug: 'doc-2',
        batch_slug: 'test-batch',
        filename: 'scan2.pdf',
        status: 'review',
        num_pages: 1,
        open_flags: 1,
        error: null,
        updated_at: new Date().toISOString(),
        total_cost: 0.00005,
      },
    ],
    total: 2,
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

    api = TestBed.inject(Api);
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

  describe('formatCost', () => {
    it('shows the whole document cost with 2 decimals and the page count', () => {
      expect(component.formatCost(0.02, 5)).toMatch(/^\$0\.02 \(5 (pages|Seiten)\)$/);
      expect(component.formatCost(1.5, 1)).toMatch(/^\$1\.50 \(1 (page|Seite)\)$/);
    });

    it('keeps 4 decimals for sub-cent documents', () => {
      expect(component.formatCost(0.0034, 2)).toMatch(/^\$0\.0034 \(2 (pages|Seiten)\)$/);
    });

    it('omits the page count when unknown', () => {
      expect(component.formatCost(0, null)).toBe('$0.00');
    });
  });

  describe('Cost display expansion behavior (OP5)', () => {
    it('does not display per-page cost when collapsed', () => {
      const text = fixture.nativeElement.textContent;
      expect(text).not.toContain('$0.0034');
      expect(text).not.toContain('$0.0001');
      expect(text).not.toMatch(/\((\d+) (pages?|Seiten?)\)/);
    });

    it('displays per-page cost when expanded', async () => {
      // Toggle expansion
      const chevronBtn = fixture.nativeElement.querySelector('button.btn-icon');
      chevronBtn.click();
      fixture.detectChanges();

      // Respond to httpResource
      const req = httpMock.expectOne('/api/batches/test-batch/docs?page=1&size=10');
      req.flush(mockDocs);
      TestBed.flushEffects();
      await fixture.whenStable();
      fixture.detectChanges();

      const text = fixture.nativeElement.textContent;
      expect(text).toContain('$0.0034 (1');
      expect(text).toContain('$0.0001 (1');
    });
  });

  describe('Batch deletion (OP6)', () => {
    it('prompts confirmation and emits deleted event on success', () => {
      const confirmSpy = mockConfirm(true);
      const deleteSpy = vi.spyOn(api, 'deleteBatch').mockReturnValue(of(undefined));
      let emitted = false;
      component.deleted.subscribe(() => { emitted = true; });

      const deleteBtn = fixture.nativeElement.querySelector('button[aria-label="Batch löschen"], button[aria-label="Delete batch"]');
      expect(deleteBtn).toBeTruthy();
      deleteBtn.click();

      expect(confirmSpy).toHaveBeenCalled();
      expect(deleteSpy).toHaveBeenCalledWith('test-batch');
      expect(emitted).toBe(true);
    });

    it('does not delete if confirmation is cancelled', () => {
      const confirmSpy = mockConfirm(false);
      const deleteSpy = vi.spyOn(api, 'deleteBatch');
      let emitted = false;
      component.deleted.subscribe(() => { emitted = true; });

      const deleteBtn = fixture.nativeElement.querySelector('button[aria-label="Batch löschen"], button[aria-label="Delete batch"]');
      deleteBtn.click();

      expect(confirmSpy).toHaveBeenCalled();
      expect(deleteSpy).not.toHaveBeenCalled();
      expect(emitted).toBe(false);
    });
  });

  describe('Batch renaming (OP7.2)', () => {
    it('renames batch inline and updates displayed name', () => {
      const renameBtn = fixture.nativeElement.querySelector('button[aria-label="Batch umbenennen"], button[aria-label="Rename batch"]');
      expect(renameBtn).toBeTruthy();
      renameBtn.click();
      fixture.detectChanges();

      const input = fixture.nativeElement.querySelector('input');
      expect(input).toBeTruthy();
      expect(input.value).toBe('Test Batch');

      input.value = 'Renamed Batch';
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();

      const updateSpy = vi.spyOn(api, 'updateBatch').mockReturnValue(of({ ...mockBatch, name: 'Renamed Batch' }));
      const form = fixture.nativeElement.querySelector('form');
      form.dispatchEvent(new Event('submit'));
      fixture.detectChanges();

      expect(updateSpy).toHaveBeenCalledWith('test-batch', { name: 'Renamed Batch' });
      expect(fixture.nativeElement.textContent).toContain('Renamed Batch');
    });
  });

  describe('Document renaming (OP7.2)', () => {
    it('renames document inline and ensures .pdf extension', async () => {
      // Expand batch first
      const chevronBtn = fixture.nativeElement.querySelector('button.btn-icon');
      chevronBtn.click();
      fixture.detectChanges();

      const req = httpMock.expectOne('/api/batches/test-batch/docs?page=1&size=10');
      req.flush(mockDocs);
      TestBed.flushEffects();
      await fixture.whenStable();
      fixture.detectChanges();

      // Click pencil on first doc
      const docRenameBtn = fixture.nativeElement.querySelector('button[aria-label="Datei umbenennen"], button[aria-label="Rename file"]');
      expect(docRenameBtn).toBeTruthy();
      docRenameBtn.click();
      fixture.detectChanges();

      const docInput = fixture.nativeElement.querySelector('input.input');
      expect(docInput).toBeTruthy();
      expect(docInput.value).toBe('scan1.pdf');

      // Edit without .pdf extension
      docInput.value = 'scan1_new';
      docInput.dispatchEvent(new Event('input'));
      fixture.detectChanges();

      const updateDocSpy = vi.spyOn(api, 'updateDoc').mockReturnValue(of({ ...mockDocs.items[0], filename: 'scan1_new.pdf' }));
      const docForm = fixture.nativeElement.querySelector('form');
      docForm.dispatchEvent(new Event('submit'));
      fixture.detectChanges();

      // Verify .pdf was appended
      expect(updateDocSpy).toHaveBeenCalledWith('doc-1', { filename: 'scan1_new.pdf' });

      // Handle reload request triggered by this.docs.reload()
      const reloadReq = httpMock.expectOne('/api/batches/test-batch/docs?page=1&size=10');
      reloadReq.flush(mockDocs);
    });
  });
});
