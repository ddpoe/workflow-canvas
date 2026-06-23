// Tiny line-based Myers LCS diff. No deps.
//
// Produces a unified-diff-style row list suitable for side-by-side rendering
// in the Envs tab. Not a full patch emitter — we only render changes, no
// hunk headers, no line numbers.
//
// Complexity: O(N*M) worst-case time, O(N*M) space. Env blobs are small
// (hundreds of lines at most), so this is fine.

export type DiffRow = { kind: 'ctx' | 'add' | 'del'; text: string };

export function diffLines(a: string, b: string): DiffRow[] {
  const aLines = a.split('\n');
  const bLines = b.split('\n');
  const n = aLines.length;
  const m = bLines.length;

  // LCS table
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      if (aLines[i] === bLines[j]) {
        lcs[i][j] = lcs[i + 1][j + 1] + 1;
      } else {
        lcs[i][j] = Math.max(lcs[i + 1][j], lcs[i][j + 1]);
      }
    }
  }

  // Walk the table to produce the diff.
  const rows: DiffRow[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (aLines[i] === bLines[j]) {
      rows.push({ kind: 'ctx', text: aLines[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      rows.push({ kind: 'del', text: aLines[i] });
      i++;
    } else {
      rows.push({ kind: 'add', text: bLines[j] });
      j++;
    }
  }
  while (i < n) rows.push({ kind: 'del', text: aLines[i++] });
  while (j < m) rows.push({ kind: 'add', text: bLines[j++] });

  return rows;
}
