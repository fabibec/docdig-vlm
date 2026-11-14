import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';

import { PageDraft, SplitReview } from './split-review';
import { ColumnKey, COLUMNS, FlagOut, PageRows } from '../../core/api/api.types';
import { I18n } from '../../core/i18n/i18n';

function flag(over: Partial<FlagOut>): FlagOut {
  return {
    id: 1, page: 1, row: 1, column: 'species', source: 'llm', severity: 'warning', code: null, params: {},
    message: 'unclear', suggestion: null, confidence: null, resolved: false, ...over,
  };
}

function rows(num: number, corrected: ColumnKey[] = []): PageRows {
  const empty = Object.fromEntries(COLUMNS.map((c) => [c, ''])) as Record<ColumnKey, string>;
  const column_x = Object.fromEntries(COLUMNS.map((c, i) => [c, [i * 10, i * 10 + 10]])) as Record<ColumnKey, [number, number]>;
  return {
    num, width: 90, height: 30, columns: COLUMNS, column_x,
    rows: [
      { idx: 1, y0: 0, y1: 10, values: { ...empty, species: 'Amsl', weight_g: '80' }, corrected: [] },
      { idx: 2, y0: 10, y1: 20, values: { ...empty, species: 'Kohlmeise', sex: 'M' }, corrected },
      { idx: 3, y0: 20, y1: 30, values: { ...empty, species: 'Blaumeise' }, corrected: [] },
    ],
  };
}

describe('SplitReview', () => {
  let fixture: ComponentFixture<SplitReview>;
  let split: SplitReview;
  let t: I18n['t'];
  let saved: ReturnType<typeof vi.fn<(d: PageDraft) => void>>;

  const el = () => fixture.nativeElement as HTMLElement;
  const cell = (row: number, col: ColumnKey) => el().querySelector<HTMLInputElement>(`#split-cell-${row}-${col}`)!;
  const td = (row: number, col: ColumnKey) => cell(row, col).closest('td')!;
  const buttonByText = (text: string) => [...el().querySelectorAll('button')].find((b) => b.textContent?.trim() === text);
  const saveButton = () => buttonByText(t('review.save'))!;

  const species = flag({ id: 10, row: 1, column: 'species', suggestion: 'Amsel', confidence: 0.4, params: { value: 'Amsl' } });
  const weight = flag({ id: 11, row: 1, column: 'weight_g', source: 'rule', code: 'weight_out_of_range', params: { value: '80', min: 5, max: 40 } });
  const done = flag({ id: 12, row: 3, column: 'species', resolved: true });
  const otherPage = flag({ id: 13, page: 2, row: 1, column: 'sex' });

  function type(input: HTMLInputElement, value: string) {
    input.value = value;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  }

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [SplitReview] });
    const i18n = TestBed.inject(I18n);
    i18n.lang.set('en');
    t = i18n.t.bind(i18n);
    fixture = TestBed.createComponent(SplitReview);
    split = fixture.componentInstance;
    fixture.componentRef.setInput('slug', 'd1');
    fixture.componentRef.setInput('pages', [{ num: 1, open_flags: 2, page_confidence: null }, { num: 2, open_flags: 1, page_confidence: null }]);
    fixture.componentRef.setInput('rows', rows(1, ['sex']));
    fixture.componentRef.setInput('flags', [species, weight, done, otherPage]);
    fixture.componentRef.setInput('page', 1);
    saved = vi.fn<(d: PageDraft) => void>();
    split.savePage.subscribe(saved);
    fixture.detectChanges();
  });

  it('shows the cropped image of the current page and an editable table', () => {
    expect(el().querySelector('img')!.getAttribute('src')).toBe('/api/docs/d1/artifacts/cropped/page/1');
    expect(cell(1, 'species').value).toBe('Amsl');
    expect(cell(2, 'species').value).toBe('Kohlmeise');
  });

  it('colours open flags amber, resolved flags green and corrected cells blue', () => {
    expect(td(1, 'species').className).toContain('bg-amber-100');
    expect(td(1, 'weight_g').className).toContain('bg-amber-100');
    expect(td(3, 'species').className).toContain('bg-emerald-100');
    expect(td(2, 'sex').className).toContain('bg-sky-100');
    expect(td(2, 'species').className).not.toMatch(/bg-(amber|emerald|sky)-100/);
    // flags of other pages are ignored
    expect(td(1, 'sex').className).not.toContain('bg-amber-100');
  });

  it('shows the flag details in the cell tooltip', () => {
    const tip = td(1, 'species').querySelector('.absolute')!;
    expect(tip.textContent).toContain('unclear');
    expect(tip.textContent).toContain(t('review.read', { value: 'Amsl' }));
    expect(tip.textContent).toContain(t('review.confidence', { pct: 40 }));
    expect(td(1, 'weight_g').querySelector('.absolute')!.textContent)
      .toContain(t('flag.weight_out_of_range', { value: '80', min: 5, max: 40 }));
  });

  describe('Save & next', () => {
    it('warns when flags on the page are still unresolved', () => {
      saveButton().click();
      fixture.detectChanges();
      expect(saved).not.toHaveBeenCalled();
      expect(el().textContent).toContain(t('review.unresolvedWarn', { n: 2 }));
    });

    it('"Save anyway" saves the whole page', () => {
      type(cell(2, 'sex'), 'F');
      saveButton().click();
      fixture.detectChanges();
      buttonByText(t('review.saveAnyway'))!.click();
      fixture.detectChanges();
      expect(saved).toHaveBeenCalledWith({ 2: { sex: 'F' } });
      expect(el().textContent).not.toContain(t('review.saveAnyway'));
    });

    it('cancelling the warning does not save', () => {
      saveButton().click();
      fixture.detectChanges();
      buttonByText(t('upload.cancel'))!.click();
      fixture.detectChanges();
      expect(saved).not.toHaveBeenCalled();
      expect(el().textContent).not.toContain(t('review.saveAnyway'));
    });

    it('saves directly once every flag is resolved by editing, resolve button or suggestion', () => {
      // suggestion
      [...td(1, 'species').querySelectorAll('button')].find((b) => b.textContent?.includes('„Amsel“'))!.click();
      fixture.detectChanges();
      expect(cell(1, 'species').value).toBe('Amsel');
      expect(td(1, 'species').className).toContain('bg-emerald-100');

      // resolve button
      td(1, 'weight_g').querySelector<HTMLButtonElement>(`button[aria-label="${t('review.resolveCell')}"]`)!.click();
      fixture.detectChanges();
      expect(td(1, 'weight_g').className).toContain('bg-emerald-100');

      saveButton().click();
      fixture.detectChanges();
      expect(saved).toHaveBeenCalledWith({ 1: { species: 'Amsel' } });
      expect(el().textContent).not.toContain(t('review.saveAnyway'));
    });

    it('editing a flagged cell resolves it; other edits turn blue', () => {
      type(cell(1, 'species'), 'Amsel');
      type(cell(1, 'weight_g'), '8.0');
      type(cell(3, 'ring_no'), 'AB123');
      expect(td(1, 'species').className).toContain('bg-emerald-100');
      expect(td(3, 'ring_no').className).toContain('bg-sky-100');

      saveButton().click();
      expect(saved).toHaveBeenCalledWith({ 1: { species: 'Amsel', weight_g: '8.0' }, 3: { ring_no: 'AB123' } });
    });

    it('Enter on a flagged cell marks it resolved', () => {
      cell(1, 'species').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
      cell(1, 'weight_g').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
      fixture.detectChanges();
      saveButton().click();
      expect(saved).toHaveBeenCalledWith({});
    });

    it('a whole-row flag is resolved by resolving any cell of the row', () => {
      fixture.componentRef.setInput('flags', [flag({ id: 20, row: 2, column: null, source: 'user', message: 'check row' })]);
      fixture.detectChanges();
      saveButton().click();
      fixture.detectChanges();
      expect(el().textContent).toContain(t('review.unresolvedWarn', { n: 1 }));

      // resolving the last open flag also hides the warning
      type(cell(2, 'age'), '3');
      expect(el().textContent).not.toContain(t('review.saveAnyway'));
      saveButton().click();
      expect(saved).toHaveBeenCalledWith({ 2: { age: '3' } });
    });

    it('is disabled while busy', () => {
      fixture.componentRef.setInput('busy', true);
      fixture.detectChanges();
      expect(saveButton().disabled).toBe(true);
      expect(buttonByText(t('review.skip'))!.disabled).toBe(true);
    });

    it('shows save errors', () => {
      fixture.componentRef.setInput('error', true);
      fixture.detectChanges();
      expect(el().textContent).toContain(t('review.saveFailed'));
    });
  });

  it('Skip emits skip and drops the local edits', () => {
    const skip = vi.fn();
    split.skip.subscribe(skip);
    type(cell(2, 'sex'), 'F');
    buttonByText(t('review.skip'))!.click();
    fixture.detectChanges();
    expect(skip).toHaveBeenCalledOnce();
    expect(cell(2, 'sex').value).toBe('M');
  });

  it('switching the page resets edits and the warning', () => {
    type(cell(2, 'sex'), 'F');
    saveButton().click();
    fixture.detectChanges();
    const select = el().querySelector<HTMLSelectElement>('select')!;
    expect([...select.options].map((o) => o.textContent?.trim())).toEqual(['1 (2)', '2 (1)']);
    select.value = '2';
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    expect(split.page()).toBe(2);
    expect(el().textContent).not.toContain(t('review.saveAnyway'));

    fixture.componentRef.setInput('rows', rows(2));
    fixture.detectChanges();
    expect(cell(2, 'sex').value).toBe('M');
  });

  it('the column-view button closes the split view', () => {
    const close = vi.fn();
    split.closeSplit.subscribe(close);
    el().querySelector<HTMLButtonElement>(`button[aria-label="${t('review.columnView')}"]`)!.click();
    expect(close).toHaveBeenCalledOnce();
  });

  it('shows a placeholder until the rows are loaded', () => {
    fixture.componentRef.setInput('rows', null);
    fixture.detectChanges();
    expect(el().textContent).toContain(t('viewer.notReady'));
  });
});
