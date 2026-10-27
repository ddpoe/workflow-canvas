/**
 * Network calls for the Registry tab.
 *
 * The three list endpoints behind the modules / methods / samples sub-tabs, a
 * method's detail payload and its version export, the registration POST
 * behind the register modal's check and its confirmation, the filesystem
 * browse its method form picks a directory from, and the env sub-tab's list and
 * package lookups.
 *
 * Each component calls these from the place it made the call itself. The URLs,
 * the response checks and the error strings are the components', moved
 * verbatim: where a component threw, the function throws the same `Error`;
 * where a component formatted a message inline without throwing, the function
 * returns that message on a result object so the rendered text is unchanged.
 */

// ---------- Response types ----------

/** One source file of a method, as the detail endpoint returns it. */
export type FileEntry = { name: string; language: string; content: string };

/** One input or output slot of a method's contract. */
export type SlotSpec = {
  type?: string;
  required?: boolean;
  multiple?: boolean;
  description?: string;
};

/** A method's declared contract. */
export type Contract = {
  input_slots: Record<string, SlotSpec>;
  output_slots: Record<string, SlotSpec>;
  params_schema: Record<string, any>;
  executor: string;
};

/**
 * One version of a method, as the methods list and the detail endpoint name
 * it. `identity` is the version's content identity, the string a pin holds
 * and the routes take; `label` is its display number. `commit` is the
 * registration commit, `null` for a version this database did not register
 * (it arrived by a pull or a fresh clone); `envFingerprint`, `image` and
 * `codeTree` are what the version records; `runCount` the runs that used it.
 */
export type DetailVersion = {
  label: number;
  identity: string;
  registered_at: string | null;
  env: string;
  newest: boolean;
  runCount: number;
  commit: string | null;
  envFingerprint: string;
  image: string;
  codeTree: string;
};

/**
 * The body of `/api/registry/methods/:module/:method/detail`: one version's
 * files, contract and env, and that version.
 */
export type MethodDetail = {
  files: FileEntry[];
  contract: Contract;
  env?: string;
  version?: DetailVersion;
};

/** One contract row of a module, as the modules list returns it. */
export type ContractRow = {
  type: string;
  name: string;
  value_type: string | null;
  required: boolean;
};

/** One row of the modules sub-tab. */
export type ModuleRow = {
  name: string;
  description: string;
  contracts: ContractRow[];
  methods: number;
  source: string;
};

/** One version of a method in the methods list. */
export type MethodVersionRow = DetailVersion;

/**
 * One row of the methods sub-tab. `source` is the registered copy's
 * `method.yaml`, project-relative, or `null` when the method's record does
 * not lead to a registered copy. `versions` lists every version in label
 * order (the newest last); `env` is the newest version's.
 */
export type MethodRow = {
  name: string;
  module: string;
  env: string;
  runCount: number;
  source: string | null;
  versions?: MethodVersionRow[];
};

/** One row of the samples sub-tab. */
export type SampleRow = {
  name: string;
  source: string;
  size: number | null;
  hash: string | null;
  pushed: boolean;
  runCount: number;
  registered_at: string | null;
  file_type: string;
};

/** What the register modal can register. One endpoint per kind. */
export type RegisterKind = 'module' | 'method' | 'sample';

/** One entry of a `/api/fs/browse` listing. */
export type FsEntry = { name: string; kind: 'dir' | 'file'; size?: number };

/** Where an env's current image is: the Registry chip's states. */
export type RegistryState = 'pushed' | 'not_pushed' | 'none' | 'cannot_push' | 'missing' | 'unknown';

/**
 * Where an env's images are, read from the captures folders and the local
 * Docker daemon (never the registry).
 */
export type EnvRegistry = {
  state: RegistryState;
  /** The project's registry base, or null when none is set. */
  base: string | null;
  /** Older images method versions record that are not pushed. */
  older_not_pushed: number;
  /** Method versions whose older image is neither here nor in the registry. */
  older_missing: number;
  /** The command that recreates the env's image. */
  recreate: string;
  /** A byo image pulled from its own registry: that ref; otherwise null. */
  own_registry: string | null;
  /** For `cannot_push`: why the env's name cannot be pushed, and the fixes; otherwise null. */
  reason: string | null;
};

/** One row of the envs sub-tab. */
export type EnvRow = {
  spec: string;
  methods: string[];
  backend: string | null;
  has_packages: boolean;
  /** Null for an env with no record. */
  registry: EnvRegistry | null;
  last_run_at: string | null;
  run_count: number;
};

/** Where a captured package came from. */
export type PackageSource = 'conda' | 'pixi' | 'pip' | 'uv';

/** One captured package of an env. */
export type Package = { name: string; version: string; source: PackageSource };

/** The body of `/api/registry/envs/:spec/packages`. */
export type PackagesResponse = {
  spec: string;
  backend: string | null;
  captured: boolean;
  packages: Package[];
};

/**
 * A method detail lookup: the payload, or the message the row renders in its
 * place. The caller writes the message into its per-row error map, so it is
 * returned rather than thrown.
 */
export type MethodDetailResult =
  | { ok: true; detail: MethodDetail }
  | { ok: false; error: string };

/**
 * A filesystem listing: the resolved path and its entries, or the message the
 * browse modal renders in their place.
 */
export type BrowseResult =
  | { ok: true; path: string; entries: FsEntry[] }
  | { ok: false; error: string };

/**
 * A registration POST. The endpoint answers with a body on success *and* on
 * failure — `detail` and `preChecks` on a rejection, `ok` and `preChecks` on
 * acceptance — so the status travels beside the parsed body instead of the
 * body standing in for it.
 */
export type RegisterResult = { ok: boolean; status: number; body: any };

/**
 * What an accepted method registration adds to its body: the source files the
 * registered copy left out, the advice naming them (`''` when none were left
 * out), and the message of the outcome it applied.
 */
export type MethodRegistered = { notCopied: string[]; advice: string; message?: string };

/** One slot or param a registration adds, removes or changes. */
export type PlanEntry = {
  name: string;
  /** Slots only. */
  kind?: 'input' | 'output';
  change: 'added' | 'removed' | 'changed';
  /** The whole declaration before (null when added) and after (null when removed). */
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
};

/**
 * What registering changes against the newest version, as Registration
 * states it. A new method has every slot and param added. `code` is null
 * when the newest version's files are no longer in git; `slots`, `params`
 * and `run` are then empty because nothing was compared. `run` holds each
 * other top-level key that changed (`script`, `executor`, `gpus`) as
 * `[before, after]`.
 */
export type PlanChanges = {
  code: { changed: string[]; added: string[]; removed: string[] } | null;
  slots: PlanEntry[];
  params: PlanEntry[];
  run: Record<string, [unknown, unknown]>;
  env: { before: string | null; after: string; rebuilt: boolean };
};

/** What a version holds: its main script, its helpers and the files left out. */
export type PlanHolds = { script: string; helpers: string[]; ignored: string[] };

/**
 * What a method registration's check plans, before anything is written: a
 * new method (registers v1), a new version (adds vN, naming what changed), or
 * the same content as the newest version (adds nothing). `message` is the
 * text `wfc register-method` prints for the same registration, rendered from
 * `changes`. `label` is the version it adds (or the newest it matches) and
 * `newestLabel` the newest before registering. `token` names what the plan
 * would write; the confirming POST sends it back as `plan_token`, and the
 * server refuses (409) when a fresh plan differs.
 */
export type RegistrationPlan = {
  outcome: 'new' | 'changed' | 'same';
  message: string;
  label: number;
  newestLabel: number | null;
  changes: PlanChanges;
  holds: PlanHolds;
  token: string;
};

// ---------- Lists ----------

/**
 * Fetch the rows of the modules sub-tab.
 *
 * The response is not status-checked: a non-2xx body has no `modules` key and
 * lands on the empty list, and a wire failure rejects into the caller's catch.
 *
 * Returns:
 *     The module rows, empty when the body carries none.
 */
export async function fetchRegistryModules(): Promise<ModuleRow[]> {
  const body = await fetch('/api/registry/modules').then(r => r.json());
  return body.modules ?? [];
}

/**
 * Fetch the rows of the methods sub-tab.
 *
 * Returns:
 *     The method rows, empty when the body carries none.
 */
export async function fetchRegistryMethods(): Promise<MethodRow[]> {
  const body = await fetch('/api/registry/methods').then(r => r.json());
  return body.methods ?? [];
}

/**
 * Fetch the rows of the samples sub-tab.
 *
 * Returns:
 *     The sample rows, empty when the body carries none.
 */
export async function fetchRegistrySamples(): Promise<SampleRow[]> {
  const body = await fetch('/api/registry/samples').then(r => r.json());
  return body.samples ?? [];
}

// ---------- Method detail and export ----------

/**
 * Fetch one version of a method's files, contract and env, for an expanded
 * method row.
 *
 * Args:
 *     mod: The owning module's name.
 *     meth: The method's name.
 *     version: The version's content identity; omitted, the newest version.
 *
 * Returns:
 *     The detail payload, or the `HTTP <status>: <text>` message the row
 *     renders instead.
 */
export async function fetchMethodDetail(
  mod: string,
  meth: string,
  version?: string
): Promise<MethodDetailResult> {
  const query = version ? `?version=${encodeURIComponent(version)}` : '';
  const resp = await fetch(
    `/api/registry/methods/${encodeURIComponent(mod)}/${encodeURIComponent(meth)}/detail${query}`
  );
  if (!resp.ok) {
    return { ok: false, error: `HTTP ${resp.status}: ${await resp.text()}` };
  }
  return { ok: true, detail: await resp.json() };
}

/**
 * The URL that downloads one version of a method as `<method>_v<N>.zip`:
 * every file of its registered copy, to unzip, edit and register again.
 *
 * Args:
 *     mod: The owning module's name.
 *     meth: The method's name.
 *     version: The version's content identity.
 *
 * Returns:
 *     The export route's URL for that version.
 */
export function methodExportUrl(mod: string, meth: string, version: string): string {
  return `/api/registry/methods/${encodeURIComponent(mod)}/${encodeURIComponent(meth)}`
    + `/export?version=${encodeURIComponent(version)}`;
}

/**
 * The error a non-2xx response becomes.
 *
 * Args:
 *     resp: The failed response.
 *
 * Returns:
 *     An `HTTP <status>: <detail>` error: the body's `detail` when it is a
 *     JSON `HTTPException` shape, the raw text otherwise.
 */
async function httpError(resp: Response): Promise<Error> {
  const text = await resp.text();
  let detail = text;
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed.detail === 'string') detail = parsed.detail;
  } catch { /* not JSON */ }
  return new Error(`HTTP ${resp.status}: ${detail}`);
}

// ---------- Registration ----------

/** The endpoint that registers one kind of record. */
function endpointFor(k: RegisterKind): string {
  if (k === 'module') return '/api/registry/modules';
  if (k === 'method') return '/api/registry/methods';
  return '/api/registry/samples';
}

/**
 * Register a record, or dry-run the registration.
 *
 * A dry run posts the same body to the same endpoint with `?dryRun=true`; the
 * server answers with the pre-checks and writes nothing. A method's dry run
 * also carries the registration's `plan` (a `RegistrationPlan`); the method
 * POST without it sends the plan's `token` as `plan_token`, applies that plan
 * and returns its `message`, or answers 409 when the plan changed since.
 *
 * Args:
 *     kind: Which endpoint to post to.
 *     body: The form's payload for that kind.
 *     dryRun: Whether to ask for pre-checks only.
 *
 * Returns:
 *     Whether the endpoint accepted, its status, and the parsed body — which
 *     is `{}` when the response carries no JSON.
 */
export async function registerRecord(
  kind: RegisterKind,
  body: any,
  dryRun: boolean
): Promise<RegisterResult> {
  const url = endpointFor(kind) + (dryRun ? '?dryRun=true' : '');
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return { ok: resp.ok, status: resp.status, body: await resp.json().catch(() => ({})) };
}

/**
 * List one directory of the server's filesystem, for the method form's
 * directory picker.
 *
 * Args:
 *     path: The directory to list, relative to the server's browse root. The
 *         empty string lists that root.
 *
 * Returns:
 *     The resolved path and its entries, or the `HTTP <status>: <text>`
 *     message the browse modal renders instead.
 */
export async function browseFilesystem(path: string): Promise<BrowseResult> {
  const resp = await fetch(`/api/fs/browse?path=${encodeURIComponent(path)}`);
  if (!resp.ok) {
    return { ok: false, error: `HTTP ${resp.status}: ${await resp.text()}` };
  }
  const body = await resp.json();
  return { ok: true, path: body.path, entries: body.entries };
}

// ---------- Envs ----------

/**
 * Fetch the rows of the envs sub-tab.
 *
 * Returns:
 *     The env rows, empty when the body carries none.
 *
 * Raises:
 *     Error: `HTTP <status>: <text>` on a non-2xx response.
 */
export async function fetchEnvs(): Promise<EnvRow[]> {
  const resp = await fetch('/api/registry/envs');
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
  const body = await resp.json();
  return body.envs ?? [];
}

/**
 * Fetch the packages captured for one env spec.
 *
 * Args:
 *     spec: The env spec whose packages to list.
 *
 * Returns:
 *     The package payload for that spec.
 *
 * Raises:
 *     Error: `HTTP <status>: <detail>` on a non-2xx response, e.g. the 409
 *         for a captures folder changed outside wfc (a package list that no
 *         longer matches the env's record, or an unreadable image.json), whose
 *         detail names the remedy: register the env again under a new name.
 */
export async function fetchEnvPackages(spec: string): Promise<PackagesResponse> {
  const resp = await fetch(`/api/registry/envs/${encodeURIComponent(spec)}/packages`);
  if (!resp.ok) throw await httpError(resp);
  return resp.json();
}
