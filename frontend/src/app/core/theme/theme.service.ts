import { DestroyRef, Injectable, computed, effect, inject, signal } from '@angular/core';

export type ThemeMode = 'system' | 'light' | 'dark';
export const THEME_MODES: ThemeMode[] = ['system', 'light', 'dark'];
const STORAGE_KEY = 'docdig.theme';

/** Light/dark mode. Default follows the OS (prefers-color-scheme) live. */
@Injectable({ providedIn: 'root' })
export class Theme {
  readonly mode = signal<ThemeMode>((localStorage.getItem(STORAGE_KEY) as ThemeMode) || 'system');
  private systemDark = signal(false);
  readonly dark = computed(() => (this.mode() === 'system' ? this.systemDark() : this.mode() === 'dark'));

  constructor() {
    const mq = matchMedia('(prefers-color-scheme: dark)');
    this.systemDark.set(mq.matches);
    const onChange = (e: MediaQueryListEvent) => this.systemDark.set(e.matches);
    mq.addEventListener('change', onChange);
    inject(DestroyRef).onDestroy(() => mq.removeEventListener('change', onChange));

    effect(() => {
      document.documentElement.classList.toggle('dark', this.dark());
      localStorage.setItem(STORAGE_KEY, this.mode());
    });
  }
}
