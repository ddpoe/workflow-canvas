/**
 * A param's `options_from` reaches the canvas from `/api/modules`.
 *
 * `options_from` names the input (or inputs) whose upstream output's options
 * a string param's editor offers in its picker. It is declared in
 * method.yaml and served in `params_schema`; the palette transform must carry
 * it onto the ParamDef the Inspector reads, for the method and for each of
 * its versions, in both its single and its list form.
 */
import { describe, it, expect } from 'vitest';
import { moduleDefsFromRegistry } from '../api';

const schema = {
  value_column: { type: 'str', options_from: 'data' },
  join_key: { type: 'str', options_from: ['cells', 'annotations'] },
};

const [mod] = moduleDefsFromRegistry({
  col_demo: {
    methods: {
      plot_column: {
        params_schema: schema,
        versions: [{ label: 1, newest: true, params_schema: schema }],
      },
    },
  },
});
const method = mod.methods[0];

describe('the palette transform keeps a param\'s options_from', () => {
  it('on the method a new node draws from', () => {
    const byName = Object.fromEntries(method.params.map(p => [p.name, p]));
    expect(byName.value_column.options_from).toBe('data');
    expect(byName.join_key.options_from).toEqual(['cells', 'annotations']);
  });

  it('on each version a pinned node draws from', () => {
    const version = method.versions![0];
    const byName = Object.fromEntries(version.params.map(p => [p.name, p]));
    expect(byName.value_column.options_from).toBe('data');
    expect(byName.join_key.options_from).toEqual(['cells', 'annotations']);
  });
});
