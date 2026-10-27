/**
 * Component-mounting Vitest tests for the Registry's method version picker.
 *
 * The methods list carries every version of a method (`versions`: label,
 * identity, registration date, env, run count, newest flag, commit) and the
 * detail route takes a version's content identity (`?version=<identity>`).
 * These tests mount the real RegistryTab with a stubbed `fetch` and assert
 * what the picker exists to do: drop down one entry per label, "latest" on
 * the newest and shown on expand, and swap the files, contract and env shown
 * when one is chosen.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup, waitFor } from '@testing-library/svelte';
import { within } from '@testing-library/dom';
import RegistryTab from '../RegistryTab.svelte';

vi.mock('../shikiHighlight.js', () => ({
  highlight: async (content: string) => `<pre class="shiki"><code>${content}</code></pre>`,
}));

const V1 = 'a'.repeat(40);
const V2 = 'b'.repeat(40);

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function detailFor(identity: string) {
  const v1 = identity === V1;
  return {
    files: [{
      name: 'segment.py',
      language: 'python',
      content: v1 ? 'print("version one")' : 'print("version two")',
    }],
    contract: {
      input_slots: {},
      output_slots: {},
      params_schema: v1 ? { threshold: { type: 'number' } } : { sigma: { type: 'number' } },
      executor: 'python',
    },
    env: v1 ? 'seg-old' : 'seg',
    version: (v1 ? TWO_VERSIONS[0] : TWO_VERSIONS[1]),
  };
}

function version(label: number, identity: string, registered_at: string, env: string,
                 runCount: number, newest: boolean) {
  return { label, identity, registered_at, env, runCount, newest,
           commit: `${label}`.repeat(40), envFingerprint: 'e'.repeat(64),
           image: `docker://local/${env}@sha256:${'f'.repeat(64)}`, codeTree: 'c'.repeat(40) };
}

const TWO_VERSIONS = [
  version(1, V1, '2026-10-01T09:00:00Z', 'seg-old', 3, false),
  version(2, V2, '2026-10-08T09:00:00Z', 'seg', 2, true),
];

let detailUrls: string[] = [];
let methodVersions: any[] = TWO_VERSIONS;

beforeEach(() => {
  detailUrls = [];
  methodVersions = TWO_VERSIONS;
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    if (url.includes('/detail')) {
      detailUrls.push(url);
      const identity = new URL(url, 'http://x').searchParams.get('version') ?? V2;
      return jsonResponse(detailFor(identity));
    }
    if (url === '/api/registry/methods') {
      return jsonResponse({
        methods: [{
          name: 'segment',
          module: 'seg',
          env: 'seg',
          runCount: 5,
          source: null,
          versions: methodVersions,
        }],
      });
    }
    if (url === '/api/registry/modules') return jsonResponse({ modules: [] });
    if (url === '/api/registry/samples') return jsonResponse({ samples: [] });
    return jsonResponse({});
  }) as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function expandSegment() {
  const view = render(RegistryTab, { props: { visible: true } });
  await fireEvent.click(await view.findByRole('button', { name: /^Methods/ }));
  await waitFor(() => expect(view.queryByTestId('method-row')).not.toBeNull());
  await fireEvent.click(view.getByTestId('method-row'));
  return view;
}

/** Open the version picker's dropdown and return it. */
async function openPicker(view: Awaited<ReturnType<typeof expandSegment>>) {
  await fireEvent.click(await view.findByTestId('version-picker'));
  return view.findByTestId('version-toggle');
}

describe('RegistryTab method version picker', () => {
  it('drops down one entry per label, newest first and marked latest, and shows the newest on expand', async () => {
    const view = await expandSegment();
    await view.findByText('sigma');
    await view.findByText('print("version two")');
    expect(view.getByTestId('version-picker')).toHaveTextContent('v2');
    expect(view.getByTestId('version-picker')).toHaveTextContent('latest');

    const toggle = await openPicker(view);
    const options = within(toggle).getAllByTestId('version-option');
    expect(options).toHaveLength(2);
    expect(options[0]).toHaveTextContent('v2');
    expect(options[0]).toHaveTextContent('latest');
    expect(options[0]).toHaveAttribute('aria-pressed', 'true');
    expect(options[1]).toHaveTextContent('v1');
    expect(options[1]).not.toHaveTextContent('latest');
    // No date, env or run count in the entries: those are behind the ⓘ and the details card.
    expect(options[1]).not.toHaveTextContent('2026-10-01');
    expect(options[1]).not.toHaveTextContent('seg-old');
    expect(within(toggle).getAllByTestId('version-info')[1]).toHaveAttribute(
      'aria-label', expect.stringContaining('3 runs'));
  });

  it('choosing an older version fetches it by identity and swaps the files, contract and env', async () => {
    const view = await expandSegment();
    await view.findByText('sigma');
    const toggle = await openPicker(view);

    await fireEvent.click(within(toggle).getAllByTestId('version-option')[1]);

    await view.findByText('threshold');
    await view.findByText('print("version one")');
    expect(view.queryByText('sigma')).toBeNull();
    expect(view.queryByText('print("version two")')).toBeNull();
    expect(view.getByTestId('version-env')).toHaveTextContent('seg-old');
    expect(view.getByTestId('version-picker')).toHaveTextContent('v1');
    expect(detailUrls.some(u => u.endsWith(`?version=${V1}`))).toBe(true);
    const reopened = await openPicker(view);
    expect(within(reopened).getAllByTestId('version-option')[1]).toHaveAttribute('aria-pressed', 'true');
  });

  it('lists both labels when two versions share one content identity', async () => {
    // A method edited and then edited back gets a new label for content an
    // older label already holds: both rows carry the same identity.
    methodVersions = [
      version(1, V1, '2026-10-01T09:00:00Z', 'seg', 1, false),
      version(2, V2, '2026-10-05T09:00:00Z', 'seg', 0, false),
      version(3, V1, '2026-10-08T09:00:00Z', 'seg', 2, true),
    ];
    const view = await expandSegment();

    const toggle = await openPicker(view);
    const options = within(toggle).getAllByTestId('version-option');
    expect(options.map(o => o.querySelector('.version-label')?.textContent)).toEqual(['v3', 'v2', 'v1']);
    expect(options[0]).toHaveTextContent('latest');
  });
});
