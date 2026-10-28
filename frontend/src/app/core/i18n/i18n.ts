import { Injectable, Pipe, PipeTransform, computed, effect, inject, signal } from '@angular/core';

import { de, Dictionary, TranslationKey } from './de';
import { en } from './en';
import { fr } from './fr';

export type Lang = 'de' | 'fr' | 'en';
export const LANGS: Lang[] = ['de', 'fr', 'en'];
const DICTS: Record<Lang, Dictionary> = { de, fr, en };
const STORAGE_KEY = 'docdig.lang';

type Params = Record<string, string | number>;

function initialLang(): Lang {
  const stored = localStorage.getItem(STORAGE_KEY) as Lang | null;
  if (stored && LANGS.includes(stored)) return stored;
  const nav = navigator.language.slice(0, 2) as Lang;
  return LANGS.includes(nav) ? nav : 'de';
}

@Injectable({ providedIn: 'root' })
export class I18n {
  readonly lang = signal<Lang>(initialLang());
  private dict = computed(() => DICTS[this.lang()]);
  /** Locale for Intl/date formatting. */
  readonly locale = computed(() => ({ de: 'de-CH', fr: 'fr-CH', en: 'en-GB' })[this.lang()]);

  constructor() {
    effect(() => {
      localStorage.setItem(STORAGE_KEY, this.lang());
      document.documentElement.lang = this.lang();
    });
  }

  t(key: TranslationKey | string, params?: Params): string {
    let text: string = (this.dict() as Record<string, string>)[key] ?? key;
    if (params) for (const [k, v] of Object.entries(params)) text = text.replaceAll(`{${k}}`, String(v));
    return text;
  }

  has(key: string): key is TranslationKey {
    return key in de;
  }
}

/** `{{ 'key' | t }}` / `{{ 'key' | t: { n: 3 } }}`. Impure so it reacts to language changes. */
@Pipe({ name: 't', pure: false })
export class TPipe implements PipeTransform {
  private i18n = inject(I18n);
  transform(key: TranslationKey | string, params?: Params): string {
    return this.i18n.t(key, params);
  }
}
