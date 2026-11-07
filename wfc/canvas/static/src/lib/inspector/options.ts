/**
 * The options picker's rules, as pure functions.
 *
 * An `options_from` param's lookup (`./nodeEdits.ts`) answers one entry per
 * upstream wired to the inputs it names. These functions turn that answer
 * into what the picker draws: one section per upstream that offers options,
 * each with a header (the node chip, the input it arrives on, the count) and
 * its Fixed, Built-from and Patterns groups; which sections start open; the
 * filter; the ticks; and the chevron's state and hover. Nothing here reads a
 * store or renders; the picker component calls these with what it holds.
 */
import { moduleColor } from '../shared/moduleColor.svelte.js';

/** The names one `from_params` entry builds, and the params it builds them from. */
export interface BuiltGroup {
  params: string[];
  names: string[];
}

/**
 * What the options route answers for one upstream output slot.
 *
 * `module` and `method` are present on a Run Reference's answer only: the
 * canvas knows a referenced run by its id, and the run names its method.
 */
export interface OutputOptions {
  fixed: string[];
  built: BuiltGroup[];
  patterns: string[];
  all: string[];
  module?: string;
  method?: string;
}

/** What kind of node an upstream is: a method, a Run Reference, or a system node. */
export type UpstreamKind = 'method' | 'run_reference' | 'system';

/** One upstream wired to one of a param's `options_from` inputs, with its answer. */
export interface UpstreamOptions {
  /** The downstream input the edge arrives on. */
  input: string;
  nodeId: string;
  /** The upstream's output slot the edge leaves from. */
  slot: string;
  /** The node's canvas label, which the no-options hover names. */
  nodeLabel: string;
  kind: UpstreamKind;
  /** A method upstream's module and method; blank for a run or a system node. */
  module: string;
  method: string;
  /** A Run Reference's run. */
  runId?: string;
  /**
   * A method upstream that runs a draft (pinned to the draft, or with no
   * registered version): not asked, since its options appear once it is
   * registered.
   */
  draft?: boolean;
  /** The lookup's answer; ``null`` when it failed or was not asked. */
  options: OutputOptions | null;
}

/** One param's lookup answer: the inputs it lists and every upstream wired to them. */
export interface ParamOptions {
  inputs: string[];
  /** In `options_from` order, then edge order within an input. */
  upstreams: UpstreamOptions[];
}

/** One upstream's section of the picker's list. */
export interface OptionSection {
  /** Unique within a row's sections: input, node and slot. */
  key: string;
  nodeId: string;
  input: string;
  kind: UpstreamKind;
  /** The method the chip and the Built-from pills name. */
  method: string;
  runId?: string;
  chip: { text: string; color: string };
  fixed: string[];
  built: BuiltGroup[];
  patterns: string[];
  /** Fixed names plus built names plus patterns, each once in this section. */
  count: number;
}

/** The value box's chevron: whether it opens the list, how it looks, its hover. */
export interface ChevronState {
  enabled: boolean;
  dimmed: boolean;
  hover: string;
}

/**
 * The inputs a param's `options_from` lists, in order.
 *
 * Args:
 *     optionsFrom: The declared value: one input name or a list.
 *
 * Returns:
 *     The input names; empty for a param that declares none.
 */
export function optionsFromInputs(optionsFrom: string | string[] | undefined): string[] {
  if (!optionsFrom) return [];
  return Array.isArray(optionsFrom) ? [...optionsFrom] : [optionsFrom];
}

/**
 * Turn a param's lookup answer into the picker's sections.
 *
 * An upstream whose lookup failed, or that offers no option, has no
 * section. Within a section a name appears once, at its first place (Fixed,
 * then the Built-from groups in order); a group left with no name is
 * dropped; patterns appear once each. The node chip takes the module's
 * colour from `moduleColor`, so reading the sections inside a `$derived`
 * follows a colour change.
 *
 * Args:
 *     p: The param's lookup answer; ``null`` or absent while it is pending.
 *
 * Returns:
 *     One section per upstream that offers options, in the answer's order.
 */
export function optionSections(p: ParamOptions | null | undefined): OptionSection[] {
  const sections: OptionSection[] = [];
  for (const u of p?.upstreams ?? []) {
    const o = u.options;
    if (!o) continue;
    const seen = new Set<string>();
    const once = (names: string[]) => names.filter(n => !seen.has(n) && (seen.add(n), true));
    const fixed = once(o.fixed ?? []);
    const built = (o.built ?? [])
      .map(g => ({ params: [...g.params], names: once(g.names) }))
      .filter(g => g.names.length > 0);
    const patterns = [...new Set(o.patterns ?? [])];
    const count = fixed.length + built.reduce((n, g) => n + g.names.length, 0) + patterns.length;
    if (count === 0) continue;
    const isRun = u.kind === 'run_reference';
    const method = isRun ? (o.method ?? u.nodeLabel) : u.method;
    const module = isRun ? (o.module ?? '') : u.module;
    sections.push({
      key: `${u.input}::${u.nodeId}::${u.slot}`,
      nodeId: u.nodeId,
      input: u.input,
      kind: u.kind,
      method,
      runId: u.runId,
      chip: {
        text: isRun ? `${method} · run ${u.runId ?? ''}` : method,
        color: moduleColor(module),
      },
      fixed,
      built,
      patterns,
      count,
    });
  }
  return sections;
}

/**
 * Whether the sections fold: several sections each get a fold chevron, one
 * section gets none.
 */
export function sectionsFold(sections: OptionSection[]): boolean {
  return sections.length > 1;
}

/**
 * Build the test a glob pattern makes: `*` matches any run of characters
 * (none included), every other character matches itself, and the whole
 * value must match.
 *
 * Args:
 *     pattern: The glob, as the output declares it.
 *
 * Returns:
 *     A predicate over values.
 */
export function globTest(pattern: string): (value: string) => boolean {
  const body = pattern
    .split('*')
    .map(part => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('.*');
  const re = new RegExp(`^${body}$`, 's');
  return value => re.test(value);
}

/** Whether a value fits a glob pattern (see `globTest`). */
export function fitsPattern(value: string, pattern: string): boolean {
  return globTest(pattern)(value);
}

/**
 * A pattern's fixed part: the text before its first `*`, which clicking
 * the pattern fills into the box.
 */
export function patternFixedPart(pattern: string): string {
  const star = pattern.indexOf('*');
  return star < 0 ? pattern : pattern.slice(0, star);
}

/**
 * What a value ticks in one section: the options equal to it and the
 * patterns it fits.
 *
 * Args:
 *     section: The section.
 *     value: The row's current text; surrounding space is ignored.
 *
 * Returns:
 *     The ticked names and patterns; both empty when the value is not among
 *     the section's options.
 */
export function tickedOptions(
  section: OptionSection,
  value: string,
): { names: string[]; patterns: string[] } {
  const v = value.trim();
  if (!v) return { names: [], patterns: [] };
  const all = [...section.fixed, ...section.built.flatMap(g => g.names)];
  return {
    names: all.filter(n => n === v),
    patterns: section.patterns.filter(p => fitsPattern(v, p)),
  };
}

/** Whether a section holds a value: an option equal to it or a pattern it fits. */
export function sectionHoldsValue(section: OptionSection, value: string): boolean {
  const t = tickedOptions(section, value);
  return t.names.length > 0 || t.patterns.length > 0;
}

/**
 * The keys of the sections open when the list opens.
 *
 * With one section, or no value, every section is open. Otherwise the
 * sections holding the value are open and the rest are folded; a value no
 * section holds opens them all, so the list never opens with everything
 * folded.
 *
 * Args:
 *     sections: The row's sections.
 *     value: The row's current text.
 *
 * Returns:
 *     The open sections' keys, in section order.
 */
export function openSectionKeys(sections: OptionSection[], value: string): string[] {
  const all = sections.map(s => s.key);
  if (sections.length <= 1 || !value.trim()) return all;
  const holding = sections.filter(s => sectionHoldsValue(s, value)).map(s => s.key);
  return holding.length > 0 ? holding : all;
}

/**
 * Filter one section by the text typed in the box.
 *
 * A name stays when it contains the text, ignoring case. A pattern stays
 * when its fixed part starts with the text or the text starts with its
 * fixed part, so a pattern stays in view while the user types past it.
 * Groups left empty are dropped; the header's count is the section's, not
 * the filtered one.
 *
 * Args:
 *     section: The section.
 *     text: The typed text; empty keeps everything.
 *
 * Returns:
 *     The section with only what the filter keeps.
 */
export function filterSection(section: OptionSection, text: string): OptionSection {
  const f = text.trim().toLowerCase();
  if (!f) return section;
  const keep = (n: string) => n.toLowerCase().includes(f);
  return {
    ...section,
    fixed: section.fixed.filter(keep),
    built: section.built
      .map(g => ({ params: g.params, names: g.names.filter(keep) }))
      .filter(g => g.names.length > 0),
    patterns: section.patterns.filter(p => {
      const fx = patternFixedPart(p).toLowerCase();
      return fx.startsWith(f) || f.startsWith(fx);
    }),
  };
}

/** Whether a (filtered) section shows no option at all. */
export function sectionIsEmpty(section: OptionSection): boolean {
  return section.fixed.length === 0 && section.built.length === 0 && section.patterns.length === 0;
}

/** Whether the filtered list shows nothing, which the list says as "No match". */
export function noMatch(sections: OptionSection[]): boolean {
  return sections.every(sectionIsEmpty);
}

/**
 * The value box's chevron.
 *
 * Dimmed and disabled when no section remains, with a hover naming why:
 * nothing is wired to the listed inputs, a wired upstream is a draft (its
 * options appear once it is registered), or what is wired offers no
 * options. Otherwise dimmed and disabled on a locked row; those two hovers
 * win over the locked one. While the lookup is pending it is dimmed with no
 * hover.
 *
 * Args:
 *     p: The param's lookup answer; ``null`` or absent while pending.
 *     sections: Its sections (`optionSections`).
 *     locked: Whether the row is locked.
 *
 * Returns:
 *     Whether the chevron opens the list, whether it is dimmed, and its hover.
 */
export function chevronState(
  p: ParamOptions | null | undefined,
  sections: OptionSection[],
  locked: boolean,
): ChevronState {
  if (!p) return { enabled: false, dimmed: true, hover: '' };
  if (sections.length === 0) {
    if (p.upstreams.length === 0) {
      return { enabled: false, dimmed: true, hover: `Nothing wired to ${p.inputs.join(', ')}` };
    }
    const drafts = [...new Set(p.upstreams.filter(u => u.draft).map(u => u.nodeLabel))];
    if (drafts.length > 0) {
      const verb = drafts.length > 1 ? 'are' : 'is';
      return { enabled: false, dimmed: true, hover: `Options appear once ${drafts.join(', ')} ${verb} registered` };
    }
    const nodes = [...new Set(p.upstreams.map(u => u.nodeLabel))];
    return { enabled: false, dimmed: true, hover: `No options on ${nodes.join(', ')}` };
  }
  if (locked) return { enabled: false, dimmed: true, hover: 'Unlock the row to change it' };
  return { enabled: true, dimmed: false, hover: 'Show the options' };
}

/**
 * The hover of a Built-from `param ↗` pill.
 *
 * Args:
 *     section: The section the group is in.
 *     param: The parameter the pill names.
 *
 * Returns:
 *     Where the pill goes: the parameter on the upstream node, or the run's
 *     Params tab in History.
 */
export function builtPillTitle(section: OptionSection, param: string): string {
  if (section.kind === 'run_reference') {
    return `Open run ${section.runId ?? ''} in History on its Params tab`;
  }
  return `Go to ${param} on ${section.method}`;
}
