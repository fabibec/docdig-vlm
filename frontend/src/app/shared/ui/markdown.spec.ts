import { describe, it, expect } from 'vitest';

import { toMarkdown } from './markdown';
import { ColumnKey, COLUMNS, RowOut } from '../../core/api/api.types';

const labels = Object.fromEntries(COLUMNS.map((c) => [c, c.toUpperCase()])) as Record<ColumnKey, string>;

function row(idx: number, values: Partial<Record<ColumnKey, string>>): RowOut {
  const all = Object.fromEntries(COLUMNS.map((c) => [c, ''])) as Record<ColumnKey, string>;
  return { idx, y0: 0, y1: 1, values: { ...all, ...values }, corrected: [] };
}

describe('toMarkdown', () => {
  it('renders a header, a separator and one line per row with aligned columns', () => {
    const md = toMarkdown([row(1, { species: 'Amsel', sex: 'M' }), row(12, { species: 'Kohlmeise' })], labels);
    const lines = md.split('\n');
    expect(lines.length).toBe(4);
    expect(lines[0].startsWith('| #  | RING_NO | SPECIES   |')).toBe(true);
    expect(lines[1]).toMatch(/^\|-+\|-+\|/);
    expect(lines[2].startsWith('| 1  |         | Amsel     | M ')).toBe(true);
    expect(lines[3].startsWith('| 12 |         | Kohlmeise |')).toBe(true);
    // every line has the same width
    expect(new Set(lines.map((l) => l.length)).size).toBe(1);
  });

  it('uses the given (translated) labels in column order', () => {
    const header = toMarkdown([], labels).split('\n')[0];
    const cells = header.split('|').map((c) => c.trim()).filter(Boolean);
    expect(cells).toEqual(['#', ...COLUMNS.map((c) => c.toUpperCase())]);
  });

  it('escapes pipes and treats missing values as empty', () => {
    const r = row(3, { additional_remark: 'a|b' });
    delete (r.values as Partial<Record<ColumnKey, string>>).species;
    const md = toMarkdown([r], labels);
    expect(md).toContain('a\\|b');
    expect(md).not.toContain('undefined');
  });
});
