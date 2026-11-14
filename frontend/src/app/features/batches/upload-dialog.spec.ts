import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { UploadDialog } from './upload-dialog';

// Zard dialog content is rendered in the CDK overlay container (document.body), not inside the fixture.
const overlayRoot = () => document.body as ReturnType<typeof TestBed.createComponent>['nativeElement'];


describe('UploadDialog', () => {
  let fixture: ComponentFixture<UploadDialog>;
  let component: UploadDialog;

  const mockFile1 = new File(['dummy content 1'], 'report.pdf', { type: 'application/pdf' });
  const mockFile2 = new File(['dummy content 2'], 'invoice.pdf', { type: 'application/pdf' });

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [UploadDialog],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(UploadDialog);
    component = fixture.componentInstance;
    component.files.set([mockFile1, mockFile2]);
    fixture.detectChanges();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('File Preview (OP7.1)', () => {
    it('opens blob URL in a new tab via window.open', () => {
      const createObjectURLSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:http://localhost/test-uuid');
      const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);

      const previewBtn = overlayRoot().querySelector('button[aria-label="Datei in neuem Tab ansehen"], button[aria-label="Preview file in new tab"]');
      expect(previewBtn).toBeTruthy();
      previewBtn.click();

      expect(createObjectURLSpy).toHaveBeenCalledWith(mockFile1);
      expect(openSpy).toHaveBeenCalledWith('blob:http://localhost/test-uuid', '_blank');
    });
  });

  describe('Pre-Upload Renaming (OP7.1)', () => {
    it('allows renaming a file prior to upload and appends .pdf if omitted', () => {
      const renameBtn = overlayRoot().querySelector('button[aria-label="Datei umbenennen"], button[aria-label="Rename file"]');
      expect(renameBtn).toBeTruthy();
      renameBtn.click();
      fixture.detectChanges();

      expect(component.editingFile()).toBe(mockFile1);

      const input = overlayRoot().querySelector('li form input');
      expect(input).toBeTruthy();
      expect(input.value).toBe('report.pdf');

      input.value = 'annual_report';
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();

      const form = overlayRoot().querySelector('li form');
      form.dispatchEvent(new Event('submit'));
      fixture.detectChanges();

      expect(component.editingFile()).toBeNull();
      const updatedFiles = component.files();
      expect(updatedFiles[0].name).toBe('annual_report.pdf');
    });

    it('cancels renaming without modifying file list', () => {
      const renameBtn = overlayRoot().querySelector('button[aria-label="Datei umbenennen"], button[aria-label="Rename file"]');
      renameBtn.click();
      fixture.detectChanges();

      expect(component.editingFile()).toBe(mockFile1);

      const cancelBtn = overlayRoot().querySelector('li form button[type="button"]');
      expect(cancelBtn).toBeTruthy();
      cancelBtn.click();
      fixture.detectChanges();

      expect(component.editingFile()).toBeNull();
      expect(component.files()[0].name).toBe('report.pdf');
    });
  });
});
