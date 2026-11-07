/**
 * A failure summary shows the exception, not the start of a traceback.
 */
import { describe, it, expect } from 'vitest';
import { errorSummary, withEndingTraceback } from '../errorSummary';

type Line = { kind: 'stdout' | 'stderr'; line: string };
const TB = 'Traceback (most recent call last):\n  File "m.py", line 3\nValueError: bad input column\n';

describe('withEndingTraceback', () => {
  it('a log that already ends in the exception is shown as it is', () => {
    const log: Line[] = [{ kind: 'stderr', line: 'Traceback (most recent call last):' }, { kind: 'stderr', line: 'ValueError: bad input column' }];
    expect(withEndingTraceback(log, TB)).toBe(log);
  });

  it('an empty log, or one of warnings only, gets the traceback after it as stderr', () => {
    expect(withEndingTraceback([], TB).map(l => l.line)).toEqual(['Traceback (most recent call last):', '  File "m.py", line 3', 'ValueError: bad input column']);
    const warned = withEndingTraceback([{ kind: 'stderr', line: 'warning: x' }], TB);
    expect(warned[0].line).toBe('warning: x');
    expect(warned.every(l => l.kind === 'stderr')).toBe(true);
    expect(warned).toHaveLength(4);
  });

  it('no traceback leaves the log alone', () => {
    const log: Line[] = [{ kind: 'stdout', line: 'ok' }];
    expect(withEndingTraceback(log, null)).toBe(log);
    expect(withEndingTraceback(log, '  ')).toBe(log);
  });
});

describe('errorSummary', () => {
  it('a traceback summarises to its exception line', () => {
    const tb = 'Traceback (most recent call last):\n  File "m.py", line 3, in main\n    run()\nValueError: bad input column\n';
    expect(errorSummary(tb)).toBe('ValueError: bad input column');
  });

  it("an exception group's traceback summarises past its border to the exception", () => {
    const tb = 'Traceback (most recent call last):\n  File "m.py", line 3\n  | ExceptionGroup: two errors (2 sub-exceptions)\n  +-+---------------- 1 ----------------\n    | ValueError: bad input column\n    +------------------------------------\n';
    expect(errorSummary(tb)).toBe('ValueError: bad input column');
  });

  it('a one-line message is shown as it is', () => {
    expect(errorSummary("KeyError: 'gene_symbol'")).toBe("KeyError: 'gene_symbol'");
  });

  it('a multi-line message that is not a traceback keeps its own order', () => {
    expect(errorSummary('Validation errors:\nmissing input data')).toBe('Validation errors:\nmissing input data');
  });
});
