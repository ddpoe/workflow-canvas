import { describe, it, expect } from 'vitest';
import { displaySlotType, slotColor, truncateSlotType } from '../slotColor';

describe('slotColor', () => {
  it('is deterministic for the same extension', () => {
    expect(slotColor('.h5ad')).toBe(slotColor('.h5ad'));
    expect(slotColor('.xyz')).toBe(slotColor('.xyz'));
  });

  it('is case-insensitive', () => {
    expect(slotColor('.CSV')).toBe(slotColor('.csv'));
  });

  it('is dot-insensitive: bare and dotted spellings share a colour', () => {
    expect(slotColor('csv')).toBe(slotColor('.csv'));
    expect(slotColor('xyz')).toBe(slotColor('.xyz'));
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

  it('displays the undotted form, keeping inner dots and directory markers', () => {
    expect(displaySlotType('.csv')).toBe('csv');
    expect(displaySlotType('csv')).toBe('csv');
    expect(displaySlotType('.tar.gz')).toBe('tar.gz');
    expect(displaySlotType('directory')).toBe('directory');
  });

  it('truncates long values in display form with a trailing ellipsis', () => {
    expect(truncateSlotType('.csv')).toBe('csv');
    expect(truncateSlotType('.superlongext')).toHaveLength(8);
  });
});
