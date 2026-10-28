import { ColumnKey, COLUMNS, RowOut } from '../../core/api/api.types';

/** Pipe table of the given rows; `labels` are the translated column headers. */
export function toMarkdown(rows: RowOut[], labels: Record<ColumnKey, string>): string {
  const header = ['#', ...COLUMNS.map((c) => labels[c])];
  const body = rows.map((r) => [String(r.idx), ...COLUMNS.map((c) => (r.values[c] ?? '').replaceAll('|', '\\|'))]);
  const widths = header.map((h, i) => Math.max(h.length, ...body.map((b) => b[i].length)));
  const line = (cells: string[]) => '| ' + cells.map((c, i) => c.padEnd(widths[i])).join(' | ') + ' |';
  return [line(header), '|' + widths.map((w) => '-'.repeat(w + 2)).join('|') + '|', ...body.map(line)].join('\n');
}
