import { describe, it, expect } from 'vitest';
import { slotColor, truncateSlotType } from '../slotColor';

describe('slotColor', () => {
  it('is deterministic for the same extension', () => {
    expect(slotColor('.h5ad')).toBe(slotColor('.h5ad'));
    expect(slotColor('.xyz')).toBe(slotColor('.xyz'));
  });

  it('is case-insensitive', () => {
    expect(slotColor('.CSV')).toBe(slotColor('.csv'));
  });

  it('gives distinct colours to distinct extensions', () => {
    expect(slotColor('.csv')).not.toBe(slotColor('.json'));
  });

  it('treats dir and directory as the same directory colour', () => {
    expect(slotColor('dir')).toBe(slotColor('directory'));
  });

  it('falls back to a hashed HSL colour for unknown extensions', () => {
    expect(slotColor('.unheardof')).toMatch(/^hsl\(\d+, 55%, 55%\)$/);
  });

  it('truncates long values with a full-value ellipsis', () => {
    expect(truncateSlotType('.csv')).toBe('.csv');
    expect(truncateSlotType('.superlongext')).toHaveLength(8);
  });
});
