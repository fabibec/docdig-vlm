import { describe, it, expect } from 'vitest';

import { FileSizePipe, formatFileSize } from './file-size.pipe';

describe('formatFileSize', () => {
  it('shows bytes below 1 KB', () => {
    expect(formatFileSize(0)).toBe('0 B');
    expect(formatFileSize(1023)).toBe('1023 B');
  });

  it('shows KB with one decimal below 10 KB, whole KB above', () => {
    expect(formatFileSize(1024)).toBe('1.0 KB');
    expect(formatFileSize(1536)).toBe('1.5 KB');
    expect(formatFileSize(10 * 1024)).toBe('10 KB');
    expect(formatFileSize(500 * 1024)).toBe('500 KB');
  });

  it('shows MB with one decimal', () => {
    expect(formatFileSize(1024 * 1024)).toBe('1.0 MB');
    expect(formatFileSize(12.34 * 1024 * 1024)).toBe('12.3 MB');
  });

  it('is exposed as the fileSize pipe', () => {
    expect(new FileSizePipe().transform(2048)).toBe('2.0 KB');
  });
});
