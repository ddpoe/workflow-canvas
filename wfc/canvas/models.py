"""The Canvas API's request and response models.

The pipeline document the canvas posts to the validate, run and cache-status
routes, and the shapes of the module-colour, draft, draft-run and draft-step
routes, so the OpenAPI snapshot (and the generated ``types/api.ts``) names
every field the frontend reads.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Pydantic models
# =============================================================================


def _node_schema_extra(schema: dict[str, Any]) -> None:
    """Document a draft node's ``folder`` without making it a dumped field.

    A declared field would be dumped as ``folder: null`` onto every node, and
    the document's key check refuses a key it does not read. Kept as an
    extra field, ``folder`` reaches the document only when the canvas sends
    it.
    """
    schema.setdefault("properties", {})["folder"] = {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "title": "Folder",
        "description": ("A draft node's method folder (with version \"draft\"), "
                        "as the draft's pointer names it."),
    }


class PipelineNode(BaseModel):
    # A field the model does not declare is kept, so the validator refuses it
    # by name instead of the route dropping it silently.
    model_config = ConfigDict(extra="allow", json_schema_extra=_node_schema_extra)

    id: str
    # A method node's pinned version identity, or "draft" for a node that
    # runs its method's draft (with ``folder``).
    version: str | None = Field(
        default=None,
        description=("A method node's pinned version identity, or \"draft\" for "
                     "a node that runs its method's draft folder."))
    type: str | None = "method"  # "method" | "input_selector" | "run_reference"
    method: str = ""
    module: str | None = None
    params: dict[str, Any] = {}
    position: dict[str, float] | None = None
    label: str | None = None  # custom NID from canvas node
    samples: list[str] | None = None  # input_selector: selected samples
    run_id: str | None = None  # run_reference: selected run ID
    source: str | None = None  # input_selector: source type
    fan_mode: str | None = None  # input_selector: "out" (default, per-sample) | "in" (bundle)


class PipelineLink(BaseModel):
    source: str
    target: str
    sourceHandle: str | None = None
    targetHandle: str | None = None


class PipelineInput(BaseModel):
    # Undeclared top-level fields are kept for the validator, as on nodes.
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    nodes: list[PipelineNode]
    links: list[PipelineLink] = []
    samples: list[str] = []
    # Parameter-sweep fields carried through to the emitter (wfc/orchestration/snakemake.py).
    # The canvas compiles its richer authoring state (sample_overrides, etc.)
    # into these two fields before POSTing — see frontend pipeline.ts.
    # Its shape is not checked here: the validator refuses a malformed one in
    # the words check-pipeline and run-pipeline use (never a 422 of its own).
    param_sets: Any = None
    explicit_combos: list[dict[str, Any]] | None = None
    # When true, pass --keep-going to Snakemake so a failure in one job
    # doesn't cancel independent jobs. Useful for fan-out pipelines where
    # one bad sample shouldn't block the rest. Default off (fail-fast).
    keep_going: bool = False
    # Pipeline variables shelf. Maps variable name -> {type, value}
    # (or bare value for back-compat). Substituted server-side at /run via
    # Graph's resolve_variables before _enrich_pipeline. The pre-
    # substitution form is also persisted as pipeline.editable.json so
    # History "Open in canvas" can rehydrate variables and bind chips.
    variables: dict[str, Any] | None = None


# =============================================================================
# Run and validate refusals of a draft node
# =============================================================================


class DraftRefusal(BaseModel):
    """Why one draft node cannot run or validate.

    ``draft_invalid``: the draft cannot be read for a run, its Registration
    check fails (``reasons`` lists each failed line), it selects no script,
    or its env cannot run. ``draft_in_use``: the draft's draft run is still
    running in ``pipelineId``. ``draft_mismatch`` (Run only): the document
    does not match the draft's contract read now -- a param or variant, a
    link slot, or a required input nothing is connected to; the message
    names the node and the field. A changed ``method.yaml`` is never a
    refusal: Run and Validate read it again.
    """

    kind: Literal["draft_invalid", "draft_in_use", "draft_mismatch"]
    #: The pipeline node that runs the draft.
    nodeId: str
    #: The draft as ``<module>.<method>``.
    draft: str
    #: The sentence shown, e.g. "qc_report: env wfc-qc-env is not registered."
    message: str
    #: Each failed Registration check line (``draft_invalid``); empty otherwise.
    reasons: list[str] = []
    #: The pipeline whose draft run is still running (``draft_in_use``).
    pipelineId: str | None = None
    #: That pipeline's name, else None.
    pipelineName: str | None = None


class ValidateResponse(BaseModel):
    """The validate route's verdict on a pipeline document."""

    model_config = ConfigDict(extra="allow")

    valid: bool
    errors: list[str] = []
    warnings: list[str] = []
    #: Each draft node's refusal; its message is also one of ``errors``.
    draftRefusals: list[DraftRefusal] = []


class RunRefusal(BaseModel):
    """The run route's 409 detail when a draft node refuses the run.

    The same kind-tagged ``{kind, message, hint}`` shape as the not-ready
    refusals, with one entry per refusing draft node.
    """

    kind: Literal["draft_refused"]
    message: str
    hint: str | None = None
    refusals: list[DraftRefusal] = []


class RunRefusalBody(BaseModel):
    """The run route's 409 body for a draft refusal."""

    detail: RunRefusal


# =============================================================================
# Module colours
# =============================================================================


class RegistryContract(BaseModel):
    """One module-level contract as the Registry lists it."""

    type: str
    name: str
    value_type: str | None = None
    required: bool = True


class RegistryModule(BaseModel):
    """One module as the Registry's Modules tab lists it."""

    name: str
    description: str = ""
    contracts: list[RegistryContract] = []
    #: The number of methods the module holds.
    methods: int = 0
    #: The project-relative ``module.yaml``.
    source: str
    #: The module's colour as ``#rrggbb``, the one the Builder, Registry and
    #: History all show; None while the module has none stored.
    color: str | None = None


class RegistryModulesResponse(BaseModel):
    """``GET /api/registry/modules``."""

    modules: list[RegistryModule]


class ColorCheckRequest(BaseModel):
    """A colour the picker asks about."""

    #: A hex colour, ``#rrggbb``.
    color: str


class ColorCheckResult(BaseModel):
    """Whether a colour can be a module colour, and the nearest one that can.

    ``failedTest`` names the first of the colour standard's six tests the
    colour fails: ``distance`` (too close to a protected colour),
    ``hue_guard``, ``chroma`` (too grey), ``white_text``, ``background``
    (too dark on the canvas backgrounds) or ``module_ink``. ``reserved`` is
    the meaning of the protected colour it is too close to, in plain words
    ("completed", "running", "failed", "cancelled", "cached results",
    "buttons and links", "system nodes"); None for a contrast failure.
    """

    ok: bool
    #: The colour checked, normalised to ``#rrggbb``.
    color: str
    failedTest: Literal["distance", "hue_guard", "chroma", "white_text",
                        "background", "module_ink"] | None = None
    reserved: str | None = None
    #: The refusal in plain words, e.g. "Too close to a colour Workflow
    #: Canvas uses to show status."; None when the colour passes.
    message: str | None = None
    #: The nearest passing shade as ``#rrggbb``; None when the colour passes.
    suggested: str | None = None
    #: The CSS Level 4 colour name nearest the suggested shade, in spaced
    #: words, e.g. "pale violet red".
    suggestedName: str | None = None


class ColorRefusalBody(BaseModel):
    """The 400 body of a refused colour change: the check's result."""

    detail: ColorCheckResult


class ModuleColorPatch(BaseModel):
    """``PATCH /api/registry/modules/{name}``: the module's new colour.

    ``color`` null resets the module to its default colour.
    """

    color: str | None


# =============================================================================
# The Builder's module palette
# =============================================================================


class PaletteVersion(BaseModel):
    """One registered version of a method, with its contract."""

    label: int
    identity: str
    registered_at: str
    env: str | None = None
    newest: bool
    inputs: dict[str, Any] | None = {}
    outputs: dict[str, Any] | None = {}
    params_schema: dict[str, Any] | None = {}
    executor: str = "python"


class PaletteMethod(BaseModel):
    """One registered method; its contract fields and env are its newest version's."""

    inputs: dict[str, Any] | None = {}
    outputs: dict[str, Any] | None = {}
    params_schema: dict[str, Any] | None = {}
    executor: str = "python"
    description: str = ""
    env: str | None = None
    versions: list[PaletteVersion] = []


class PaletteDraft(BaseModel):
    """One draft in the palette's Drafts group, with the contract its folder declares.

    A ``version`` draft is the next version of the registered method of the
    same name in its module (the node's version menu shows it as ● DRAFT); a
    ``method`` draft is a new method of the module.
    """

    kind: Literal["version", "method"]
    #: The draft's method folder, as its pointer names it.
    folder: str
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    params_schema: dict[str, Any] = {}
    executor: str = "python"
    description: str = ""
    env: str | None = None
    #: False when the draft's Registration check fails (⚠ INVALID).
    valid: bool = True
    #: Each failed check line, shown on hover.
    reasons: list[str] = []


class PaletteModule(BaseModel):
    """One module of ``GET /api/modules``, keyed by its name."""

    description: str = ""
    methods: dict[str, PaletteMethod] = {}
    #: The module's colour as ``#rrggbb``; None while it has none stored.
    color: str | None = None
    #: The module's drafts by method name (the palette's Drafts group).
    drafts: dict[str, PaletteDraft] = {}


# =============================================================================
# Drafts (the Registry's Drafts group)
# =============================================================================


class DraftCheckLine(BaseModel):
    """One line of a draft's Registration check (✓ or ✗)."""

    ok: bool
    text: str


class DraftCheck(BaseModel):
    """A draft's Registration check: every line, ✓ or ✗. Nothing is written."""

    ok: bool
    lines: list[DraftCheckLine] = []


class DraftContract(BaseModel):
    """The contract a draft folder's ``method.yaml`` declares."""

    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    params_schema: dict[str, Any] = {}
    executor: str = "python"


class DraftRunSummary(BaseModel):
    """The draft's latest draft run, in brief."""

    #: When it started, ISO 8601 UTC.
    startedAt: str
    pipelineId: str
    #: The pipeline's name, else None (shown as its id).
    pipelineName: str | None = None
    #: ``running``, ``succeeded``, ``failed`` or ``cancelled``.
    status: str
    #: True while its steps are still running.
    running: bool = False


class DraftEntry(BaseModel):
    """One draft as the Registry's Drafts group lists it."""

    module: str
    method: str
    kind: Literal["version", "method"]
    #: The method folder, as its pointer names it.
    folder: str
    env: str | None = None
    #: The registered method's newest version label; None for a draft method.
    newestLabel: int | None = None
    #: The version registering the draft adds (newest + 1, or 1).
    registersAs: int
    #: Whether any file of the folder changed since the newest version
    #: ("Files updated since v3").
    filesUpdated: bool = False
    #: Whether ``method.yaml`` changed since the contract shown was stored on
    #: the pointer (Update, or the next run, reads it again; nothing refuses).
    stale: bool = False
    #: False when the Registration check fails (⚠ INVALID).
    valid: bool = True
    #: Each failed check line, shown on hover.
    reasons: list[str] = []
    contract: DraftContract = DraftContract()
    check: DraftCheck
    draftRun: DraftRunSummary | None = None


class DraftsResponse(BaseModel):
    """``GET /api/registry/drafts``."""

    drafts: list[DraftEntry]


class DraftAddRequest(BaseModel):
    """``+ Draft from folder…``: the module first, then the method folder."""

    module: str
    #: The method folder, absolute or relative to the project root.
    folder: str


class DraftAddResponse(BaseModel):
    """What adding the folder as a draft does (the dry run) or did.

    ``message`` is the dialog's sentence, e.g. "Module __demo__ already has a
    method called __demo__label (newest version v3). This folder becomes its
    DRAFT version; registering it adds v4."
    """

    ok: bool
    module: str
    method: str
    kind: Literal["version", "method"]
    message: str
    newestLabel: int | None = None
    registersAs: int
    #: The added draft; None on a dry run.
    draft: DraftEntry | None = None


class DraftUpdateResponse(BaseModel):
    """Update: the draft's ``method.yaml`` read again (nothing registered)."""

    draft: DraftEntry
    #: Whether the contract differs from the one read before.
    changed: bool
    message: str


class DraftRef(BaseModel):
    """A draft named by its module and method."""

    module: str
    method: str


class SnapshotCheckRequest(BaseModel):
    """The drafts whose folders to check (on drag, and before Run or Validate)."""

    drafts: list[DraftRef]


class SnapshotCheckEntry(BaseModel):
    """One draft folder against its pointer's stat snapshot.

    ``current``: the draft can be read and its Registration check passes.
    ``invalid``: the check fails. ``missing``: the draft or its folder is
    gone. A changed ``method.yaml`` is information, not a state: Run and
    Validate read it again.
    """

    module: str
    method: str
    state: Literal["current", "invalid", "missing"]
    message: str | None = None
    #: Whether ``method.yaml`` changed since the pointer stored its contract:
    #: the palette now shows the contract read now, for the node to take.
    contractChanged: bool = False
    #: Whether any other file changed (feeds "Files updated since vN" only).
    filesUpdated: bool = False


class SnapshotCheckResponse(BaseModel):
    """``POST /api/registry/drafts/snapshot-check``."""

    drafts: list[SnapshotCheckEntry]


class DraftDeleteLaterDraft(BaseModel):
    """A later draft whose results computed from the deleted one are cleared."""

    #: The draft as ``<module>.<method>``.
    draft: str
    #: Its draft run, as messages name one.
    run: str


class DraftDeletePipeline(BaseModel):
    """A pipeline whose cleared line a delete changes."""

    pipelineId: str
    #: Its name, else its id.
    name: str


class DraftDeletePlan(BaseModel):
    """What Remove draft or Delete outputs deletes, planned before anything is."""

    module: str
    method: str
    #: True for Delete outputs (the draft is kept).
    outputsOnly: bool
    #: The draft folder, project-relative.
    folder: str
    #: The method folder the draft points at; never touched.
    source: str
    #: The draft's own draft run, or None when it has none.
    draftRun: str | None = None
    laterDrafts: list[DraftDeleteLaterDraft] = []
    pipelines: list[DraftDeletePipeline] = []
    #: What the delete removes, one line each (Execution's wording).
    deletes: list[str] = []
    #: What the delete leaves, one line each (Execution's wording).
    keeps: list[str] = []
    #: The line pointing at the other delete; empty when nothing is deleted.
    hint: str = ""
    #: The plan as ``wfc delete-draft`` prints it.
    message: str
    #: Send back as ``planned`` to delete; a fresh plan that differs is a 409.
    token: str


class DraftActionResponse(BaseModel):
    """Remove draft and Delete outputs: the plan (dry run) or what was done."""

    ok: bool
    message: str
    #: The dry run's plan; None once deleted.
    plan: DraftDeletePlan | None = None


class MethodFile(BaseModel):
    """One file at the top of a method folder, with its content."""

    name: str
    language: str
    content: str


class DraftDetail(BaseModel):
    """A draft's files and contract for its expanded Registry row."""

    draft: DraftEntry
    #: The folder's top-level files, the main script first.
    files: list[MethodFile] = []
    contract: DraftContract = DraftContract()
    env: str | None = None


# =============================================================================
# Draft runs and draft steps
# =============================================================================

#: A draft step's status, in the run rows' words.
DraftStepStatus = Literal["pending", "running", "success", "failed", "cancelled"]


class DraftStepOutput(BaseModel):
    """One output a draft step wrote."""

    slot: str
    #: The file's name inside the step's folder.
    filename: str | None = None
    sizeBytes: int | None = None


class DraftStepInput(BaseModel):
    """What filled one input of a draft step.

    ``source`` is ``sample`` (``sample`` set), ``run`` (a recorded run,
    ``runId`` and ``sourceSlot`` set) or ``draft`` (an upstream draft step,
    ``draftString`` and ``sourceSlot`` set).
    """

    slot: str
    source: Literal["sample", "run", "draft"]
    sample: str | None = None
    runId: str | None = None
    draftString: str | None = None
    sourceSlot: str | None = None


class DraftStep(BaseModel):
    """One draft-mode step of the latest draft run of a draft.

    The shape History's Pipelines rows, Lineages and Descendants draw. A step
    is the draft's own (``isDraft``) or downstream of it; ``draft`` names the
    draft that owns it (the nearest one upstream).
    """

    #: ``draft:<module>.<method>/<node>@<sample>``, every part written.
    draftString: str
    module: str
    method: str
    nodeId: str
    sample: str
    #: The owning draft as ``<module>.<method>``.
    draft: str
    isDraft: bool
    status: DraftStepStatus
    #: Unix epoch milliseconds the step started; None before it started.
    timestamp: int | None = None
    #: Seconds; None until it ends.
    duration: float | None = None
    pipelineId: str
    pipelineName: str | None = None
    #: The node's NID in the pipeline document.
    nid: str | None = None
    #: The method version label the step ran: None for the draft's own step.
    version: int | None = None
    sizeBytes: int | None = None
    #: Recorded runs upstream of the step, by run id.
    parentRunIds: list[str] = []
    #: Draft steps upstream of the step, by draft string.
    parentDraftSteps: list[str] = []
    error_message: str | None = None


class DraftStepsResponse(BaseModel):
    """``GET /api/wfc/draft-steps``: every draft step that still exists."""

    steps: list[DraftStep]


class DraftStepArtifact(BaseModel):
    """One file in a draft step's folder."""

    name: str
    size: int
    #: The viewer the file opens in; ``None`` for download only.
    viewer: Literal["image", "table", "json", "text", "pdf"] | None = None
    extension: str = ""


class RunArtifactChild(BaseModel):
    """One direct child of a folder output: a file, or a subdirectory ``name/``."""

    name: str
    size: int
    #: The extension, lower-case without the dot; ``""`` for a subdirectory.
    extension: str = ""
    #: The viewer the file opens in; ``None`` for download only and for a
    #: subdirectory.
    viewer: Literal["image", "table", "json", "text", "pdf"] | None = None


class RunArtifact(BaseModel):
    """``GET /api/wfc/run/{id}/artifacts``: one archived output of a run."""

    #: The displayed name; a folder output ends in ``/``.
    name: str
    type: Literal["file", "dir"]
    size: int
    #: A folder output's descendant-file count.
    count: int | None = None
    #: The viewer a file output opens in; ``None`` for download only and for
    #: a folder.
    viewer: Literal["image", "table", "json", "text", "pdf"] | None = None
    #: A file output's extension, lower-case without the dot; ``""`` for a
    #: folder.
    extension: str = ""
    #: A folder output's direct children (one level deep).
    children: list[RunArtifactChild] | None = None


#: A table's delimiter, by name: ``,``, tab, ``;``, ``|`` or a space.
TableDelimiter = Literal["comma", "tab", "semicolon", "pipe", "space"]


class TablePreview(BaseModel):
    """The rows a table preview shows, and how they were split."""

    #: The header row when ``header`` is on; ``None`` otherwise.
    columns: list[str] | None = None
    #: The first rows after the header, at most 100.
    rows: list[list[str]]
    delimiter: TableDelimiter
    #: Whether the delimiter was guessed from the file (``False`` when it was
    #: asked for, or when guessing failed and the default was used).
    delimiter_guessed: bool
    #: Whether the first row is shown as the header.
    header: bool
    #: Whether ``header`` was guessed from the file rather than asked for.
    header_guessed: bool


class ArtifactPreview(BaseModel):
    """``GET /api/wfc/run/{id}/preview/{path}``: the start of a table, JSON or text file."""

    #: How the file is shown: ``json`` only when it parsed; JSON too large or
    #: not valid comes back as ``text``.
    kind: Literal["table", "json", "text"]
    #: The bytes of the file the preview covers: at most what it read.
    shown_bytes: int
    #: The file's size in bytes.
    total_bytes: int
    #: Whether the file holds more than the preview shows.
    truncated: bool
    #: Why the preview is cut or differs from the file; ``None`` when it does not.
    reason: str | None = None
    #: The text shown, for ``json`` (the whole file, valid JSON) and ``text``.
    text: str | None = None
    #: The rows shown, for ``table``.
    table: TablePreview | None = None


#: An image preview's size, by its longer side: ``thumb`` ~260 px, ``view``
#: ~2048 px, ``large`` ~4096 px (an image no larger is shown at 1:1).
ImageSize = Literal["thumb", "view", "large"]


class ImageInfo(BaseModel):
    """``GET /api/wfc/run/{id}/image-info/{path}``: what an image preview shows of its file.

    Read from the file's header, never decoding it, for the size asked: the
    facts the viewer's note states (smaller than the file, bit depth reduced,
    page 1 of N) and whether the image route would refuse it.
    """

    #: The file's width and height in pixels.
    width: int
    height: int
    #: The preview's width and height in pixels: the file's own when it fits
    #: the size asked, never larger.
    preview_width: int
    preview_height: int
    #: Bits per sample in the file (1, 8, 16, 32).
    bit_depth: int
    #: Whether the preview holds fewer bits per sample than the file (it is 8-bit).
    bit_depth_reduced: bool
    #: Frames or pages in the file; the preview shows the first.
    pages: int
    #: The bytes decoding the file takes.
    decoded_bytes: int
    #: The bytes converting it to this preview takes at its peak: the decoded
    #: file plus the working images of the resize, the 8-bit conversion and the
    #: PNG encode. A conversion may take at most half of ``[previews] memory_mb``.
    memory_bytes: int
    #: The smallest ``[previews] memory_mb`` that lets the image route convert
    #: it at this size: ``memory_bytes`` twice over, in whole MB.
    memory_mb_needed: int
    #: Why the image route refuses this image (it needs more than half of
    #: ``memory_mb``), with the setting that would admit it; ``None`` when it
    #: converts it.
    refused: str | None = None


class ImageTooLarge(BaseModel):
    """The image route's 422: an image whose conversion needs more than half of ``[previews] memory_mb``."""

    #: What to do: raise ``[previews] memory_mb`` to ``memory_mb_needed``, or download the file.
    detail: str
    #: The file's width and height in pixels, read from its header.
    width: int
    height: int
    #: The smallest ``[previews] memory_mb`` that lets the image route convert it at this size.
    memory_mb_needed: int


class DraftStepDetail(BaseModel):
    """History's run panel for a draft step."""

    step: DraftStep
    #: Always ``DRAFT`` for the draft's own step; the version label otherwise.
    versionText: str
    #: The owning draft's method folder.
    folder: str
    #: "__demo__label's draft runs again".
    keptUntil: str
    params: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    inputs: list[DraftStepInput] = []
    outputs: list[DraftStepOutput] = []
    artifacts: list[DraftStepArtifact] = []
    #: The step's stdout and stderr, as its log holds them.
    log: str | None = None


class DraftRunNodeStep(BaseModel):
    """One sample of one node in a draft run."""

    sample: str
    draftString: str
    status: DraftStepStatus
    sizeBytes: int | None = None


class DraftRunNode(BaseModel):
    """One node line of the Registry's Draft run tab."""

    nodeId: str
    module: str
    method: str
    isDraft: bool
    #: The node's status over its samples.
    status: DraftStepStatus
    sizeBytes: int | None = None
    steps: list[DraftRunNodeStep] = []


class DraftRunDetail(BaseModel):
    """The latest draft run of a draft (the Registry's Draft run tab)."""

    module: str
    method: str
    #: When it started, ISO 8601 UTC.
    startedAt: str
    pipelineId: str
    pipelineName: str | None = None
    samples: list[str] = []
    #: ``running``, ``succeeded``, ``failed`` or ``cancelled``.
    status: str
    #: Steps by status (``success``, ``failed`` …).
    tally: dict[str, int] = {}
    totalBytes: int = 0
    #: "__demo__label's draft runs again in demo".
    keptUntil: str
    nodes: list[DraftRunNode] = []


# =============================================================================
# Pipelines (History's Pipelines view)
# =============================================================================


class ClearedDraft(BaseModel):
    """Why one draft's steps of a pipeline run were cleared."""

    #: The draft as ``<module>.<method>``.
    draft: str
    reason: Literal["replaced", "outputs_deleted", "registered", "removed"]
    #: When, ISO 8601 UTC.
    at: str
    #: ``replaced``: the pipeline whose draft run replaced it.
    replacedByPipelineId: str | None = None
    #: ``registered``: the version label it was registered as.
    registeredAs: int | None = None


class PipelineCleared(BaseModel):
    """The one greyed line of a pipeline run whose draft steps are gone."""

    count: int
    #: "9 draft steps cleared: replaced by the draft run of 14:31."
    text: str
    drafts: list[ClearedDraft] = []
