import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, type Mock, vi } from 'vitest';
import { I18n } from '../../core/i18n/i18n';
import { Paginator } from './paginator';

describe('Paginator', () => {
  let fixture: ComponentFixture<Paginator>;
  let t: I18n['t'];
  let emitted: Mock<(page: number) => void>;

  const el = () => fixture.nativeElement as HTMLElement;
  const prev = () =>
    el().querySelector<HTMLButtonElement>(`button[aria-label="${t('pager.prev')}"]`)!;
  const next = () =>
    el().querySelector<HTMLButtonElement>(`button[aria-label="${t('pager.next')}"]`)!;

  function set(page: number, size: number, total: number) {
    fixture.componentRef.setInput('page', page);
    fixture.componentRef.setInput('size', size);
    fixture.componentRef.setInput('total', total);
    fixture.detectChanges();
  }

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [Paginator] });
    const i18n = TestBed.inject(I18n);
    i18n.lang.set('en');
    t = i18n.t.bind(i18n);
    fixture = TestBed.createComponent(Paginator);
    emitted = vi.fn<(page: number) => void>();
    fixture.componentInstance.pageChange.subscribe(emitted);
  });

  it('renders nothing when everything fits on one page', () => {
    set(1, 10, 10);
    expect(el().querySelector('button')).toBeNull();
    expect(el().textContent?.trim()).toBe('');
  });

  it('shows the range of the current page', () => {
    set(1, 10, 25);
    expect(el().textContent).toContain('1–10 of 25');
    set(3, 10, 25);
    expect(el().textContent).toContain('21–25 of 25');
  });

  it('disables "previous" on the first page and emits the next page', () => {
    set(1, 10, 25);
    expect(prev().disabled).toBe(true);
    expect(next().disabled).toBe(false);
    next().click();
    expect(emitted).toHaveBeenCalledWith(2);
  });

  it('disables "next" on the last page and emits the previous page', () => {
    set(3, 10, 25);
    expect(next().disabled).toBe(true);
    expect(prev().disabled).toBe(false);
    prev().click();
    expect(emitted).toHaveBeenCalledWith(2);
  });

  it('enables both buttons in the middle', () => {
    set(2, 10, 25);
    expect(prev().disabled).toBe(false);
    expect(next().disabled).toBe(false);
  });

  it('computes pages, from and to', () => {
    set(2, 10, 25);
    const p = fixture.componentInstance;
    expect(p.pages()).toBe(3);
    expect(p.from()).toBe(11);
    expect(p.to()).toBe(20);
    set(1, 10, 0);
    expect(p.pages()).toBe(1);
  });
});
