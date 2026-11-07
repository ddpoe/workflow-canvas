<script lang="ts">
  /**
   * The artifact viewer's image body: one image preview, zoomable and pannable.
   *
   * Opens on the image route's `view` size. Zoom is measured against the 1:1
   * size: the original's pixels when it is at most 4096 px on both sides,
   * otherwise the `large` size's. The `large` PNG is asked for only when 1:1
   * or a deeper zoom would enlarge the `view` one, and only when image-info
   * says it can be made. The image's facts come from image-info: whether it
   * is too large to decode, and the amber note when the preview differs from
   * the file. `shows` tells the viewer whether there is an image to zoom (not
   * one too large or unreadable). The viewer keys this body by path, so
   * stepping resets the zoom.
   */
  import { onMount, tick } from 'svelte';
  import { artifactImageUrl, fetchImageInfo } from './historyApi.js';
  import type { ImageInfo, ImageSize } from './historyApi.js';
  import { fitWithin, LARGE_SIDE, previewNote, tooLargeText } from './viewerFormat.js';

  interface Props {
    runId: string;
    path: string;
    name: string;
    /** The zoom readout the viewer's header shows: "Fit" or a percentage. */
    zoomLabel?: string;
    /** False once the image is known to be too large or unreadable: nothing to zoom. */
    shows?: boolean;
  }

  let { runId, path, name, zoomLabel = $bindable('Fit'), shows = $bindable(true) }: Props = $props();

  // Room the stage keeps around a fitted image: its padding and the image's.
  const PAD_X = 128;
  const PAD_Y = 56;
  const MIN_ZOOM = 0.05;
  const MAX_ZOOM = 8;
  const STEP = 1.25;
  const WHEEL_STEP = 1.15;

  let viewInfo = $state<ImageInfo | null>(null);
  let largeInfo = $state<ImageInfo | null>(null);
  let infoDone = $state(false);
  let largeAsked = false;
  let size = $state<ImageSize>('view');
  let loadedSrc = $state<string | null>(null);
  let errored = $state(false);
  /** The loaded image's own pixel size, for an image image-info cannot read (SVG). */
  let natural = $state<{ width: number; height: number } | null>(null);
  /** Zoom against the 1:1 size; null is Fit. */
  let zoom = $state<number | null>(null);
  let stage = $state<HTMLDivElement | null>(null);
  let stageSize = $state({ width: 0, height: 0 });
  let drag = $state<{ x: number; y: number; left: number; top: number } | null>(null);
  let pannable = $state(false);

  let src = $derived(artifactImageUrl(runId, path, size));
  let refused = $derived(viewInfo?.refused ? viewInfo : null);

  /** The 1:1 size: what `large` holds (the original when it fits 4096 px). */
  let oneToOneSize = $derived.by(() => {
    if (viewInfo) return fitWithin(viewInfo.width, viewInfo.height, LARGE_SIDE);
    return natural;
  });

  let fitZoom = $derived.by(() => {
    const one = oneToOneSize;
    if (!one || stageSize.width <= 0 || stageSize.height <= 0) return null;
    const z = Math.min((stageSize.width - PAD_X) / one.width, (stageSize.height - PAD_Y) / one.height);
    return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, z));
  });

  let scale = $derived(zoom ?? fitZoom);

  /** The image's size on screen, or null before the stage is measured. */
  let shown = $derived.by(() => {
    const one = oneToOneSize;
    if (!one || scale == null) return null;
    return { width: one.width * scale, height: one.height * scale };
  });

  let note = $derived.by(() => {
    const info = size === 'large' ? largeInfo : viewInfo;
    return info ? previewNote(info) : null;
  });

  $effect(() => {
    zoomLabel = zoom == null ? 'Fit' : `${Math.round(zoom * 100)}%`;
  });

  $effect(() => {
    shows = !refused && !(errored && infoDone);
  });

  // Ask for `large` once the zoom would enlarge `view` and `large` has more pixels.
  $effect(() => {
    if (largeAsked || !viewInfo || !shown || size !== 'view') return;
    const one = oneToOneSize;
    if (!one || one.width <= viewInfo.preview_width) return;
    if (shown.width <= viewInfo.preview_width + 0.5) return;
    largeAsked = true;
    fetchImageInfo(runId, path, 'large')
      .then(info => {
        largeInfo = info;
        if (!info.refused) size = 'large';
      })
      .catch(() => {
        // No facts for `large`: keep enlarging `view`.
      });
  });

  // Whether the image overflows its stage, so a drag pans it.
  $effect(() => {
    void shown;
    void stageSize;
    tick().then(() => {
      pannable = !!stage && (stage.scrollWidth > stage.clientWidth + 1 || stage.scrollHeight > stage.clientHeight + 1);
    });
  });

  onMount(() => {
    measure();
    fetchImageInfo(runId, path, 'view')
      .then(info => { viewInfo = info; })
      .catch(() => {
        // An SVG, or a file Pillow cannot read: no facts, no note.
      })
      .finally(() => { infoDone = true; });
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      setZoom((scale ?? 1) * (e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP), e);
    };
    stage?.addEventListener('wheel', onWheel, { passive: false });
    return () => stage?.removeEventListener('wheel', onWheel);
  });

  /** Re-read the stage's size: on load, on a window resize, entering or leaving full screen. */
  export function measure(): void {
    if (stage) stageSize = { width: stage.clientWidth, height: stage.clientHeight };
  }

  /** Zoom to `z` (against 1:1), keeping the point under the pointer, or the centre, in place. */
  async function setZoom(z: number, at?: { clientX: number; clientY: number }): Promise<void> {
    if (!stage) return;
    const next = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, z));
    const rect = stage.getBoundingClientRect();
    const px = at ? at.clientX - rect.left : stage.clientWidth / 2;
    const py = at ? at.clientY - rect.top : stage.clientHeight / 2;
    const fx = (stage.scrollLeft + px) / Math.max(1, stage.scrollWidth);
    const fy = (stage.scrollTop + py) / Math.max(1, stage.scrollHeight);
    zoom = next;
    await tick();
    stage.scrollLeft = fx * stage.scrollWidth - px;
    stage.scrollTop = fy * stage.scrollHeight - py;
  }

  export function zoomIn(): void {
    void setZoom((scale ?? 1) * STEP);
  }

  export function zoomOut(): void {
    void setZoom((scale ?? 1) / STEP);
  }

  export function fit(): void {
    zoom = null;
  }

  export function actualSize(): void {
    void setZoom(1);
  }

  function onDblClick(e: MouseEvent): void {
    if (zoom == null) void setZoom(1, e);
    else zoom = null;
  }

  function onLoad(e: Event): void {
    const img = e.currentTarget as HTMLImageElement;
    if (size === 'view' && img.naturalWidth > 0) natural = { width: img.naturalWidth, height: img.naturalHeight };
    loadedSrc = img.getAttribute('src');
    measure();
  }

  function onPointerDown(e: PointerEvent): void {
    if (!pannable || !stage || e.button !== 0) return;
    e.preventDefault();
    stage.setPointerCapture?.(e.pointerId);
    drag = { x: e.clientX, y: e.clientY, left: stage.scrollLeft, top: stage.scrollTop };
  }

  function onPointerMove(e: PointerEvent): void {
    if (!drag || !stage) return;
    stage.scrollLeft = drag.left - (e.clientX - drag.x);
    stage.scrollTop = drag.top - (e.clientY - drag.y);
  }

  function onPointerUp(e: PointerEvent): void {
    if (!drag) return;
    stage?.releasePointerCapture?.(e.pointerId);
    drag = null;
  }
</script>

<svelte:window onresize={measure} />
<svelte:document onfullscreenchange={() => tick().then(measure)} />

<div class="image-body">
  {#if refused}
    <div class="img-msg" data-testid="viewer-too-large">{tooLargeText(refused)}</div>
  {:else if errored && infoDone}
    <div class="img-msg" data-testid="viewer-no-preview">No preview: download to view.</div>
  {:else}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      class="img-stage"
      class:pan={pannable}
      class:drag={drag !== null}
      bind:this={stage}
      onpointerdown={onPointerDown}
      onpointermove={onPointerMove}
      onpointerup={onPointerUp}
      onpointercancel={onPointerUp}
    >
      <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
      <img
        {src}
        alt={name}
        draggable="false"
        class:sized={shown !== null}
        style={shown ? `width: ${shown.width}px; height: ${shown.height}px;` : ''}
        onload={onLoad}
        onerror={() => { errored = true; }}
        ondblclick={onDblClick}
        data-testid="viewer-image"
      />
    </div>
    {#if loadedSrc !== src && !errored}
      <div class="img-msg overlay-msg" data-testid="viewer-making-preview">making preview…</div>
    {/if}
    {#if note}
      <div class="img-note" data-testid="viewer-image-note">{note}</div>
    {/if}
  {/if}
</div>

<style>
  .image-body {
    position: relative;
    height: 100%;
    width: 100%;
  }
  .img-stage {
    height: 100%;
    overflow: auto;
    display: flex;
    padding: 20px 56px;
  }
  .img-stage.pan { cursor: grab; }
  .img-stage.pan.drag { cursor: grabbing; }
  .img-stage img {
    margin: auto;
    flex: none;
    max-width: 100%;
    max-height: 100%;
    background: #fcfcfb;
    padding: 8px;
    border-radius: 4px;
    box-sizing: content-box;
    user-select: none;
  }
  .img-stage img.sized { max-width: none; max-height: none; }
  .img-msg {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
    color: var(--text-secondary);
    padding: 16px;
    text-align: center;
  }
  .overlay-msg {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }
  /* The preview differs from its file: one amber line (the download-only ring's tint). */
  .img-note {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 8px;
    text-align: center;
    pointer-events: none;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 10.5px;
    color: #E9A847;
  }
</style>
