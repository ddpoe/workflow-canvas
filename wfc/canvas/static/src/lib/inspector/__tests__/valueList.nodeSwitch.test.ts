/**
 * Switching the Inspector between nodes leaves no row behind in the commit set.
 *
 * The Inspector reuses one `ValueList` per param across node selections, so a
 * selection change is a `nodeId` prop change on a mounted component: the old
 * node's row actors are torn down and the new node's spawned. Each torn-down
 * row must leave `paramEditorAggregator`, or Lock All and Run go on asking
 * about a row nobody can see — and, the actor being stopped, can never commit.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';
import { tick } from 'svelte';
import ValueList from '../ValueList.svelte';
import { awaitAllCommitted, dirtyEditorIds, hasDirtyEditors } from '../../machines/root';
import type { ParamDef } from '../../shared/types';

afterEach(() => {
  cleanup();
});

const diameter: ParamDef = { name: 'nuclear_diameter', type: 'number', contractType: 'float', required: false };

function props(nodeId: string) {
  return { nodeId, param: diameter, baseValue: 41, onBaseChange: () => {}, singleValue: true };
}

/** The node ids that still have a row in the commit set. */
function nodesWithRows(): string[] {
  return [...new Set(dirtyEditorIds().map(id => id.split('::')[0]))];
}

describe('switching nodes in the Inspector', () => {
  it('leaves only the shown node\'s rows in the commit set, and Lock All settles every one', async () => {
    const { rerender } = render(ValueList, { props: props('node_a') });
    await tick();
    await rerender(props('node_b'));
    await tick();
    await rerender(props('node_a'));
    await tick();

    // Rows open unlocked on spawn, so the shown node's row is the one dirty row.
    expect(nodesWithRows()).toEqual(['node_a']);

    await awaitAllCommitted();
    expect(hasDirtyEditors()).toBe(false);
  });

  it('leaves no row behind when the Inspector closes', async () => {
    const { rerender, unmount } = render(ValueList, { props: props('node_a') });
    await tick();
    await rerender(props('node_b'));
    await tick();
    unmount();
    await tick();

    expect(dirtyEditorIds()).toEqual([]);
  });
});
