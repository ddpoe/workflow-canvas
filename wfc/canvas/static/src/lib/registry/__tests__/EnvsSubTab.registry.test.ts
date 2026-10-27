/**
 * The Envs table's Registry chip (UI half).
 *
 * EnvsSubTab renders each row's `registry` object from
 * `GET /api/registry/envs`: one chip per state (`pushed`, `not pushed`,
 * `no registry`, `missing` in red, `unknown`), the counts of older images
 * not pushed and older versions missing, and a tooltip naming the command
 * that fixes it (`wfc push-env`, or for missing the recreate commands). A
 * byo image pulled from its own registry is named by that registry, not the
 * project's base.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, fireEvent, cleanup } from '@testing-library/svelte';
import { within } from '@testing-library/dom';
import EnvsSubTab from '../EnvsSubTab.svelte';

const BASE = 'ghcr.io/lab/tools-project';
const RECREATE = 'wfc register-env tools --force';
const BYO_REF = 'ghcr.io/vendor/aligner@sha256:' + 'a'.repeat(64);

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function envRow(spec: string, registry: unknown) {
  return { spec, methods: ['m.run'], backend: 'pixi', has_packages: false,
           registry, last_run_at: null, run_count: 0 };
}

function chip(state: string, older_not_pushed = 0, older_missing = 0,
              base: string | null = BASE, own_registry: string | null = null) {
  return { state, base, older_not_pushed, older_missing, recreate: RECREATE, own_registry };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('EnvsSubTab Registry chip', () => {
  it('renders each state, the older counts and a tooltip naming the fix', async () => {
    globalThis.fetch = vi.fn(async () => jsonResponse({
      envs: [
        envRow('pushed-env', chip('pushed')),
        envRow('tools', chip('not_pushed', 2, 1)),
        envRow('local-env', chip('none', 0, 0, null)),
        envRow('container:gone', chip('missing')),
        envRow('down-env', chip('unknown')),
        envRow('unrecorded', null),
        envRow('byo-env', chip('pushed', 0, 0, BASE, BYO_REF)),
      ],
    })) as unknown as typeof fetch;

    const { findAllByTestId } = render(EnvsSubTab, { props: { visible: true } });
    const rows = await findAllByTestId('env-row');
    expect(rows).toHaveLength(7);
    const chips = rows.slice(0, 5).map(r => within(r).getByTestId('registry-chip'));
    expect(chips.map(c => c.textContent)).toEqual(
      ['pushed', 'not pushed', 'no registry', 'missing', 'unknown']);
    expect(chips[3]).toHaveClass('registry-missing');
    expect(within(rows[5]).queryByTestId('registry-chip')).toBeNull();

    const cell = (i: number) => within(rows[i]).getByTestId('registry-cell').getAttribute('title');
    expect(cell(0)).toBe(`Image is in ${BASE}`);
    expect(within(rows[1]).getByTestId('older-not-pushed')).toHaveTextContent('2 older not pushed');
    expect(within(rows[1]).getByTestId('older-missing')).toHaveTextContent('1 older missing');
    expect(cell(1)).toContain(`Not pushed to ${BASE}: run \`wfc push-env tools\``);
    expect(cell(1)).toContain('2 older images not pushed: run `wfc push-env tools`');
    expect(cell(1)).toContain(`1 older version with no image: recreate it with \`${RECREATE}\`, then \`wfc register-method\``);
    expect(cell(2)).toContain('No registry: this image stays on this machine');
    expect(cell(3)).toContain(`recreate it with \`${RECREATE}\`, then \`wfc register-method\``);
    expect(cell(3)).not.toContain('push-env');
    expect(cell(4)).toContain('Docker is not running');
    expect(within(rows[0]).queryByTestId('older-missing')).toBeNull();

    // A byo image lives in its own registry, whatever base the project sets.
    expect(within(rows[6]).getByTestId('registry-chip')).toHaveTextContent('pushed');
    expect(cell(6)).toBe(`Image is in its own registry: ${BYO_REF}`);
    expect(cell(6)).not.toContain(BASE);
  });

  it('says an env whose name no registry accepts cannot be pushed, with the reason', async () => {
    const reason = "env 'MyTools' cannot be pushed: a registry repository name is lower-case";
    globalThis.fetch = vi.fn(async () => jsonResponse({
      envs: [envRow('MyTools', { ...chip('cannot_push', 0, 0, null), reason })],
    })) as unknown as typeof fetch;

    const { findAllByTestId } = render(EnvsSubTab, { props: { visible: true } });
    const [row] = await findAllByTestId('env-row');
    expect(within(row).getByTestId('registry-chip')).toHaveTextContent('cannot push');
    const title = within(row).getByTestId('registry-cell').getAttribute('title');
    expect(title).toContain(reason);
    expect(title).not.toContain('wfc push-env');
  });
});
