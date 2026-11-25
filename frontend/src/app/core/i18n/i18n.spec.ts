import { ChangeDetectionStrategy, Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { de } from './de';
import { en } from './en';
import { fr } from './fr';
import { I18n, TPipe } from './i18n';

@Component({
  selector: 'app-t-host',
  imports: [TPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span id="plain">{{ 'pager.next' | t }}</span><span id="params">{{ 'pager.range' | t: { from: 1, to: 10, total: 42 } }}</span>`,
})
class THost {}

function create(): I18n {
  TestBed.configureTestingModule({});
  return TestBed.inject(I18n);
}

describe('I18n', () => {
  beforeEach(() => localStorage.clear());

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  describe('initial language', () => {
    it('uses the stored language', () => {
      localStorage.setItem('docdig.lang', 'fr');
      expect(create().lang()).toBe('fr');
    });

    it('ignores an unknown stored language', () => {
      localStorage.setItem('docdig.lang', 'xx');
      vi.spyOn(navigator, 'language', 'get').mockReturnValue('en-GB');
      expect(create().lang()).toBe('en');
    });

    it('defaults to the browser language', () => {
      vi.spyOn(navigator, 'language', 'get').mockReturnValue('fr-CH');
      expect(create().lang()).toBe('fr');
    });

    it('falls back to de for unsupported browser languages', () => {
      vi.spyOn(navigator, 'language', 'get').mockReturnValue('it-IT');
      expect(create().lang()).toBe('de');
    });
  });

  describe('persistence', () => {
    it('stores the language in localStorage (docdig.lang) and sets <html lang>', () => {
      const i18n = create();
      i18n.lang.set('fr');
      TestBed.tick();
      expect(localStorage.getItem('docdig.lang')).toBe('fr');
      expect(document.documentElement.lang).toBe('fr');

      i18n.lang.set('en');
      TestBed.tick();
      expect(localStorage.getItem('docdig.lang')).toBe('en');
      expect(document.documentElement.lang).toBe('en');
    });
  });

  describe('t()', () => {
    let i18n: I18n;
    beforeEach(() => {
      i18n = create();
    });

    it('translates into the current language', () => {
      i18n.lang.set('de');
      expect(i18n.t('pager.next')).toBe(de['pager.next']);
      i18n.lang.set('en');
      expect(i18n.t('pager.next')).toBe(en['pager.next']);
      i18n.lang.set('fr');
      expect(i18n.t('pager.next')).toBe(fr['pager.next']);
    });

    it('replaces every {name} placeholder', () => {
      i18n.lang.set('en');
      expect(i18n.t('pager.range', { from: 11, to: 20, total: 35 })).toBe('11–20 of 35');
      expect(i18n.t('flag.weight_out_of_range', { value: '3', min: 5, max: 40 })).toContain('“3”');
    });

    it('leaves unknown placeholders and keys as they are', () => {
      i18n.lang.set('en');
      expect(i18n.t('pager.range', { from: 1 })).toBe('1–{to} of {total}');
      expect(i18n.t('does.not.exist')).toBe('does.not.exist');
    });

    it('has() only accepts known keys', () => {
      expect(i18n.has('flag.time_format')).toBe(true);
      expect(i18n.has('flag.unknown_code')).toBe(false);
    });

    it('exposes a locale per language', () => {
      i18n.lang.set('de');
      expect(i18n.locale()).toBe('de-CH');
      i18n.lang.set('fr');
      expect(i18n.locale()).toBe('fr-CH');
      i18n.lang.set('en');
      expect(i18n.locale()).toBe('en-GB');
    });
  });

  describe('t pipe', () => {
    it('follows language changes', async () => {
      const i18n = create();
      i18n.lang.set('de');
      const fixture = TestBed.createComponent(THost);
      fixture.detectChanges();
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('#plain')!.textContent).toBe(de['pager.next']);
      expect(el.querySelector('#params')!.textContent).toBe('1–10 von 42');

      i18n.lang.set('en');
      fixture.detectChanges();
      expect(el.querySelector('#plain')!.textContent).toBe(en['pager.next']);
      expect(el.querySelector('#params')!.textContent).toBe('1–10 of 42');

      i18n.lang.set('fr');
      fixture.detectChanges();
      expect(el.querySelector('#plain')!.textContent).toBe(fr['pager.next']);
    });
  });
});
