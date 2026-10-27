/**
 * Component-mounting Vitest tests for the Envs-tab Packages panel.
 *
 * EnvsSubTab fetches the `GET /api/registry/envs` list (carrying
 * `backend` + `has_packages`) and, on row expand, the
 * `GET /api/registry/envs/{spec}/packages` endpoint. These tests mount
 * the real component with a stubbed `fetch` and assert the two
 * user-visible outcomes the panel exists to produce:
 *
 *   1. A captured env renders its sorted, source-tagged `name==version`
 *      package list on expand.
 *   2. An uncaptured env renders the backend-specific empty state —
 *      byo vs legacy/not-rebuilt — without a packages round-trip.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup } from '@testing-library/svelte';
import { within } from '@testing-library/dom';
import EnvsSubTab from '../EnvsSubTab.svelte';

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function installFetch(handler: (url: string) => unknown) {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    return jsonResponse(handler(url));
  }) as unknown as typeof fetch;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('EnvsSubTab packages panel — populated', () => {
  beforeEach(() => {
    installFetch((url) => {
      if (url.endsWith('/packages')) {
        return {
          spec: 'container:analysis',
          backend: 'pixi',
          captured: true,
          packages: [
            { name: 'numpy', version: '1.26.4', source: 'conda' },
            { name: 'pandas', version: '2.1.0', source: 'pip' },
            { name: 'scipy', version: '1.11.0', source: 'pixi' },
          ],
        };
      }
      // GET /api/registry/envs
      return {
        envs: [
          {
            spec: 'container:analysis',
            methods: ['seg.segment'],
            backend: 'pixi',
            has_packages: true,
            last_run_at: null,
            run_count: 0,
          },
        ],
      };
    });
  });

  it('renders the sorted name==version list tagged by source on expand', async () => {
    const { findByTestId, getByTestId } = render(EnvsSubTab, { props: { visible: true } });

    // Row appears once the list fetch resolves; expand it.
    const row = await findByTestId('env-row');
    await fireEvent.click(row);

    const panel = await findByTestId('packages-panel');
    const rows = within(panel).getAllByTestId('package-row');
    expect(rows).toHaveLength(3);

    expect(rows[0]).toHaveTextContent('numpy==1.26.4');
    expect(rows[0]).toHaveTextContent('conda');
    expect(rows[1]).toHaveTextContent('pandas==2.1.0');
    expect(rows[1]).toHaveTextContent('pip');
    expect(rows[2]).toHaveTextContent('scipy==1.11.0');
    expect(rows[2]).toHaveTextContent('pixi');

    // No empty state when packages are present.
    expect(() => getByTestId('packages-empty')).toThrow();
  });
});

describe('EnvsSubTab packages panel — uv env', () => {
  beforeEach(() => {
    installFetch((url) => {
      if (url.endsWith('/packages')) {
        return {
          spec: 'tools',
          backend: 'uv',
          captured: true,
          packages: [{ name: 'requests', version: '2.32.3', source: 'uv' }],
        };
      }
      return {
        envs: [
          {
            spec: 'tools',
            methods: ['net.fetch'],
            backend: 'uv',
            has_packages: true,
            last_run_at: null,
            run_count: 0,
          },
        ],
      };
    });
  });

  it('shows the uv backend chip and tags the packages with the uv source', async () => {
    const { findByTestId, getByText } = render(EnvsSubTab, { props: { visible: true } });

    const row = await findByTestId('env-row');
    const chip = within(row).getByText('uv');
    expect(chip).toHaveClass('chip-backend', 'backend-uv');

    await fireEvent.click(row);
    const panel = await findByTestId('packages-panel');
    const pkg = within(panel).getByTestId('package-row');
    expect(pkg).toHaveTextContent('requests==2.32.3');
    expect(within(pkg).getByText('uv')).toHaveClass('pkg-source', 'src-uv');
    expect(getByText('requests==2.32.3')).toBeInTheDocument();
  });
});

describe('EnvsSubTab packages panel — refused package list', () => {
  const detail =
    ".wfc/captures/analysis/0a/packages.lock no longer matches env 'analysis''s record. " +
    "env 'analysis''s capture files under .wfc/captures/analysis/0a/ were changed " +
    'outside wfc. Register the env again under a new name: `wfc register-env <new-name> ...`';

  beforeEach(() => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.endsWith('/packages')) {
        return {
          ok: false,
          status: 409,
          json: async () => ({ detail }),
          text: async () => JSON.stringify({ detail }),
        } as unknown as Response;
      }
      return jsonResponse({
        envs: [
          {
            spec: 'analysis',
            methods: ['seg.segment'],
            backend: 'pixi',
            has_packages: true,
            last_run_at: null,
            run_count: 0,
          },
        ],
      });
    }) as unknown as typeof fetch;
  });

  it('shows the refusal detail naming the re-register command', async () => {
    const { findByTestId, findByText } = render(EnvsSubTab, { props: { visible: true } });

    await fireEvent.click(await findByTestId('env-row'));

    await findByText(`HTTP 409: ${detail}`);
  });
});

describe('EnvsSubTab packages panel — empty states', () => {
  let packagesCalls = 0;

  beforeEach(() => {
    packagesCalls = 0;
    installFetch((url) => {
      if (url.endsWith('/packages')) {
        packagesCalls += 1;
        return { spec: '?', backend: null, captured: false, packages: [] };
      }
      return {
        envs: [
          {
            spec: 'container:vendor',
            methods: ['vendor.run'],
            backend: 'byo',
            has_packages: false,
            last_run_at: null,
            run_count: 0,
          },
          {
            spec: 'container:legacy',
            methods: ['old.step'],
            backend: 'pixi',
            has_packages: false,
            last_run_at: null,
            run_count: 0,
          },
        ],
      };
    });
  });

  it('shows the byo message and the legacy message, without a packages fetch', async () => {
    const { findAllByTestId, findByText } = render(EnvsSubTab, { props: { visible: true } });

    const rows = await findAllByTestId('env-row');
    expect(rows).toHaveLength(2);

    // byo env → bring-your-own copy.
    await fireEvent.click(rows[0]);
    await findByText(/No package manifest — bring-your-own image/);

    // legacy/not-rebuilt env → re-register copy.
    await fireEvent.click(rows[1]);
    await findByText(/Not captured — re-register this env to record its packages/);

    // has_packages:false rows must not trigger the packages endpoint.
    expect(packagesCalls).toBe(0);
  });
});
