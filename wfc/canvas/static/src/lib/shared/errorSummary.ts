/**
 * The line a summary shows for a run's error message.
 *
 * A message is usually one line, the exception wfc lifted from stderr. When
 * it is a whole Python traceback, its first line ("Traceback (most recent
 * call last):") says nothing, so the summary is its last non-blank line,
 * the exception itself. Any other message is returned as it is.
 *
 * Args:
 *     message: The run's error message.
 *
 * Returns:
 *     The text to show where only a line or two fits.
 */
export function errorSummary(message: string): string {
  if (!message.trimStart().startsWith('Traceback (most recent call last)')) return message;
  // An exception group's traceback ends in its box-drawing border
  // ("+-----", "|"), which is skipped to reach the exception line.
  const last = message.split('\n').map(l => l.trim())
    .filter(l => l && !/^[\s|+-]+$/.test(l)).pop();
  return last ? last.replace(/^\|\s*/, '') : message;
}

type LogLine = { kind: 'stdout' | 'stderr'; line: string };

/**
 * A run's log with the traceback its ending carried, when the log lacks it.
 *
 * A method that raises prints its traceback to stderr, so the log already
 * holds its last line. When the ending is wfc's own, the log lacks it: a
 * launch failure (empty log, or only wfc's warnings), or a one-line ending
 * after the method ran (an output that could not be collected, code changed
 * since the claim). Then the traceback is added after the log as stderr
 * lines, so it is always shown.
 *
 * Args:
 *     lines: The lines the stream delivered.
 *     traceback: The terminal frame's `error_traceback`, if any.
 *
 * Returns:
 *     The lines to show.
 */
export function withEndingTraceback(lines: LogLine[], traceback: string | null): LogLine[] {
  if (!traceback || !traceback.trim()) return lines;
  const tbLines = traceback.replace(/\n+$/, '').split('\n');
  const lastTb = tbLines.map(l => l.trim()).filter(Boolean).pop();
  if (lastTb && lines.some(l => l.line.trim() === lastTb)) return lines;
  return [...lines, ...tbLines.map(line => ({ kind: 'stderr' as const, line }))];
}
