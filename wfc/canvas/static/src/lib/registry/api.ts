/**
 * Network calls for the Registry tab.
 *
 * The three list endpoints behind the modules / methods / samples sub-tabs, a
 * method's detail payload, the per-method validate POST, the registration POST
 * behind the register modal's Dry Run and Register buttons, the filesystem
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

/** The body of `/api/registry/methods/:module/:method/detail`. */
export type MethodDetail = { files: FileEntry[]; contract: Contract };

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

/** One row of the methods sub-tab. */
export type MethodRow = {
  name: string;
  module: string;
  env: string;
  validated: boolean | null;
  runCount: number;
  source: string;
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

/** One row of the envs sub-tab. */
export type EnvRow = {
  spec: string;
  methods: string[];
  backend: string | null;
  has_packages: boolean;
  last_run_at: string | null;
  run_count: number;
};

/** Where a captured package came from. */
export type PackageSource = 'conda' | 'pixi' | 'pip';

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

// ---------- Method detail and validation ----------

/**
 * Fetch one method's files and contract, for an expanded method row.
 *
 * Args:
 *     mod: The owning module's name.
 *     meth: The method's name.
 *
 * Returns:
 *     The detail payload, or the `HTTP <status>: <text>` message the row
 *     renders instead.
 */
export async function fetchMethodDetail(
  mod: string,
  meth: string
): Promise<MethodDetailResult> {
  const resp = await fetch(
    `/api/registry/methods/${encodeURIComponent(mod)}/${encodeURIComponent(meth)}/detail`
  );
  if (!resp.ok) {
    return { ok: false, error: `HTTP ${resp.status}: ${await resp.text()}` };
  }
  return { ok: true, detail: await resp.json() };
}

/**
 * Re-validate one registered method against its declared contract.
 *
 * Args:
 *     mod: The owning module's name.
 *     method: The method's name.
 *
 * Returns:
 *     The validate response, whose `validated` field updates the row.
 *
 * Raises:
 *     Error: On a non-2xx response, carrying the body's `detail` when it is a
 *         JSON `HTTPException` shape and the raw text otherwise.
 */
export async function validateRegisteredMethod(
  mod: string,
  method: string
): Promise<any> {
  const resp = await fetch('/api/registry/methods/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ module: mod, method }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    // Prefer JSON-body .detail when available (our HTTPException shape);
    // fall back to raw text for plain-text error bodies.
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed.detail === 'string') detail = parsed.detail;
    } catch { /* not JSON */ }
    throw new Error(`HTTP ${resp.status}: ${detail}`);
  }
  return resp.json();
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
 * server answers with the pre-checks and writes nothing.
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
 *     Error: `HTTP <status>: <text>` on a non-2xx response.
 */
export async function fetchEnvPackages(spec: string): Promise<PackagesResponse> {
  const resp = await fetch(`/api/registry/envs/${encodeURIComponent(spec)}/packages`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
  return resp.json();
}
