/**
 * One finished run with an artifact of every kind the run panel shows, and
 * the History routes that open its panel, for the viewer specs
 * (`image-lightbox.spec.ts`, `artifact-viewer-gallery.spec.ts`).
 *
 * The artifact routes (listing, file, preview, image, image-info) are served
 * through `setupArtifactRoutes` in `src/lib/__fixtures__/route-replay.ts`;
 * the images are SVG plots of the size image-info reports.
 */
import { expect, type Page } from '@playwright/test';
import {
  setupArtifactRoutes,
  type ArtifactRouteFixture,
  type ImageFixture,
} from '../../../src/lib/__fixtures__/route-replay';

export const RUN_ID = 'r1';

const RUN = {
  id: RUN_ID,
  module: 'scrna',
  method: 'qc_and_cluster',
  version: 1,
  timestamp: 1_700_000_000_000,
  duration: 84.2,
  status: 'success',
  inputs: {},
  outputs: {},
  outputAvailability: {},
  metrics: {},
  dataSource: 'TP132_ctrl_01',
  parentRunIds: [],
  parents: [],
  versionIdentity: null,
  codeFingerprint: null,
  envName: null,
  envFingerprint: null,
  gitCommit: null,
  wfcVersion: null,
  pipelineDocHash: null,
  keyedUnderEarlierRule: false,
  experimentId: 'exp1',
  runName: 'pipe_a/qc_and_cluster',
  nid: 'v1',
  user: 'tester',
  favorite: false,
  pipelineId: 'pipe_a',
  pipelineName: 'pipe_a',
  scriptPath: null,
};

/** A deterministic scatter plot as an SVG of the given pixel size. */
export function plotSvg(width: number, height: number, hue: number, title: string): string {
  let seed = hue * 7919 + width;
  const rnd = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  const dots: string[] = [];
  for (let i = 0; i < 160; i += 1) {
    const cluster = i % 4;
    const cx = width * (0.25 + 0.5 * ((cluster % 2) + rnd() * 0.8) / 1.8);
    const cy = height * (0.25 + 0.5 * ((cluster >> 1) + rnd() * 0.8) / 1.8);
    const r = Math.max(2, Math.min(width, height) / 90);
    dots.push(`<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r.toFixed(1)}" `
      + `fill="hsl(${(hue + cluster * 55) % 360} 65% 50%)" fill-opacity="0.75"/>`);
  }
  const fs = Math.round(Math.min(width, height) / 18);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`
    + `<rect width="100%" height="100%" fill="#ffffff"/>`
    + `<line x1="${width * 0.08}" y1="${height * 0.92}" x2="${width * 0.95}" y2="${height * 0.92}" stroke="#333" stroke-width="2"/>`
    + `<line x1="${width * 0.08}" y1="${height * 0.08}" x2="${width * 0.08}" y2="${height * 0.92}" stroke="#333" stroke-width="2"/>`
    + `<text x="${width / 2}" y="${height * 0.06}" font-family="sans-serif" font-size="${fs}" text-anchor="middle" fill="#222">${title}</text>`
    + dots.join('')
    + '</svg>';
}

function image(width: number, height: number, hue: number, title: string, extra: Partial<ImageFixture> = {}): ImageFixture {
  return { width, height, body: plotSvg(width, height, hue, title), contentType: 'image/svg+xml', ...extra };
}

/** A one-page PDF saying `text`, with a valid cross-reference table. */
function onePagePdf(text: string): Buffer {
  const objs = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
    '',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  const stream = `BT /F1 28 Tf 72 760 Td (${text}) Tj ET`;
  objs[3] = `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`;
  let out = '%PDF-1.4\n';
  const offsets: number[] = [];
  objs.forEach((o, i) => {
    offsets.push(out.length);
    out += `${i + 1} 0 obj\n${o}\nendobj\n`;
  });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n`;
  for (const off of offsets) out += `${String(off).padStart(10, '0')} 00000 n \n`;
  out += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, 'latin1');
}

const TSV_ROWS = [
  ['gene', 'ctrl_01', 'ctrl_02', 'ilc_01', 'ilc_02', 'nst_01'],
  ['ESR1', '1834', '1702', '2411', '2290', '988'],
  ['GATA3', '1207', '1150', '1640', '1588', '701'],
  ['CDH1', '2203', '2118', '41', '57', '2050'],
  ['KRT8', '3307', '3190', '2875', '2799', '3402'],
  ['MKI67', '92', '104', '61', '77', '311'],
  ['PGR', '640', '598', '902', '877', '233'],
  ['ERBB2', '455', '470', '388', '402', '1210'],
];

const CSV_ROWS = [
  ['cell_id', 'sample', 'cluster', 'n_genes', 'total_counts', 'pct_mito'],
  ...Array.from({ length: 12 }, (_, i) => [
    `AAACCTG${String(1000 + i * 37)}`, i % 2 ? 'ctrl_02' : 'ctrl_01', String(i % 5),
    String(1800 + i * 61), String(5200 + i * 233), (2.1 + (i % 4) * 0.7).toFixed(2),
  ]),
];

const METRICS = {
  sample: 'TP132_ctrl_01',
  cells: { before_qc: 6120, after_qc: 5384, doublets_removed: 211 },
  thresholds: { min_genes: 200, max_pct_mito: 15, min_counts: 500 },
  clusters: [
    { id: 0, size: 1720, marker: 'KRT8' },
    { id: 1, size: 1302, marker: 'ESR1' },
    { id: 2, size: 988, marker: 'PTPRC' },
  ],
  leiden_resolution: 0.8,
  converged: true,
};

const LOG_TEXT = [
  '2026-11-05 10:02:11 INFO  loading TP132_ctrl_01 (6120 cells x 33538 genes)',
  '2026-11-05 10:02:14 INFO  computing QC metrics',
  '2026-11-05 10:02:19 WARN  14 cells have zero counts after filtering; dropped',
  '2026-11-05 10:02:20 INFO  filtering: min_genes=200 max_pct_mito=15',
  '2026-11-05 10:02:31 INFO  5384 cells kept',
  '2026-11-05 10:02:40 WARN  scrublet: expected doublet rate not set, using 0.06',
  '2026-11-05 10:03:02 ERROR harmony did not converge in 10 iterations; using the last embedding',
  '2026-11-05 10:03:05 INFO  leiden resolution=0.8 -> 9 clusters',
  '2026-11-05 10:03:35 INFO  wrote umap.png, cluster_assignments_per_sample.csv',
  '2026-11-05 10:03:35 INFO  done in 84.2 s',
].join('\n');

const CONFIG_YAML = 'qc:\n  min_genes: 200\n  max_pct_mito: 15\ncluster:\n  method: leiden\n  resolution: 0.8\n';

function table(rows: string[][], delimiter: 'tab' | 'comma', bytes: number) {
  return {
    kind: 'table', truncated: false, reason: null, shown_bytes: bytes, total_bytes: bytes, text: null,
    table: {
      delimiter, delimiter_guessed: true, header: true, header_guessed: true,
      columns: rows[0], rows: rows.slice(1),
    },
  };
}

function textPreview(kind: 'json' | 'text', text: string) {
  return { kind, text, truncated: false, reason: null, shown_bytes: text.length, total_bytes: text.length, table: null };
}

const file = (name: string, size: number, viewer: string | null, extension: string) =>
  ({ name, size, viewer, extension, type: 'file' });

const LONG_CSV = 'cluster_assignments_per_sample_after_harmony_integration.csv';

/** The run's artifacts: every category, a folder, a long name and download-only files. */
export const ARTIFACTS: ArtifactRouteFixture = {
  runId: RUN_ID,
  listing: [
    file('counts.tsv', 18_400, 'table', 'tsv'),
    file(LONG_CSV, 412_000, 'table', 'csv'),
    file('metrics.json', 1_900, 'json', 'json'),
    file('adata_filtered.h5ad', 182_000_000, null, 'h5ad'),
    file('umap.png', 286_000, 'image', 'png'),
    file('spatial_section_04.tif', 96_000_000, 'image', 'tif'),
    file('whole_slide_scan.tif', 1_536_000_000, 'image', 'tif'),
    file('qc_report.pdf', 1_100_000, 'pdf', 'pdf'),
    {
      name: 'qc_plots/', size: 9_850_000, type: 'dir', viewer: null, extension: '', count: 6,
      children: [
        { name: '01_n_genes.png', size: 210_000, viewer: 'image', extension: 'png' },
        { name: '02_total_counts.png', size: 205_000, viewer: 'image', extension: 'png' },
        { name: '03_pct_mito_16bit.tif', size: 40_000_000, viewer: 'image', extension: 'tif' },
        { name: 'matrix.h5', size: 9_000_000, viewer: null, extension: 'h5' },
        { name: 'notes.txt', size: 300, viewer: 'text', extension: 'txt' },
        { name: 'raw/', size: 0, viewer: null, extension: '' },
      ],
    },
    file('run.log', 2_400, 'text', 'log'),
    file('config.yaml', 120, 'text', 'yaml'),
  ],
  files: {
    'qc_report.pdf': { body: onePagePdf('QC report: TP132_ctrl_01'), contentType: 'application/pdf' },
  },
  previews: {
    'counts.tsv': table(TSV_ROWS, 'tab', 18_400),
    [LONG_CSV]: table(CSV_ROWS, 'comma', 412_000),
    'metrics.json': textPreview('json', JSON.stringify(METRICS, null, 2)),
    'run.log': textPreview('text', LOG_TEXT),
    'config.yaml': textPreview('text', CONFIG_YAML),
    'qc_plots/notes.txt': textPreview('text', 'QC plots for TP132_ctrl_01.\nThresholds as in config.yaml.\n'),
  },
  images: {
    'umap.png': image(1800, 1400, 200, 'UMAP: leiden clusters'),
    'spatial_section_04.tif': image(8000, 6000, 20, 'Spatial section 04', { bitDepth: 16 }),
    'whole_slide_scan.tif': image(40000, 38400, 120, 'Whole slide', { refused: true }),
    'qc_plots/01_n_genes.png': image(1200, 900, 140, 'n_genes per cell'),
    'qc_plots/02_total_counts.png': image(1200, 900, 260, 'total_counts per cell'),
    'qc_plots/03_pct_mito_16bit.tif': image(5000, 4000, 320, 'pct_mito (16-bit)', { bitDepth: 16, pages: 3 }),
  },
};

export const LONG_CSV_NAME = LONG_CSV;

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

/**
 * Mock the History routes the run panel needs, then the run's artifacts.
 *
 * Args:
 *     page: The Playwright page.
 *     artifacts: The artifact fixture; the default is `ARTIFACTS`.
 *
 * Returns:
 *     What `setupArtifactRoutes` returns (the image route's requests).
 */
export async function setupArtifactRun(page: Page, artifacts: ArtifactRouteFixture = ARTIFACTS) {
  await page.route('**/api/wfc/run/*/cancelled-descendants', route => route.fulfill(json([])));
  await page.route('**/api/wfc/run/*', async (route) => {
    const m = route.request().url().match(/\/api\/wfc\/run\/([^/?]+)(?:\?.*)?$/);
    if (!m) return route.fallback();
    return route.fulfill(json(RUN));
  });
  await page.route('**/api/wfc/runs', route => route.fulfill(json([RUN])));
  await page.route('**/api/wfc/pipelines', route => route.fulfill(json([
    { pipelineId: 'pipe_a', status: 'succeeded', expected: 1, nodeOrder: null },
  ])));
  await page.route('**/api/wfc/draft-steps', route => route.fulfill(json({ steps: [] })));
  await page.route('**/api/wfc/modules', route => route.fulfill(json(['scrna'])));
  await page.route('**/api/wfc/methods', route => route.fulfill(json([
    { name: 'qc_and_cluster', module: 'scrna', script_path: null, env: 'x' },
  ])));
  await page.route('**/api/wfc/status', route => route.fulfill(json({ loaded: true, path: '/x', modules: 1, runs: 1 })));
  await page.route('**/api/dev/status', route => route.fulfill(json({ dev: false })));
  await page.route('**/api/samples', route => route.fulfill(json([])));
  return setupArtifactRoutes(page, artifacts);
}

/**
 * Open the History tab's Pipelines view, the run's panel and its Artifacts tab.
 *
 * Args:
 *     page: The Playwright page, its routes already set up.
 */
export async function openRunArtifacts(page: Page): Promise<void> {
  await page.goto('/?fixture=history-pipelines');
  await page.waitForSelector('.view-switcher', { timeout: 10_000 });
  await page.locator('.view-switcher .seg', { hasText: 'Pipelines' }).click();
  const row = page.locator('.pipeline-row', { hasText: 'pipe_a' });
  await expect(row).toBeVisible({ timeout: 5_000 });
  await row.locator('.row-head').click();
  await row.locator('.children .child-row').first().click();
  await expect(page.locator('.detail-panel')).toBeVisible({ timeout: 5_000 });
  await page.locator('.detail-panel').getByText('Artifacts').first().click();
  await expect(page.getByTestId('artifact-row').first()).toBeVisible({ timeout: 5_000 });
}
