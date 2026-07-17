/**
 * Single source of truth for classifying a param into the editor/validator
 * type the paramEditor machine consumes.
 *
 * A param's kind is described by two fields that must be read together:
 * `contractType` carries the precise method.yaml type, while the legacy
 * `type` field collapses int and float into 'number'. Every widget that
 * needs "what kind of value is this?" must call this function rather than
 * re-deriving the answer from the raw fields — independent derivations can
 * (and did) disagree between the input widget and the validator.
 */
import type { ParamDef } from './types.js';
import type { ParamEditorType } from './machines/paramEditor.machine.js';

export function classifyParamType(param: ParamDef): ParamEditorType {
  const ct = param.contractType ?? 'unknown';
  if (param.type === 'boolean' || ct === 'bool') return 'bool';
  if (param.constraints?.enum && param.constraints.enum.length > 0) return 'enum';
  if (ct === 'int') return 'int';
  // A numeric param with no precise contract type falls back to float:
  // float validation accepts whole numbers, int validation would wrongly
  // reject fractions.
  if (ct === 'float' || param.type === 'number') return 'float';
  if (ct === 'list' || param.type === 'list') return 'list';
  if (ct === 'dict' || param.type === 'dict') return 'dict';
  return 'string';
}
