import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { UploadDialog } from './upload-dialog';
import { I18n } from '../../core/i18n/i18n';

// Zard dialog content is rendered in the CDK overlay container (document.body), not inside the fixture.
const overlayRoot = () => document.body;

function pdf(name: string, content = `content of ${name}`, lastModified = 1700000000000): File {
  return new File([content], name, { type: 'application/pdf', lastModified });
}

/** FileList stand-in for drop / <input type=file> change events. */
function fileList(files: File[]): FileList {
  return Object.assign([...files], { item: (i: number) => files[i] ?? null }) as unknown as FileList;
}

describe('UploadDialog', () => {
  let fixture: ComponentFixture<UploadDialog>;
  let component: UploadDialog;
  let httpMock: HttpTestingController;
  let t: I18n['t'];

  const file1 = pdf('first_doc.pdf', 'dummy 1 content', 1700000000000);
  const file2 = pdf('second_doc.pdf', 'dummy 2 content', 1700000001000);
  const file3 = pdf('third_doc.pdf', 'dummy 3 content', 1700000002000);

  const q = <T extends Element = HTMLElement>(sel: string) => overlayRoot().querySelector<T>(sel);
  const qa = <T extends Element = HTMLElement>(sel: string) => overlayRoot().querySelectorAll<T>(sel);
  const byLabel = (key: string) => qa<HTMLButtonElement>(`button[aria-label="${t(key)}"]`);
  const names = () => [...qa('li > span.truncate')].map((s) => s.textContent?.trim());
  const sendButton = () => [...qa<HTMLButtonElement>('z-dialog-footer button')].find((b) => b.textContent?.includes(t('upload.send', { n: component.files().length })))!;

  /** Drops files onto the drop zone. */
  function drop(files: File[]) {
    const zone = q('.border-dashed')!;
    const ev = new Event('drop', { bubbles: true, cancelable: true });
    Object.defineProperty(ev, 'dataTransfer', { value: { files: fileList(files) } });
    zone.dispatchEvent(ev);
    fixture.detectChanges();
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [UploadDialog],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();

    const i18n = TestBed.inject(I18n);
    i18n.lang.set('en');
    t = i18n.t.bind(i18n);
    httpMock = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(UploadDialog);
    component = fixture.componentInstance;
    component.files.set([file1, file2, file3]);
    fixture.detectChanges();
    await fixture.whenStable();
  });

  afterEach(() => {
    httpMock.verify();
    vi.restoreAllMocks();
  });

  describe('file selection', () => {
    it('lists the selected files with their sizes', () => {
      expect(names()).toEqual(['first_doc.pdf', 'second_doc.pdf', 'third_doc.pdf']);
      expect(q('li')!.textContent).toContain('15 B');
    });

    it('adds dropped PDFs sorted by name and silently drops non-PDF files', () => {
      component.files.set([]);
      drop([pdf('scan10.pdf'), new File(['x'], 'notes.txt', { type: 'text/plain' }), pdf('scan2.PDF'), pdf('scan1.pdf')]);
      expect(names()).toEqual(['scan1.pdf', 'scan2.PDF', 'scan10.pdf']);
      expect(overlayRoot().textContent).not.toContain('notes.txt');
    });

    it('skips duplicate names with a notice', () => {
      drop([pdf('first_doc.pdf'), pdf('new.pdf'), pdf('new.pdf')]);
      expect(component.files().map((f) => f.name)).toEqual(['first_doc.pdf', 'new.pdf', 'second_doc.pdf', 'third_doc.pdf']);
      expect(overlayRoot().textContent).toContain(t('upload.duplicates', { names: 'first_doc.pdf, new.pdf' }));
    });

    it('removes a file with the ✕ button', () => {
      byLabel('upload.remove')[1].click();
      fixture.detectChanges();
      expect(names()).toEqual(['first_doc.pdf', 'third_doc.pdf']);
    });

    it('paginates the preview with 10 files per page', () => {
      component.files.set(Array.from({ length: 12 }, (_, i) => pdf(`f${String(i + 1).padStart(2, '0')}.pdf`)));
      fixture.detectChanges();
      expect(names().length).toBe(10);
      expect(overlayRoot().textContent).toContain(t('pager.range', { from: 1, to: 10, total: 12 }));

      q<HTMLButtonElement>(`button[aria-label="${t('pager.next')}"]`)!.click();
      fixture.detectChanges();
      expect(names()).toEqual(['f11.pdf', 'f12.pdf']);
    });

    it('goes back a page when the last file of the last page is removed', () => {
      component.files.set(Array.from({ length: 11 }, (_, i) => pdf(`f${String(i + 1).padStart(2, '0')}.pdf`)));
      fixture.detectChanges();
      q<HTMLButtonElement>(`button[aria-label="${t('pager.next')}"]`)!.click();
      fixture.detectChanges();
      expect(names()).toEqual(['f11.pdf']);

      byLabel('upload.remove')[0].click();
      fixture.detectChanges();
      expect(names().length).toBe(10);
    });

    it('shows a placeholder and disables upload without files', () => {
      component.files.set([]);
      fixture.detectChanges();
      expect(overlayRoot().textContent).toContain(t('upload.noFiles'));
      expect(sendButton().disabled).toBe(true);
    });
  });

  describe('preview', () => {
    it('opens the file as blob URL in a new tab', () => {
      const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:http://localhost/test-preview');
      const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);

      const previewButtons = byLabel('upload.preview');
      expect(previewButtons.length).toBe(3);
      previewButtons[1].click();

      expect(createSpy).toHaveBeenCalledWith(file2);
      expect(openSpy).toHaveBeenCalledWith('blob:http://localhost/test-preview', '_blank');
    });
  });

  describe('rename before upload', () => {
    function startRename(index = 0): HTMLInputElement {
      byLabel('upload.rename')[index].click();
      fixture.detectChanges();
      return q<HTMLInputElement>('li form input')!;
    }

    function submit(value: string) {
      const input = q<HTMLInputElement>('li form input')!;
      input.value = value;
      input.dispatchEvent(new Event('input'));
      q('li form')!.dispatchEvent(new Event('submit'));
      fixture.detectChanges();
    }

    it('opens an inline form prefilled with the current name', () => {
      expect(byLabel('upload.rename').length).toBe(3);
      const input = startRename();
      expect(component.editingFile()).toBe(file1);
      expect(input.value).toBe('first_doc.pdf');
    });

    it('replaces the file with a renamed copy and appends .pdf', async () => {
      startRename();
      submit('custom_report');

      const files = component.files();
      expect(files[0].name).toBe('custom_report.pdf');
      expect(files[0]).toBeInstanceOf(File);
      expect(files[0].type).toBe('application/pdf');
      expect(files[0].lastModified).toBe(file1.lastModified);
      expect(await files[0].text()).toBe('dummy 1 content');
      expect(files.slice(1)).toEqual([file2, file3]);
      expect(component.editingFile()).toBeNull();
      expect(q('li form')).toBeNull();
      expect(names()[0]).toBe('custom_report.pdf');
    });

    it('does not append .pdf twice (any case)', () => {
      startRename();
      submit('already_fine.pdf');
      expect(component.files()[0].name).toBe('already_fine.pdf');

      startRename();
      submit('already_fine.PDF');
      expect(component.files()[0].name).toBe('already_fine.PDF');
    });

    it('keeps the name when the input is blank', () => {
      startRename();
      submit('   ');
      expect(component.editingFile()).toBeNull();
      expect(component.files()[0]).toBe(file1);
    });

    it('cancels with the ✕ button without closing the dialog', () => {
      const closed = vi.fn();
      component.closed.subscribe(closed);
      startRename();
      q<HTMLButtonElement>('li form button[type="button"]')!.click();
      fixture.detectChanges();

      expect(component.editingFile()).toBeNull();
      expect(component.files()[0]).toBe(file1);
      expect(closed).not.toHaveBeenCalled();
    });

    it('cancels with Esc without closing the dialog', () => {
      const closed = vi.fn();
      component.closed.subscribe(closed);
      const input = startRename();
      input.value = 'unsaved';
      input.dispatchEvent(new Event('input'));
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      fixture.detectChanges();

      expect(component.editingFile()).toBeNull();
      expect(component.files()[0].name).toBe('first_doc.pdf');
      expect(closed).not.toHaveBeenCalled();
    });

    it('resets the edit state when the edited file is removed', () => {
      // While editing, the row's own remove button is replaced by the form, so call remove() directly.
      startRename(1);
      expect(component.editingFile()).toBe(file2);

      (component as unknown as { remove(f: File): void }).remove(file2);
      fixture.detectChanges();
      expect(component.editingFile()).toBeNull();
      expect(component.files()).toEqual([file1, file3]);
    });

    it('uploads the renamed files', () => {
      startRename();
      submit('final_upload');
      sendButton().click();
      fixture.detectChanges();

      const req = httpMock.expectOne('/api/batches');
      const form = req.request.body as FormData;
      expect((form.getAll('files') as File[]).map((f) => f.name)).toEqual(['final_upload.pdf', 'second_doc.pdf', 'third_doc.pdf']);
      req.flush({});
    });
  });

  describe('upload', () => {
    it('posts the name and all files as multipart form and emits created', () => {
      const created = vi.fn();
      component.created.subscribe(created);
      const nameInput = q<HTMLInputElement>('#batch-name')!;
      expect(nameInput.value).toMatch(/^Batch /);
      nameInput.value = '  Spring 2026  ';
      nameInput.dispatchEvent(new Event('input'));
      fixture.detectChanges();

      sendButton().click();
      fixture.detectChanges();

      const req = httpMock.expectOne('/api/batches');
      expect(req.request.method).toBe('POST');
      expect(req.request.reportProgress).toBe(true);
      const form = req.request.body as FormData;
      expect(form.get('name')).toBe('Spring 2026');
      expect(form.getAll('files')).toHaveLength(3);
      req.flush({ slug: 'spring-2026' });
      expect(created).toHaveBeenCalledOnce();
    });

    it('falls back to "Batch" for an empty name', () => {
      const nameInput = q<HTMLInputElement>('#batch-name')!;
      nameInput.value = ' ';
      nameInput.dispatchEvent(new Event('input'));
      sendButton().click();
      const req = httpMock.expectOne('/api/batches');
      expect((req.request.body as FormData).get('name')).toBe('Batch');
      req.flush({});
    });

    it('shows the upload progress in % and blocks closing meanwhile', () => {
      const closed = vi.fn();
      component.closed.subscribe(closed);
      sendButton().click();
      const req = httpMock.expectOne('/api/batches');
      req.event({ type: 1 /* HttpEventType.UploadProgress */, loaded: 42, total: 100 });
      fixture.detectChanges();
      expect(overlayRoot().textContent).toContain(t('upload.uploading', { pct: 42 }));

      [...qa<HTMLButtonElement>('z-dialog-footer button')].find((b) => b.textContent?.trim() === t('upload.cancel'))!.click();
      expect(closed).not.toHaveBeenCalled();
      req.flush({});
    });

    it('shows the duplicate names returned by the server (409)', () => {
      sendButton().click();
      httpMock.expectOne('/api/batches').flush(
        { detail: 'duplicate_files', filenames: ['a.pdf', 'b.pdf'] }, { status: 409, statusText: 'Conflict' },
      );
      fixture.detectChanges();
      expect(overlayRoot().textContent).toContain(t('upload.serverDuplicates', { names: 'a.pdf, b.pdf' }));
      expect(sendButton().disabled).toBe(false);
    });

    it('shows other server errors', () => {
      sendButton().click();
      httpMock.expectOne('/api/batches').flush({ detail: 'disk full' }, { status: 500, statusText: 'Server Error' });
      fixture.detectChanges();
      expect(overlayRoot().textContent).toContain(t('upload.failed', { msg: 'disk full' }));
    });

    it('emits closed on cancel', () => {
      const closed = vi.fn();
      component.closed.subscribe(closed);
      [...qa<HTMLButtonElement>('z-dialog-footer button')].find((b) => b.textContent?.trim() === t('upload.cancel'))!.click();
      expect(closed).toHaveBeenCalledOnce();
    });
  });
});
