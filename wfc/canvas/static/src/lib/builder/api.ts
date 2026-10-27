/**
 * Network calls for the Builder area.
 *
 * Every Builder request lives here: the demo pipeline and the dev-mode probe
 * `App.svelte` makes at boot, the module registry the sidebar reads, the runs
 * preview's cache-status POST, the registered-samples list behind the
 * `samples` store, and the archive-status pair. The dev *toolbar*'s routes are
 * deliberately separate — see `devApi.ts` — because they are outside the
 * generated contract and served only in development.
 *
 * Each function owns its URL, method, body and response check. Callers keep
 * their own state writes and their own try/catch, so each caller decides how
 * a failure degrades.
 */
import type { ArchiveStatus } from './archiveStatus.js';
import { PARAM_TYPES } from '../shared/types.js';
import type {
  ContractType, MethodDef, MethodVersionDef, ModuleDef, ParamDef, PipelineJSON, SampleInfo, SlotDef,
} from '../shared/types.js';
import type { CacheStatusResponse } from './runsPreviewStatus.js';

/**
 * Fetch the scaffolded demo pipeline document.
 *
 * Returns:
 *     The parsed document, or ``null`` when no demo is scaffolded (non-2xx),
 *     which leaves the canvas boot inert.
 */
export async function fetchDemoPipelineDocument(): Promise<unknown | null> {
  const resp = await fetch('/api/pipelines/demo');
  if (!resp.ok) return null;
  return resp.json();
}

/**
 * Probe whether the server is running in development mode.
 *
 * Returns:
 *     The parsed status payload, or ``null`` on a non-2xx response. Rejects
 *     on a wire failure; the caller swallows it.
 */
export function fetchDevStatus(): Promise<{ dev?: boolean } | null> {
  return fetch('/api/dev/status').then(r => (r.ok ? r.json() : null));
}

/**
 * Fetch the registered module/method registry.
 *
 * Returns:
 *     The raw ``{module_name: {description, methods: {...}}}`` payload, or
 *     ``null`` on a non-2xx response. The sidebar owns the transform into
 *     ``ModuleDef[]``.
 */
export async function fetchModuleRegistry(): Promise<Record<string, unknown> | null> {
  const resp = await fetch('/api/modules');
  if (!resp.ok) return null;
  return resp.json();
}

/** Module accent colours, by the module's position in the registry. */
const MODULE_COLORS = ['#1ABC9C', '#2ecc71', '#9b59b6', '#E9A847', '#4A90D9', '#E74C3C', '#3498db', '#e67e22'];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Raw = any;

function slotsFrom(raw: Raw, withMulti: boolean): SlotDef[] {
  return Object.entries(raw || {}).map(([slotName, slotData]: [string, Raw]) => {
    const slot: SlotDef = {
      name: slotName,
      type: slotData?.type || 'csv',
      description: slotData?.description ?? undefined,
    };
    if (withMulti) slot.multi = slotData?.multiple || false;
    return slot;
  });
}

function paramsFrom(raw: Raw): ParamDef[] {
  return Object.entries(raw || {}).map(([pName, pData]: [string, Raw]) => {
    const rawType = (pData.type ?? 'str') as string;
    const contractType: ContractType = (PARAM_TYPES as readonly string[]).includes(rawType)
      ? (rawType as ContractType)
      : 'unknown';
    return {
      name: pName,
      type: rawType,
      contractType,
      // Preserve native dict/list defaults; only coerce primitives to string.
      // Without this, `{}.toString()` becomes the literal
      // string "[object Object]" and every dict-defaulted param gets corrupted.
      default: pData.default !== undefined && pData.default !== null
        ? (typeof pData.default === 'object' ? pData.default : pData.default.toString())
        : undefined,
      description: pData.description ?? undefined,
      required: pData.required || false,
      constraints: pData.constraints || undefined,
    };
  });
}

/**
 * Turn the `/api/modules` payload into the palette's `ModuleDef[]`.
 *
 * Each method carries its versions in label order (newest last), each with
 * its own contract; the method's own slots and params are its newest
 * version's, which is what a node that follows the newest draws.
 *
 * Args:
 *     raw: ``{module_name: {description, methods: {method_name: {...}}}}``.
 *
 * Returns:
 *     One ``ModuleDef`` per module, in the payload's order.
 */
export function moduleDefsFromRegistry(raw: Record<string, unknown>): ModuleDef[] {
  return Object.entries(raw).map(([modName, modData]: [string, Raw], i: number) => ({
    name: modName,
    description: modData.description,
    color: MODULE_COLORS[i % MODULE_COLORS.length],
    methods: Object.entries(modData.methods || {}).map(([methName, methData]: [string, Raw]): MethodDef => ({
      name: methName,
      module: modName,
      description: methData.description,
      versions: (methData.versions || []).map((v: Raw): MethodVersionDef => ({
        label: v.label,
        identity: v.identity,
        newest: !!v.newest,
        registeredAt: v.registered_at ?? undefined,
        env: v.env ?? undefined,
        inputs: slotsFrom(v.inputs, true),
        outputs: slotsFrom(v.outputs, false),
        params: paramsFrom(v.params_schema),
      })),
      inputs: slotsFrom(methData.inputs, true),
      outputs: slotsFrom(methData.outputs, false),
      params: paramsFrom(methData.params_schema),
      color: MODULE_COLORS[i % MODULE_COLORS.length],
    })),
  }));
}

/**
 * Ask the backend what the pipeline would do if it ran now.
 *
 * Posts the pipeline document exactly as the run route receives it; the
 * route prepares and loads it the way a run does and answers one row per
 * engine target. Edges, slots, modules and bundles are never sent
 * separately: the document carries them.
 *
 * Args:
 *     pipeline: The exported canvas pipeline document.
 *
 * Returns:
 *     Every target's row, or ``blocked_reason`` when the document is refused.
 *
 * Raises:
 *     Error: On any non-2xx response or wire failure.
 */
export async function fetchCacheStatus(pipeline: PipelineJSON): Promise<CacheStatusResponse> {
  const resp = await fetch('/api/wfc/cache-status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(pipeline),
  });
  if (!resp.ok) throw new Error(`status ${resp.status}: ${await resp.text()}`);
  return resp.json();
}

/**
 * Fetch the registered samples.
 *
 * Returns:
 *     The sample list, or ``null`` on a non-2xx response.
 */
export async function fetchSamples(): Promise<SampleInfo[] | null> {
  const resp = await fetch('/api/wfc/samples');
  if (!resp.ok) return null;
  return resp.json();
}

/**
 * Fetch the current archive status.
 *
 * Returns:
 *     The status payload, or ``null`` on a non-2xx response.
 */
export async function fetchArchiveStatus(): Promise<ArchiveStatus | null> {
  const resp = await fetch('/api/wfc/archive-status');
  if (!resp.ok) return null;
  return resp.json();
}

/**
 * Start an archive pass. The response is deliberately not inspected — a 409
 * (a pass already running, or a pipeline in flight) needs no special handling,
 * because the caller's follow-up status refresh already reflects whatever is
 * actually running.
 */
export async function postStartArchive(): Promise<void> {
  await fetch('/api/wfc/cache/archive', { method: 'POST' });
}
