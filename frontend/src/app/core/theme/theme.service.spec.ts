import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { Theme } from './theme.service';

/** Controllable `matchMedia('(prefers-color-scheme: dark)')`. */
class FakeMediaQuery {
  private listeners = new Set<(e: MediaQueryListEvent) => void>();
  constructor(public matches: boolean) {}
  addEventListener(_: 'change', l: (e: MediaQueryListEvent) => void) { this.listeners.add(l); }
  removeEventListener(_: 'change', l: (e: MediaQueryListEvent) => void) { this.listeners.delete(l); }
  get listenerCount() { return this.listeners.size; }
  set(matches: boolean) {
    this.matches = matches;
    for (const l of this.listeners) l({ matches } as MediaQueryListEvent);
  }
}

describe('Theme', () => {
  let mq: FakeMediaQuery;
  const isDark = () => document.documentElement.classList.contains('dark');

  function create(): Theme {
    TestBed.configureTestingModule({});
    const theme = TestBed.inject(Theme);
    TestBed.tick();
    return theme;
  }

  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove('dark');
    mq = new FakeMediaQuery(false);
    vi.stubGlobal('matchMedia', vi.fn((query: string) => {
      expect(query).toBe('(prefers-color-scheme: dark)');
      return mq;
    }));
  });

  afterEach(() => {
    TestBed.resetTestingModule();
    vi.unstubAllGlobals();
    localStorage.clear();
    document.documentElement.classList.remove('dark');
  });

  it('defaults to system and stores the mode in localStorage (docdig.theme)', () => {
    const theme = create();
    expect(theme.mode()).toBe('system');
    expect(localStorage.getItem('docdig.theme')).toBe('system');
  });

  it('restores the stored mode', () => {
    localStorage.setItem('docdig.theme', 'dark');
    const theme = create();
    expect(theme.mode()).toBe('dark');
    expect(isDark()).toBe(true);
  });

  it('light and dark toggle .dark on <html> regardless of the OS', () => {
    mq.matches = true;
    const theme = create();
    theme.mode.set('light');
    TestBed.tick();
    expect(theme.dark()).toBe(false);
    expect(isDark()).toBe(false);
    expect(localStorage.getItem('docdig.theme')).toBe('light');

    mq.set(false);
    theme.mode.set('dark');
    TestBed.tick();
    expect(isDark()).toBe(true);
    expect(localStorage.getItem('docdig.theme')).toBe('dark');
  });

  it('system follows prefers-color-scheme initially and live', () => {
    mq.matches = true;
    const theme = create();
    expect(theme.dark()).toBe(true);
    expect(isDark()).toBe(true);

    mq.set(false);
    TestBed.tick();
    expect(isDark()).toBe(false);

    mq.set(true);
    TestBed.tick();
    expect(isDark()).toBe(true);
  });

  it('ignores OS changes while an explicit mode is set', () => {
    const theme = create();
    theme.mode.set('light');
    TestBed.tick();
    mq.set(true);
    TestBed.tick();
    expect(isDark()).toBe(false);

    theme.mode.set('system');
    TestBed.tick();
    expect(isDark()).toBe(true);
  });

  it('removes the media query listener when destroyed', () => {
    create();
    expect(mq.listenerCount).toBe(1);
    TestBed.resetTestingModule();
    expect(mq.listenerCount).toBe(0);
  });
});
