import { describe, it, expect } from 'vitest';

import { de } from './de';
import { en } from './en';
import { fr } from './fr';

const DICTS: Record<string, Record<string, string>> = { de, en, fr };
const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

describe('i18n dictionaries', () => {
  const deKeys = Object.keys(de).sort();

  for (const [lang, dict] of Object.entries(DICTS)) {
    describe(lang, () => {
      it('has exactly the keys of de.ts', () => {
        expect(Object.keys(dict).sort()).toEqual(deKeys);
      });

      it('has no empty texts', () => {
        const empty = Object.entries(dict).filter(([, v]) => typeof v !== 'string' || !v.trim()).map(([k]) => k);
        expect(empty).toEqual([]);
      });

      it('uses the same {placeholders} as de.ts', () => {
        const mismatched = deKeys.filter((k) => placeholders(dict[k]).join() !== placeholders(de[k as keyof typeof de]).join());
        expect(mismatched).toEqual([]);
      });
    });
  }

  it('provides a label for every column and status', () => {
    const required = [
      'ring_no', 'species', 'sex', 'age', 'date', 'time', 'wing_length_mm', 'weight_g', 'additional_remark',
    ].map((c) => `col.${c}`).concat(['queued', 'processing', 'review', 'done', 'error'].map((s) => `status.${s}`));
    for (const key of required) expect(deKeys).toContain(key);
  });
});
