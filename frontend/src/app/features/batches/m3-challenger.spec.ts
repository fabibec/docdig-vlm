import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { signal } from '@angular/core';
import { Subject, of } from 'rxjs';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { UploadDialog } from './upload-dialog';
import { BatchCard } from './batch-card';
import { Api } from '../../core/api/api.service';
import { BatchOut, DocOut, Paginated, Progress, ServerEvent } from '../../core/api/api.types';
import { Realtime } from '../../core/realtime/realtime.service';

// Zard dialog content is rendered in the CDK overlay container (document.body), not inside the fixture.
const overlayRoot = () => document.body as ReturnType<typeof TestBed.createComponent>['nativeElement'];


class MockRealtime {
  readonly events$ = new Subject<ServerEvent>();
  readonly pending = signal({ files_left: 0, flags_left: 0 });
  readonly connected = signal(true);
  readonly progress = signal<Record<string, Progress>>({});
}

describe('M3 Challenger: UploadDialog Empirical Challenge', () => {
  let fixture: ComponentFixture<UploadDialog>;
  let component: UploadDialog;
  let api: Api;

  const mockFile1 = new File(['dummy 1 content'], 'first_doc.pdf', { type: 'application/pdf', lastModified: 1700000000000 });
  const mockFile2 = new File(['dummy 2 content'], 'second_doc.pdf', { type: 'application/pdf', lastModified: 1700000001000 });
  const mockFile3 = new File(['dummy 3 content'], 'third_doc.pdf', { type: 'application/pdf', lastModified: 1700000002000 });

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [UploadDialog],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    api = TestBed.inject(Api);
    fixture = TestBed.createComponent(UploadDialog);
    component = fixture.componentInstance;
    component.files.set([mockFile1, mockFile2, mockFile3]);
    fixture.detectChanges();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('OP7.1 Preview: generates object URL via URL.createObjectURL and invokes window.open with _blank', () => {
    const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:http://localhost/test-preview');
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);

    const buttons = overlayRoot().querySelectorAll('button[aria-label="Datei in neuem Tab ansehen"], button[aria-label="Preview file in new tab"]');
    expect(buttons.length).toBe(3);

    // Click preview on second file
    buttons[1].click();

    expect(createSpy).toHaveBeenCalledWith(mockFile2);
    expect(openSpy).toHaveBeenCalledWith('blob:http://localhost/test-preview', '_blank');
  });

  it('OP7.1 Pre-upload Rename: pencil button opens inline form with current filename', () => {
    const renameButtons = overlayRoot().querySelectorAll('button[aria-label="Datei umbenennen"], button[aria-label="Rename file"]');
    expect(renameButtons.length).toBe(3);

    renameButtons[0].click();
    fixture.detectChanges();

    expect(component.editingFile()).toBe(mockFile1);
    expect(component.editName()).toBe('first_doc.pdf');

    const input = overlayRoot().querySelector('li form input') as HTMLInputElement;
    expect(input).toBeTruthy();
    expect(input.value).toBe('first_doc.pdf');
  });

  it('OP7.1 Pre-upload Rename: updates file in files signal as a File object and auto-appends .pdf', async () => {
    component.startRename(mockFile1);
    fixture.detectChanges();

    const input = overlayRoot().querySelector('li form input') as HTMLInputElement;
    input.value = 'custom_report';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();

    const form = overlayRoot().querySelector('li form') as HTMLFormElement;
    form.dispatchEvent(new Event('submit'));
    fixture.detectChanges();

    const files = component.files();
    expect(files[0].name).toBe('custom_report.pdf');
    expect(files[0] instanceof File).toBe(true);
    expect(files[0].type).toBe('application/pdf');
    expect(files[0].lastModified).toBe(mockFile1.lastModified);
    // Verify file content preservation
    const content = await files[0].text();
    expect(content).toBe('dummy 1 content');

    // Other files remain untouched
    expect(files[1].name).toBe('second_doc.pdf');
    expect(files[2].name).toBe('third_doc.pdf');
    expect(component.editingFile()).toBeNull();
  });

  it('OP7.1 Pre-upload Rename: does not duplicate .pdf if user supplies .pdf or .PDF', () => {
    // With lowercase .pdf
    component.startRename(mockFile1);
    component.editName.set('already_fine.pdf');
    component.saveRename(mockFile1);
    expect(component.files()[0].name).toBe('already_fine.pdf');

    // With uppercase .PDF
    component.startRename(component.files()[0]);
    component.editName.set('already_fine.PDF');
    component.saveRename(component.files()[0]);
    expect(component.files()[0].name).toBe('already_fine.PDF');
  });

  it('OP7.1 Pre-upload Rename: whitespace-only submission cancels rename without altering filename', () => {
    component.startRename(mockFile1);
    component.editName.set('   ');
    component.saveRename(mockFile1);

    expect(component.editingFile()).toBeNull();
    expect(component.files()[0].name).toBe('first_doc.pdf');
  });

  it('OP7.1 Pre-upload Rename: cancel button (lucideX) cancels editing without closing dialog', () => {
    let closedEmitted = false;
    component.closed.subscribe(() => { closedEmitted = true; });

    component.startRename(mockFile1);
    fixture.detectChanges();

    const cancelBtn = overlayRoot().querySelector('li form button[type="button"]');
    expect(cancelBtn).toBeTruthy();
    cancelBtn.click();
    fixture.detectChanges();

    expect(component.editingFile()).toBeNull();
    expect(component.files()[0].name).toBe('first_doc.pdf');
    expect(closedEmitted).toBe(false);
  });

  it('OP7.1 Pre-upload Rename: Escape key on input cancels rename (and bubbles to host dialog)', () => {
    let closedEmitted = false;
    component.closed.subscribe(() => { closedEmitted = true; });

    component.startRename(mockFile1);
    fixture.detectChanges();

    const input = overlayRoot().querySelector('li form input') as HTMLInputElement;
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
    fixture.detectChanges();

    expect(component.editingFile()).toBeNull();
    // Empirical observation: keydown.escape bubbling hits host listener '(document:keydown.escape)': 'cancel()'
    expect(closedEmitted).toBe(true);
  });

  it('OP7.1 Pre-upload Rename: removing an active editing file resets edit state', () => {
    component.startRename(mockFile2);
    expect(component.editingFile()).toBe(mockFile2);

    component['remove'](mockFile2);
    expect(component.editingFile()).toBeNull();
    expect(component.files().length).toBe(2);
  });

  it('OP7.1 Pre-upload Rename: send() forwards renamed files to api.createBatch', () => {
    component.startRename(mockFile1);
    component.editName.set('final_upload');
    component.saveRename(mockFile1);

    const createBatchSpy = vi.spyOn(api, 'createBatch').mockReturnValue(new Subject());
    component['send']();

    expect(createBatchSpy).toHaveBeenCalled();
    const passedFiles: File[] = createBatchSpy.mock.calls[0][1];
    expect(passedFiles.length).toBe(3);
    expect(passedFiles[0].name).toBe('final_upload.pdf');
    expect(passedFiles[1].name).toBe('second_doc.pdf');
    expect(passedFiles[2].name).toBe('third_doc.pdf');
  });
});

describe('M3 Challenger: BatchCard Empirical Challenge', () => {
  let fixture: ComponentFixture<BatchCard>;
  let component: BatchCard;
  let api: Api;
  let httpMock: HttpTestingController;

  const mockBatch: BatchOut = {
    slug: 'batch-xyz',
    name: 'Original Batch Name',
    created_at: new Date().toISOString(),
    num_docs: 2,
    counts: { queued: 0, processing: 0, review: 1, done: 1, error: 0 },
    open_flags: 0,
  };

  const mockDocs: Paginated<DocOut> = {
    items: [
      {
        slug: 'doc-xyz-1',
        batch_slug: 'batch-xyz',
        filename: 'my_doc.pdf',
        status: 'review',
        num_pages: 2,
        open_flags: 0,
        error: null,
        updated_at: new Date().toISOString(),
        total_cost: 0.005,
      },
      {
        slug: 'doc-xyz-2',
        batch_slug: 'batch-xyz',
        filename: 'another_doc.pdf',
        status: 'done',
        num_pages: 1,
        open_flags: 0,
        error: null,
        updated_at: new Date().toISOString(),
        total_cost: 0.00003,
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

  describe('Batch Renaming (OP7.2)', () => {
    it('pencil icon next to batch title toggles inline form with b.name', () => {
      const pencilBtn = fixture.nativeElement.querySelector('button[aria-label="Batch umbenennen"], button[aria-label="Rename batch"]');
      expect(pencilBtn).toBeTruthy();

      pencilBtn.click();
      fixture.detectChanges();

      expect(component['editingBatch']()).toBe(true);
      expect(component['batchNameInput']()).toBe('Original Batch Name');

      const input = fixture.nativeElement.querySelector('form input') as HTMLInputElement;
      expect(input).toBeTruthy();
      expect(input.value).toBe('Original Batch Name');
    });

    it('submitting calls api.updateBatch and updates name in view', () => {
      component['startBatchRename']();
      fixture.detectChanges();

      const input = fixture.nativeElement.querySelector('form input') as HTMLInputElement;
      input.value = 'Renamed Harvest 2026';
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();

      const updateSpy = vi.spyOn(api, 'updateBatch').mockReturnValue(of({ ...mockBatch, name: 'Renamed Harvest 2026' }));
      const form = fixture.nativeElement.querySelector('form') as HTMLFormElement;
      form.dispatchEvent(new Event('submit'));
      fixture.detectChanges();

      expect(updateSpy).toHaveBeenCalledWith('batch-xyz', { name: 'Renamed Harvest 2026' });
      expect(component['editingBatch']()).toBe(false);
      expect(component['currentBatchName']()).toBe('Renamed Harvest 2026');
      expect(fixture.nativeElement.textContent).toContain('Renamed Harvest 2026');
    });

    it('Escape cancels batch renaming and reverts view', () => {
      component['startBatchRename']();
      fixture.detectChanges();

      const input = fixture.nativeElement.querySelector('form input') as HTMLInputElement;
      input.value = 'Unsaved Name';
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();

      const updateSpy = vi.spyOn(api, 'updateBatch');
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
      fixture.detectChanges();

      expect(component['editingBatch']()).toBe(false);
      expect(updateSpy).not.toHaveBeenCalled();
      expect(component['currentBatchName']()).toBe('Original Batch Name');
      expect(fixture.nativeElement.textContent).toContain('Original Batch Name');
    });

    it('cancel button (lucideX) cancels batch renaming', () => {
      component['startBatchRename']();
      fixture.detectChanges();

      const cancelBtn = fixture.nativeElement.querySelector('form button[type="button"]');
      cancelBtn.click();
      fixture.detectChanges();

      expect(component['editingBatch']()).toBe(false);
      expect(component['currentBatchName']()).toBe('Original Batch Name');
    });

    it('submitting unchanged or empty name cancels without API call', () => {
      const updateSpy = vi.spyOn(api, 'updateBatch');

      // Unchanged
      component['startBatchRename']();
      component['saveBatchRename']();
      expect(component['editingBatch']()).toBe(false);
      expect(updateSpy).not.toHaveBeenCalled();

      // Whitespace
      component['startBatchRename']();
      component['batchNameInput'].set('   ');
      component['saveBatchRename']();
      expect(component['editingBatch']()).toBe(false);
      expect(updateSpy).not.toHaveBeenCalled();
    });
  });

  describe('Document Renaming (OP7.2)', () => {
    async function expandAndLoadDocs() {
      const expandBtn = fixture.nativeElement.querySelector('button.btn-icon');
      expandBtn.click();
      fixture.detectChanges();

      const req = httpMock.expectOne('/api/batches/batch-xyz/docs?page=1&size=10');
      req.flush(mockDocs);
      TestBed.flushEffects();
      await fixture.whenStable();
      fixture.detectChanges();
    }

    it('pencil icon next to doc filename toggles inline form with doc.filename', async () => {
      await expandAndLoadDocs();

      const docPencils = fixture.nativeElement.querySelectorAll('button[aria-label="Datei umbenennen"], button[aria-label="Rename file"]');
      expect(docPencils.length).toBe(2);

      docPencils[0].click();
      fixture.detectChanges();

      expect(component['editingDocSlug']()).toBe('doc-xyz-1');
      expect(component['docNameInput']()).toBe('my_doc.pdf');

      const input = fixture.nativeElement.querySelector('form input.input') as HTMLInputElement;
      expect(input).toBeTruthy();
      expect(input.value).toBe('my_doc.pdf');
    });

    it('submitting calls api.updateDoc, auto-appends .pdf, and reloads document list', async () => {
      await expandAndLoadDocs();

      component['startDocRename'](mockDocs.items[0]);
      fixture.detectChanges();

      const input = fixture.nativeElement.querySelector('form input.input') as HTMLInputElement;
      input.value = 'invoice_march';
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();

      const updateDocSpy = vi.spyOn(api, 'updateDoc').mockReturnValue(of({
        ...mockDocs.items[0],
        filename: 'invoice_march.pdf',
      }));

      const form = fixture.nativeElement.querySelector('form') as HTMLFormElement;
      form.dispatchEvent(new Event('submit'));
      fixture.detectChanges();

      expect(updateDocSpy).toHaveBeenCalledWith('doc-xyz-1', { filename: 'invoice_march.pdf' });
      expect(component['editingDocSlug']()).toBeNull();

      // Expect reload
      const reloadReq = httpMock.expectOne('/api/batches/batch-xyz/docs?page=1&size=10');
      reloadReq.flush({
        ...mockDocs,
        items: [
          { ...mockDocs.items[0], filename: 'invoice_march.pdf' },
          mockDocs.items[1],
        ],
      });
      TestBed.flushEffects();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(fixture.nativeElement.textContent).toContain('invoice_march.pdf');
    });

    it('does not duplicate .pdf if user supplies lowercase .pdf', async () => {
      await expandAndLoadDocs();

      const updateDocSpy = vi.spyOn(api, 'updateDoc').mockReturnValue(of(mockDocs.items[0]));

      component['startDocRename'](mockDocs.items[0]);
      component['docNameInput'].set('renamed_test.pdf');
      component['saveDocRename'](mockDocs.items[0]);
      fixture.detectChanges();

      expect(updateDocSpy).toHaveBeenCalledWith('doc-xyz-1', { filename: 'renamed_test.pdf' });
      const reloadReq = httpMock.expectOne('/api/batches/batch-xyz/docs?page=1&size=10');
      reloadReq.flush(mockDocs);
    });

    it('does not duplicate .pdf if user supplies uppercase .PDF', async () => {
      await expandAndLoadDocs();

      const updateDocSpy = vi.spyOn(api, 'updateDoc').mockReturnValue(of(mockDocs.items[0]));

      component['startDocRename'](mockDocs.items[0]);
      component['docNameInput'].set('renamed_test.PDF');
      component['saveDocRename'](mockDocs.items[0]);
      fixture.detectChanges();

      expect(updateDocSpy).toHaveBeenCalledWith('doc-xyz-1', { filename: 'renamed_test.PDF' });
      const reloadReq = httpMock.expectOne('/api/batches/batch-xyz/docs?page=1&size=10');
      reloadReq.flush(mockDocs);
    });

    it('Escape cancels document renaming and leaves filename unchanged', async () => {
      await expandAndLoadDocs();

      component['startDocRename'](mockDocs.items[0]);
      fixture.detectChanges();

      const input = fixture.nativeElement.querySelector('form input.input') as HTMLInputElement;
      input.value = 'cancelled_name';
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();

      const updateDocSpy = vi.spyOn(api, 'updateDoc');
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
      fixture.detectChanges();

      expect(component['editingDocSlug']()).toBeNull();
      expect(updateDocSpy).not.toHaveBeenCalled();
      expect(fixture.nativeElement.textContent).toContain('my_doc.pdf');
    });

    it('cancel button (lucideX) cancels document renaming', async () => {
      await expandAndLoadDocs();

      component['startDocRename'](mockDocs.items[0]);
      fixture.detectChanges();

      const cancelBtn = fixture.nativeElement.querySelector('form button[type="button"]');
      cancelBtn.click();
      fixture.detectChanges();

      expect(component['editingDocSlug']()).toBeNull();
      expect(fixture.nativeElement.textContent).toContain('my_doc.pdf');
    });

    it('submitting unchanged or empty doc filename cancels without API call', async () => {
      await expandAndLoadDocs();

      const updateDocSpy = vi.spyOn(api, 'updateDoc');

      // Unchanged
      component['startDocRename'](mockDocs.items[0]);
      component['saveDocRename'](mockDocs.items[0]);
      expect(component['editingDocSlug']()).toBeNull();
      expect(updateDocSpy).not.toHaveBeenCalled();

      // Empty
      component['startDocRename'](mockDocs.items[0]);
      component['docNameInput'].set('   ');
      component['saveDocRename'](mockDocs.items[0]);
      expect(component['editingDocSlug']()).toBeNull();
      expect(updateDocSpy).not.toHaveBeenCalled();
    });
  });
});
