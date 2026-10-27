<script lang="ts">
  /**
   * A labelled tag: a key half in its concept's colour and a neutral value
   * half, `version | v2`. The run detail's Cache-key card draws its hash
   * tags with it; the Registry names a module, method, version and env with
   * it.
   *
   * `kind` picks the concept colour (method, version, env); `color` sets
   * any other colour, which the tag's border and key take; with neither,
   * the tag takes the `--c` its parent sets (grey when none). `module` fills
   * the key half with the colour (the module's own, from `getModuleColor`)
   * and bolds the value. Any other attribute (a test id, data attributes, a
   * title) lands on the tag's root element.
   */
  type Kind = 'method' | 'version' | 'env' | 'module' | 'plain';

  let {
    label,
    value,
    kind = 'plain',
    color = undefined,
    ...rest
  }: {
    label: string;
    value: string;
    kind?: Kind;
    color?: string;
    [attr: string]: unknown;
  } = $props();
</script>

<span
  class="ltag k-{kind}"
  style={color ? `--c: ${color}` : undefined}
  {...rest}
><span class="k">{label}</span><span class="v">{value}</span></span>

<style>
  .ltag {
    display: inline-flex; align-items: stretch; vertical-align: middle;
    max-width: 100%; min-width: 0;
    border-radius: 3px; font-family: Consolas, monospace; font-size: 10px; line-height: 15px;
    border: 1px solid color-mix(in srgb, var(--c, #9a9da5) 35%, transparent);
    white-space: nowrap;
  }
  .k {
    flex-shrink: 0;
    padding: 0 4px; background: color-mix(in srgb, var(--c, #9a9da5) 18%, transparent);
    color: var(--c, #9a9da5); font-size: 9px; letter-spacing: 0.2px; border-radius: 2px 0 0 2px;
  }
  .v {
    min-width: 0; overflow: hidden; text-overflow: ellipsis;
    padding: 0 5px; background: rgba(255, 255, 255, 0.04); color: #c8ccd8;
    border-radius: 0 2px 2px 0;
  }
  .k-method { --c: #c2a77e; }
  .k-method .v { color: #e6e6e6; }
  .k-version { --c: #7ea893; }
  .k-env { --c: #a68fae; }
  .k-module { border-color: var(--c); }
  .k-module .k { background: var(--c); color: #111; font-weight: 700; }
  .k-module .v {
    color: #e6e6e6; font-weight: 600;
    background: color-mix(in srgb, var(--c) 14%, transparent);
  }
</style>
