import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import { DocumentPage } from './document-page';
import { ColumnKey, COLUMNS, DocDetail, PageRows } from '../../core/api/api.types';
import { I18n } from '../../core/i18n/i18n';

function detail(over: Partial<DocDetail> = {}): DocDetail {
  return {
    slug: 'd1', batch_slug: 'b1', batch_name: 'Spring 2026', filename: 'scan1.pdf', status: 'done', num_pages: 2,
    open_flags: 0, error: null, updated_at: '', artifact_url: '',
    pages: [{ num: 1, open_flags: 0, page_confidence: 0.9 }, { num: 2, open_flags: 0, page_confidence: 0.8 }],
    ...over,
  };
}

function rows(num: number): PageRows {
  const values = Object.fromEntries(COLUMNS.map((c) => [c, ''])) as Record<ColumnKey, string>;
  const column_x = Object.fromEntries(COLUMNS.map((c, i) => [c, [i * 10, i * 10 + 10]])) as Record<ColumnKey, [number, number]>;
  return {
    num, width: 100, height: 100, columns: COLUMNS, column_x,
    rows: [{ idx: 1, y0: 0, y1: 10, values: { ...values, species: `Amsel p${num}` }, corrected: [] }],
  };
}

describe('DocumentPage', () => {
  let fixture: ComponentFixture<DocumentPage>;
  let http: HttpTestingController;
  let t: I18n['t'];
  const el = () => fixture.nativeElement as HTMLElement;

  async function render() {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  async function load(d: DocDetail) {
    fixture.componentRef.setInput('docSlug', d.slug);
    fixture.detectChanges();
    http.expectOne(`/api/docs/${d.slug}`).flush(d);
    http.expectOne(`/api/docs/${d.slug}/page/1/rows`).flush(rows(1));
    await render();
  }

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [DocumentPage],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    const i18n = TestBed.inject(I18n);
    i18n.lang.set('en');
    t = i18n.t.bind(i18n);
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(DocumentPage);
  });

  afterEach(() => http.verify());

  it('shows filename, batch, page count and status in the header', async () => {
    await load(detail());
    expect(el().querySelector('h1')!.textContent).toBe('scan1.pdf');
    const text = el().textContent!;
    expect(text).toContain(`${t('doc.batch')}: Spring 2026`);
    expect(text).toContain(t('doc.pages', { n: 2 }));
    expect(el().querySelector('app-status-badge')!.textContent).toContain(t('status.done'));
    expect(el().querySelector('a[href="/"]')).toBeTruthy();
  });

  it('links Annotate to the review page of the file', async () => {
    await load(detail());
    const annotate = [...el().querySelectorAll('a')].find((a) => a.textContent?.trim() === t('doc.annotate'))!;
    expect(annotate.getAttribute('href')).toBe('/review/file/d1');
  });

  it('also offers Annotate for files in review', async () => {
    await load(detail({ status: 'review' }));
    expect(el().textContent).toContain(t('doc.annotate'));
  });

  it('hides Annotate for files that are not processed yet', async () => {
    await load(detail({ status: 'processing' }));
    expect(el().textContent).not.toContain(t('doc.annotate'));
  });

  it('starts the viewer on the result step with the rows of page 1', async () => {
    await load(detail());
    expect(el().querySelector('table')).toBeTruthy();
    expect(el().querySelector('tbody')!.textContent).toContain('Amsel p1');
  });

  it('loads the rows of the page picked in the viewer', async () => {
    await load(detail());
    const select = el().querySelector<HTMLSelectElement>('app-pipeline-viewer select')!;
    select.value = '2';
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    http.expectOne('/api/docs/d1/page/2/rows').flush(rows(2));
    await render();
    expect(el().querySelector('tbody')!.textContent).toContain('Amsel p2');
  });

  it('shows "not found" when the document cannot be loaded', async () => {
    fixture.componentRef.setInput('docSlug', 'missing');
    fixture.detectChanges();
    http.expectOne('/api/docs/missing').flush({ detail: 'not found' }, { status: 404, statusText: 'Not Found' });
    http.expectOne('/api/docs/missing/page/1/rows').flush({ detail: 'not found' }, { status: 404, statusText: 'Not Found' });
    await render();
    expect(el().textContent).toContain(t('doc.notFound'));
  });
});
