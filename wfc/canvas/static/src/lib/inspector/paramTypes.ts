/**
 * Single source of truth for classifying a param into the editor/validator
 * type the paramEditor machine consumes.
 *
 * The kind comes from `contractType` alone, the method.yaml type narrowed to
 * the parameter vocabulary. Every widget that needs "what kind of value is
 * this?" must call this function rather than re-deriving the answer from the
 * raw fields — independent derivations can disagree between the input widget
 * and the validator. `any`, `str` and an unknown type edit as text.
 */
import type { ParamDef } from '../shared/types.js';
import type { ParamEditorType } from '../machines/paramEditor.machine.js';

export function classifyParamType(param: ParamDef): ParamEditorType {
  const ct = param.contractType ?? 'unknown';
  if (ct === 'bool') return 'bool';
  if (param.constraints?.enum && param.constraints.enum.length > 0) return 'enum';
  if (ct === 'int') return 'int';
  if (ct === 'float') return 'float';
  if (ct === 'list') return 'list';
  if (ct === 'dict') return 'dict';
  return 'string';
}
