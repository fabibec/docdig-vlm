import { TestBed } from '@angular/core/testing';
import { HttpEventType, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Observable } from 'rxjs';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import { Api } from './api.service';

describe('Api', () => {
  let api: Api;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    api = TestBed.inject(Api);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  describe('URL helpers', () => {
    it('builds the read URLs', () => {
      expect(api.batchesUrl(2, 10)).toBe('/api/batches?page=2&size=10');
      expect(api.batchDocsUrl('b1', 1, 10)).toBe('/api/batches/b1/docs?page=1&size=10');
      expect(api.docUrl('d1')).toBe('/api/docs/d1');
      expect(api.flagsUrl('d1')).toBe('/api/docs/d1/flags?open=true');
      expect(api.allFlagsUrl('d1')).toBe('/api/docs/d1/flags');
      expect(api.rowsUrl('d1', 3)).toBe('/api/docs/d1/page/3/rows');
      expect(api.artifactUrl('d1', 'cropped', 2)).toBe('/api/docs/d1/artifacts/cropped/page/2');
      expect(api.docFileUrl('d1')).toBe('/api/docs/d1/file');
    });

    it('builds the review URL for all, batch and file scope (file wins over batch)', () => {
      expect(api.reviewUrl({})).toBe('/api/review');
      expect(api.reviewUrl({ batch: 'b1' })).toBe('/api/review?batch=b1');
      expect(api.reviewUrl({ doc: 'd1' })).toBe('/api/review?doc=d1');
      expect(api.reviewUrl({ batch: 'b1', doc: 'd1' })).toBe('/api/review?doc=d1');
    });
  });

  describe('read calls', () => {
    const cases: [string, () => Observable<unknown>, string][] = [
      ['batches', () => api.batches(1, 10), '/api/batches?page=1&size=10'],
      ['batchDocs', () => api.batchDocs('b1', 2, 5), '/api/batches/b1/docs?page=2&size=5'],
      ['doc', () => api.doc('d1'), '/api/docs/d1'],
      ['flags', () => api.flags('d1'), '/api/docs/d1/flags?open=true'],
      ['rows', () => api.rows('d1', 4), '/api/docs/d1/page/4/rows'],
      ['review', () => api.review({ batch: 'b1' }), '/api/review?batch=b1'],
    ];
    for (const [name, call, url] of cases) {
      it(`${name} sends GET ${url}`, () => {
        let result: unknown;
        call().subscribe((r) => (result = r));
        const req = http.expectOne(url);
        expect(req.request.method).toBe('GET');
        req.flush({ ok: name });
        expect(result).toEqual({ ok: name });
      });
    }
  });

  describe('write calls', () => {
    const cases: [string, () => Observable<unknown>, string, string, unknown][] = [
      ['resolveRow', () => api.resolveRow('d1', 2, 7, { values: { species: 'Amsel' } }),
        'POST', '/api/docs/d1/page/2/rows/7/resolve', { values: { species: 'Amsel' } }],
      ['resolvePage', () => api.resolvePage('d1', 2, { rows: { 3: { sex: 'M' } } }),
        'POST', '/api/docs/d1/page/2/resolve', { rows: { 3: { sex: 'M' } } }],
      ['createFlag', () => api.createFlag('d1', { page: 1, row: 4, column: null, message: 'check' }),
        'POST', '/api/docs/d1/flags', { page: 1, row: 4, column: null, message: 'check' }],
      ['deleteFlag', () => api.deleteFlag('d1', 42), 'DELETE', '/api/docs/d1/flags/42', null],
      ['reopenFlags', () => api.reopenFlags('d1', [1, 2]), 'POST', '/api/docs/d1/flags/reopen', { ids: [1, 2] }],
      ['deleteBatch', () => api.deleteBatch('b1'), 'DELETE', '/api/batches/b1', null],
      ['updateBatch', () => api.updateBatch('b1', { name: 'New' }), 'PATCH', '/api/batches/b1', { name: 'New' }],
      ['updateDoc', () => api.updateDoc('d1', { filename: 'x.pdf' }), 'PATCH', '/api/docs/d1', { filename: 'x.pdf' }],
    ];
    for (const [name, call, method, url, body] of cases) {
      it(`${name} sends ${method} ${url}`, () => {
        let done = false;
        call().subscribe(() => (done = true));
        const req = http.expectOne(url);
        expect(req.request.method).toBe(method);
        expect(req.request.body).toEqual(body);
        req.flush({});
        expect(done).toBe(true);
      });
    }

    it('createBatch posts a multipart form with name and files and reports upload progress', () => {
      const a = new File(['a'], 'a.pdf', { type: 'application/pdf' });
      const b = new File(['bb'], 'b.pdf', { type: 'application/pdf' });
      const types: HttpEventType[] = [];
      api.createBatch('Spring', [a, b]).subscribe((ev) => types.push(ev.type));

      const req = http.expectOne('/api/batches');
      expect(req.request.method).toBe('POST');
      expect(req.request.reportProgress).toBe(true);
      const form = req.request.body as FormData;
      expect(form).toBeInstanceOf(FormData);
      expect(form.get('name')).toBe('Spring');
      expect((form.getAll('files') as File[]).map((f) => f.name)).toEqual(['a.pdf', 'b.pdf']);

      req.event({ type: HttpEventType.UploadProgress, loaded: 1, total: 3 });
      req.flush({ slug: 'spring' });
      expect(types).toContain(HttpEventType.UploadProgress);
      expect(types).toContain(HttpEventType.Response);
    });
  });
});
