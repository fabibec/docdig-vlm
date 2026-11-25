import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';
import type { DocStatus } from '../../core/api/api.types';
import { I18n } from '../../core/i18n/i18n';
import { StatusBadge } from './status-badge';

describe('StatusBadge', () => {
  let i18n: I18n;

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [StatusBadge] });
    i18n = TestBed.inject(I18n);
    i18n.lang.set('en');
  });

  function render(status: DocStatus, count: number | null = null) {
    const fixture = TestBed.createComponent(StatusBadge);
    fixture.componentRef.setInput('status', status);
    if (count !== null) fixture.componentRef.setInput('count', count);
    fixture.detectChanges();
    return fixture;
  }

  const text = (el: HTMLElement) => el.textContent?.replace(/\s+/g, ' ').trim();

  it('shows the translated status of every status', () => {
    for (const s of ['queued', 'processing', 'review', 'done', 'error'] as DocStatus[]) {
      expect(text(render(s).nativeElement)).toBe(i18n.t(`status.${s}`));
    }
  });

  it('appends the count when given', () => {
    expect(text(render('review', 3).nativeElement)).toBe(`${i18n.t('status.review')} · 3`);
    expect(text(render('done', 0).nativeElement)).toBe(`${i18n.t('status.done')} · 0`);
  });

  it('uses a different colour per status', () => {
    const cls = (s: DocStatus) =>
      (render(s).nativeElement as HTMLElement).querySelector('span')!.className;
    expect(cls('done')).toContain('bg-green-100');
    expect(cls('review')).toContain('bg-red-100');
    expect(cls('processing')).toContain('bg-amber-100');
    expect(cls('error')).toContain('text-destructive');
    expect(cls('queued')).toContain('bg-muted');
  });

  it('follows language changes', () => {
    const fixture = render('done');
    i18n.lang.set('de');
    fixture.detectChanges();
    expect(text(fixture.nativeElement)).toBe('Fertig');
  });
});
