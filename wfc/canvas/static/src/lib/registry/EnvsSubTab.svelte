<script lang="ts">
  import { fetchEnvs, fetchEnvPackages } from './api.js';
  import type { EnvRegistry, EnvRow, PackagesResponse, RegistryState } from './api.js';

  let { visible = true, filter = '' }: { visible?: boolean; filter?: string } = $props();

  let envs = $state<EnvRow[]>([]);
  let loadError = $state<string | null>(null);
  let loading = $state(false);

  // Expansion + per-row package cache.
  let expanded = $state<Set<string>>(new Set());
  let packagesBySpec = $state<Record<string, PackagesResponse>>({});
  let pkgError = $state<Record<string, string>>({});
  let pkgLoading = $state<Record<string, boolean>>({});
  let methodsTooltipOpen = $state<string | null>(null);

  async function refresh() {
    loading = true;
    loadError = null;
    try {
      envs = await fetchEnvs();
    } catch (err) {
      loadError = String(err);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if (visible) void refresh();
  });

  const q = $derived(filter.trim().toLowerCase());
  const filteredEnvs = $derived(
    q === '' ? envs : envs.filter(e => e.spec.toLowerCase().includes(q))
  );

  function fmtRelative(iso: string | null): string {
    if (!iso) return '—';
    const then = new Date(iso).getTime();
    const delta = Date.now() - then;
    if (delta < 60_000) return 'just now';
    const mins = Math.floor(delta / 60_000);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 14) return `${days}d ago`;
    const weeks = Math.floor(days / 7);
    if (weeks < 8) return `${weeks}w ago`;
    const months = Math.floor(days / 30);
    return `${months}mo ago`;
  }

  function pluralize(n: number, word: string): string {
    return `${n} ${word}${n === 1 ? '' : 's'}`;
  }

  async function loadPackages(spec: string) {
    pkgLoading = { ...pkgLoading, [spec]: true };
    try {
      const body: PackagesResponse = await fetchEnvPackages(spec);
      packagesBySpec = { ...packagesBySpec, [spec]: body };
      pkgError = { ...pkgError, [spec]: '' };
    } catch (err) {
      // The server's refusal (e.g. a package list that no longer matches
      // the record) names its fix; show the message without "Error: ".
      pkgError = { ...pkgError, [spec]: err instanceof Error ? err.message : String(err) };
    } finally {
      pkgLoading = { ...pkgLoading, [spec]: false };
    }
  }

  function toggleExpand(env: EnvRow) {
    const spec = env.spec;
    const next = new Set(expanded);
    if (next.has(spec)) {
      next.delete(spec);
    } else {
      next.add(spec);
      // Only fetch when a package list was captured; for byo / legacy
      // envs the empty state is driven by the row's backend with no
      // round-trip (the list endpoint already told us has_packages).
      if (env.has_packages && !packagesBySpec[spec] && !pkgLoading[spec]) {
        void loadPackages(spec);
      }
    }
    expanded = next;
  }

  // Empty-state copy for an env with no captured package list: byo images
  // carry no source artifact; everything else is an older env registered
  // before package capture (or never rebuilt since).
  function emptyStateMessage(backend: string | null): string {
    return backend === 'byo'
      ? 'No package manifest — bring-your-own image'
      : 'Not captured — re-register this env to record its packages';
  }

  const REGISTRY_LABEL: Record<RegistryState, string> = {
    pushed: 'pushed',
    not_pushed: 'not pushed',
    none: 'no registry',
    cannot_push: 'cannot push',
    missing: 'missing',
    unknown: 'unknown',
  };

  // The bare env name the CLI takes (a legacy spec carries `container:`).
  function envName(spec: string): string {
    return spec.startsWith('container:') ? spec.slice('container:'.length) : spec;
  }

  // The Registry chip's tooltip: where the image is and the command that
  // fixes it, then the older images and versions the counts stand for.
  function registryTooltip(spec: string, reg: EnvRegistry): string {
    const push = `wfc push-env ${envName(spec)}`;
    const recreate = `recreate it with \`${reg.recreate}\`, then \`wfc register-method\``;
    const lines: string[] = [];
    if (reg.state === 'pushed' && reg.own_registry) lines.push(`Image is in its own registry: ${reg.own_registry}`);
    else if (reg.state === 'pushed') lines.push(`Image is in ${reg.base ?? 'the registry'}`);
    else if (reg.state === 'not_pushed') lines.push(`Not pushed to ${reg.base}: run \`${push}\``);
    else if (reg.state === 'none') lines.push(`No registry: this image stays on this machine; \`${push}\` sets one and pushes it`);
    else if (reg.state === 'cannot_push') lines.push(`Cannot be pushed: ${reg.reason ?? 'the env name is not a registry repository name'}`);
    else if (reg.state === 'missing') lines.push(`Not on this machine or in the registry: ${recreate}`);
    else lines.push('Docker is not running: cannot tell whether the image is on this machine');
    if (reg.older_not_pushed > 0) {
      lines.push(`${pluralize(reg.older_not_pushed, 'older image')} not pushed: run \`${push}\``);
    }
    if (reg.older_missing > 0) {
      lines.push(`${pluralize(reg.older_missing, 'older version')} with no image: ${recreate}`);
    }
    return lines.join('\n');
  }

  function toggleMethodsTooltip(spec: string) {
    methodsTooltipOpen = methodsTooltipOpen === spec ? null : spec;
  }

  function onMethodClick(_methodPath: string) {
    // Future: emit an event to RegistryTab to jump to the Methods sub-tab
    // filtered to this method. Deferred — this is a small UX polish.
  }
</script>

<div class="envs" style:display={visible ? 'block' : 'none'}>
  {#if loadError}
    <div class="error">Failed to load envs: {loadError}</div>
  {:else if loading && envs.length === 0}
    <div class="empty">Loading…</div>
  {:else if filteredEnvs.length === 0}
    <div class="empty">No envs registered.</div>
  {:else}
    <div class="body">
      <table class="reg-table">
        <thead>
          <tr>
            <th class="spec-header">Env Spec</th>
            <th>Methods</th>
            <th>Backend</th>
            <th>Registry</th>
            <th>Last Run</th>
            <th>Runs</th>
          </tr>
        </thead>
        <tbody>
          {#each filteredEnvs as env}
            {@const isExpanded = expanded.has(env.spec)}
            <tr
              class="env-row"
              class:expanded={isExpanded}
              data-testid="env-row"
              onclick={() => toggleExpand(env)}
            >
              <td class="mono">
                <span class="chevron">{isExpanded ? '▼' : '▶'}</span>
                <span class="chip chip-env">{env.spec}</span>
              </td>
              <td onclick={(e) => { e.stopPropagation(); toggleMethodsTooltip(env.spec); }}>
                <span class="methods-count">{pluralize(env.methods.length, 'method')}</span>
                {#if methodsTooltipOpen === env.spec}
                  <div class="methods-tooltip">
                    <div class="label">Methods using {env.spec}</div>
                    {#each env.methods as m}
                      <div class="tooltip-method" onclick={() => onMethodClick(m)}>{m}</div>
                    {/each}
                  </div>
                {/if}
              </td>
              <td>
                {#if env.backend}
                  <span class="chip chip-backend backend-{env.backend}">{env.backend}</span>
                {:else}
                  <span class="dim">—</span>
                {/if}
              </td>
              <td>
                {#if env.registry}
                  {@const reg = env.registry}
                  <span class="registry-cell" data-testid="registry-cell" title={registryTooltip(env.spec, reg)}>
                    <span class="chip chip-registry registry-{reg.state}" data-testid="registry-chip">{REGISTRY_LABEL[reg.state]}</span>
                    {#if reg.older_not_pushed > 0}
                      <span class="registry-count" data-testid="older-not-pushed">{reg.older_not_pushed} older not pushed</span>
                    {/if}
                    {#if reg.older_missing > 0}
                      <span class="registry-count registry-count-missing" data-testid="older-missing">{reg.older_missing} older missing</span>
                    {/if}
                  </span>
                {:else}
                  <span class="dim">—</span>
                {/if}
              </td>
              <td class="mono dim">{fmtRelative(env.last_run_at)}</td>
              <td class="mono dim">{env.run_count}</td>
            </tr>

            {#if isExpanded}
              <tr class="detail-row">
                <td colspan="6">
                  <div class="label">Packages</div>
                  {#if !env.has_packages}
                    <div class="dim empty-pkgs" data-testid="packages-empty">{emptyStateMessage(env.backend)}</div>
                  {:else if pkgError[env.spec]}
                    <div class="error">{pkgError[env.spec]}</div>
                  {:else if !packagesBySpec[env.spec]}
                    <div class="dim">Loading packages…</div>
                  {:else if !packagesBySpec[env.spec].captured}
                    <div class="dim empty-pkgs" data-testid="packages-empty">{emptyStateMessage(packagesBySpec[env.spec].backend)}</div>
                  {:else if packagesBySpec[env.spec].packages.length === 0}
                    <div class="dim empty-pkgs" data-testid="packages-empty">No packages found in the captured manifest.</div>
                  {:else}
                    {@const pkgs = packagesBySpec[env.spec].packages}
                    <div class="pkg-list" data-testid="packages-panel">
                      {#each pkgs as pkg}
                        <div class="pkg-row" data-testid="package-row">
                          <span class="pkg-source src-{pkg.source}">{pkg.source}</span>
                          <span class="pkg-name mono">{pkg.name}=={pkg.version}</span>
                        </div>
                      {/each}
                    </div>
                  {/if}
                </td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .envs { display: block; }
  .body { padding: 0; }
  .empty, .error { padding: 28px 14px; color: #888; font-size: 12.5px; }
  .error { color: #E74C3C; }

  .reg-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  .reg-table thead th {
    text-align: left;
    color: #888;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.6px;
    padding: 6px 10px;
    border-bottom: 1px solid var(--border);
  }
  .spec-header { padding-left: 33px !important; }

  .reg-table tbody td {
    padding: 8px 10px;
    border-bottom: 1px solid rgba(62, 62, 66, 0.5);
    vertical-align: middle;
    position: relative;
  }
  .env-row { cursor: pointer; }
  .env-row:hover { background: #2d2d30; }
  .env-row.expanded { background: #2d2d30; }
  .chevron { color: #888; font-size: 9px; margin-right: 8px; display: inline-block; width: 8px; }

  .mono { font-family: "JetBrains Mono", Consolas, monospace; font-size: 11.5px; }
  .dim { color: #888; }

  .chip {
    display: inline-block;
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 2px;
    font-family: "JetBrains Mono", Consolas, monospace;
    border: 1px solid;
  }
  .chip-env { color: #9b59b6; background: rgba(155, 89, 182, 0.14); border-color: rgba(155, 89, 182, 0.35); }
  .backend-pixi { color: var(--accent); background: rgba(74, 144, 217, 0.14); border-color: rgba(74, 144, 217, 0.35); }
  .backend-conda { color: #50C878; background: rgba(80, 200, 120, 0.14); border-color: rgba(80, 200, 120, 0.35); }
  .backend-byo { color: #E9A847; background: rgba(233, 168, 71, 0.14); border-color: rgba(233, 168, 71, 0.35); }
  .backend-uv { color: #C77DDB; background: rgba(199, 125, 219, 0.14); border-color: rgba(199, 125, 219, 0.35); }

  .registry-cell { display: inline-flex; align-items: center; gap: 6px; cursor: help; }
  .registry-pushed { color: #50C878; background: rgba(80, 200, 120, 0.14); border-color: rgba(80, 200, 120, 0.35); }
  .registry-not_pushed, .registry-cannot_push { color: #E9A847; background: rgba(233, 168, 71, 0.14); border-color: rgba(233, 168, 71, 0.35); }
  .registry-none, .registry-unknown { color: #888; background: rgba(136, 136, 136, 0.12); border-color: rgba(136, 136, 136, 0.35); }
  .registry-missing { color: #E74C3C; background: rgba(231, 76, 60, 0.14); border-color: rgba(231, 76, 60, 0.45); }
  .registry-count { color: #888; font-size: 10.5px; }
  .registry-count-missing { color: #E74C3C; }

  .methods-count { color: var(--accent); border-bottom: 1px dotted var(--accent); cursor: help; padding-bottom: 1px; font-size: 11.5px; font-family: "JetBrains Mono", Consolas, monospace; }

  .methods-tooltip {
    position: absolute;
    top: 30px;
    left: 10px;
    background: #252526;
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 8px 12px;
    font-size: 11px;
    line-height: 1.7;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
    z-index: 10;
    min-width: 200px;
  }
  .methods-tooltip .label {
    color: #888; font-size: 9px; text-transform: uppercase;
    letter-spacing: 0.6px; margin-bottom: 4px; font-weight: 600;
  }
  .tooltip-method {
    color: var(--accent); font-family: "JetBrains Mono", Consolas, monospace;
    cursor: pointer; font-size: 11px;
  }
  .tooltip-method:hover { color: #5aa0e9; }

  .detail-row td {
    background: #1e1e1e;
    padding: 14px 18px 18px 28px;
    border-bottom: 1px solid var(--border);
  }

  .label {
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.6px;
    color: #888;
    font-weight: 600;
    margin-bottom: 8px;
  }

  .empty-pkgs { font-size: 11.5px; }

  .pkg-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 420px;
    overflow: auto;
  }
  .pkg-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 2px 0;
  }
  .pkg-source {
    flex-shrink: 0;
    width: 42px;
    text-align: center;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 1px 0;
    border-radius: 2px;
    font-family: "JetBrains Mono", Consolas, monospace;
    border: 1px solid;
  }
  .src-conda { color: #50C878; background: rgba(80, 200, 120, 0.10); border-color: rgba(80, 200, 120, 0.30); }
  .src-pixi  { color: var(--accent); background: rgba(74, 144, 217, 0.10); border-color: rgba(74, 144, 217, 0.30); }
  .src-pip   { color: #E9A847; background: rgba(233, 168, 71, 0.10); border-color: rgba(233, 168, 71, 0.30); }
  .src-uv    { color: #C77DDB; background: rgba(199, 125, 219, 0.10); border-color: rgba(199, 125, 219, 0.30); }
  .pkg-name { color: #ccc; font-size: 11.5px; }
</style>
