import { HttpClient, HttpEvent, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  ArtifactState, BatchOut, DocDetail, DocOut, FlagOut, PageRows, Paginated, ResolveOut, ReviewQueue, RowUpdate,
} from './api.types';

const API = '/api';

/** Thin typed wrapper around the backend. Components use httpResource() with the URL helpers. */
@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);

  // ---- URLs (for httpResource / <img>) ----
  batchesUrl = (page: number, size: number) => `${API}/batches?page=${page}&size=${size}`;
  batchDocsUrl = (slug: string, page: number, size: number) => `${API}/batches/${slug}/docs?page=${page}&size=${size}`;
  docUrl = (slug: string) => `${API}/docs/${slug}`;
  flagsUrl = (slug: string) => `${API}/docs/${slug}/flags?open=true`;
  rowsUrl = (slug: string, page: number) => `${API}/docs/${slug}/page/${page}/rows`;
  artifactUrl = (slug: string, state: ArtifactState, page: number) => `${API}/docs/${slug}/artifacts/${state}/page/${page}`;

  reviewUrl(scope: { batch?: string; doc?: string }): string {
    let params = new HttpParams();
    if (scope.doc) params = params.set('doc', scope.doc);
    else if (scope.batch) params = params.set('batch', scope.batch);
    const q = params.toString();
    return `${API}/review${q ? '?' + q : ''}`;
  }

  // ---- calls ----
  batches(page: number, size: number) { return this.http.get<Paginated<BatchOut>>(this.batchesUrl(page, size)); }
  batchDocs(slug: string, page: number, size: number) {
    return this.http.get<Paginated<DocOut>>(this.batchDocsUrl(slug, page, size));
  }
  doc(slug: string) { return this.http.get<DocDetail>(this.docUrl(slug)); }
  flags(slug: string) { return this.http.get<FlagOut[]>(this.flagsUrl(slug)); }
  rows(slug: string, page: number) { return this.http.get<PageRows>(this.rowsUrl(slug, page)); }
  review(scope: { batch?: string; doc?: string }) { return this.http.get<ReviewQueue>(this.reviewUrl(scope)); }

  resolveRow(slug: string, page: number, idx: number, body: RowUpdate) {
    return this.http.post<ResolveOut>(`${API}/docs/${slug}/page/${page}/rows/${idx}/resolve`, body);
  }

  createBatch(name: string, files: File[]): Observable<HttpEvent<BatchOut>> {
    const form = new FormData();
    form.append('name', name);
    for (const f of files) form.append('files', f, f.name);
    return this.http.post<BatchOut>(`${API}/batches`, form, { reportProgress: true, observe: 'events' });
  }
}
