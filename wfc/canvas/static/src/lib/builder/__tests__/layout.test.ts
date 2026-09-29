/**
 * Where a loaded node goes when its document gives no position.
 *
 * A document without positions — a synthesized lineage, a hand-written
 * pipeline — is laid out left to right in pipeline order: each node one
 * column right of its deepest parent, the nodes of a column stacked. A node
 * the document placed stays where it was put.
 */
import { describe, it, expect } from 'vitest';
import { layoutByDepth, COLUMN_GAP, ROW_GAP } from '../layout';

const link = (source: string, target: string) => ({ source, target });

describe('layoutByDepth', () => {
  it('places a chain one column per step, left to right', () => {
    const pos = layoutByDepth(
      [{ id: 'sel' }, { id: 'seg' }, { id: 'quant' }],
      [link('sel', 'seg'), link('seg', 'quant')],
    );
    expect(pos.seg.x - pos.sel.x).toBe(COLUMN_GAP);
    expect(pos.quant.x - pos.seg.x).toBe(COLUMN_GAP);
    expect(new Set([pos.sel.y, pos.seg.y, pos.quant.y]).size).toBe(1);
  });

  it('puts a node with two parents one column right of the deeper one', () => {
    // sel -> a -> b -> join, and sel -> join directly: join follows b.
    const pos = layoutByDepth(
      [{ id: 'sel' }, { id: 'a' }, { id: 'b' }, { id: 'join' }],
      [link('sel', 'a'), link('a', 'b'), link('b', 'join'), link('sel', 'join')],
    );
    expect(pos.join.x - pos.b.x).toBe(COLUMN_GAP);
  });

  it('stacks the nodes of one column without overlap', () => {
    const pos = layoutByDepth(
      [{ id: 'sel' }, { id: 'cyto' }, { id: 'tissue' }],
      [link('sel', 'cyto'), link('sel', 'tissue')],
    );
    expect(pos.cyto.x).toBe(pos.tissue.x);
    expect(Math.abs(pos.cyto.y - pos.tissue.y)).toBe(ROW_GAP);
  });

  it('leaves a positioned node alone and returns no position for it', () => {
    const pos = layoutByDepth(
      [{ id: 'sel', position: { x: 5, y: 7 } }, { id: 'seg' }],
      [link('sel', 'seg')],
    );
    expect(pos.sel).toBeUndefined();
    expect(pos.seg).toBeDefined();
  });

  it('puts a node nothing links in the first column', () => {
    const pos = layoutByDepth(
      [{ id: 'sel' }, { id: 'seg' }, { id: 'lonely' }],
      [link('sel', 'seg')],
    );
    expect(pos.lonely.x).toBe(pos.sel.x);
  });
});
