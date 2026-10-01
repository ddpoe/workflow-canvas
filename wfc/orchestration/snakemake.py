"""The Snakefile emitter (wildcard-based).

Generates ONE rule per method using wildcards for samples and parameter variants.
No rule explosion — the number of rules equals the number of pipeline steps,
regardless of how many samples or parameter variants exist.

Supports:
  - Cartesian product (all variant combinations)
  - Selective combos (explicit list of variant combinations)
  - Asymmetric fan-out (different steps with different numbers of variants)

Design choices:
  - Everything hardcoded — no Snakemake config files needed
  - Each Snakefile is a throwaway build artifact; the DB is the record of truth
  - Each method dir is mapped to a single Snakemake rule (with wildcards)
  - wfc CLI handles bookkeeping; Snakemake handles execution + DAG resolution

The emitter reads nothing: the pipeline and the samples' content hashes are
values in (Execution's composer read them), and the text is the value out.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from axiom_annotations import AutoStep, Step, task, workflow

from .. import layout
from ..contracts import COLLAPSED_SAMPLE
from ..graph import (
    PipelineDef,
    StepDef,
    carries_sample_bundle,
    find_leaf_nodes,
    reads_per_sample,
)
from ..graph.expansion import resolve_variant_model  # from its defining module, so the AutoStep edge resolves

# =============================================================================
# Internal helpers
# =============================================================================

def _python_repr(obj) -> str:
    """Convert a Python object to a repr string with proper Python booleans."""
    if isinstance(obj, dict):
        items = ", ".join(f"{_python_repr(k)}: {_python_repr(v)}" for k, v in obj.items())
        return "{" + items + "}"
    elif isinstance(obj, list):
        items = ", ".join(_python_repr(v) for v in obj)
        return "[" + items + "]"
    elif isinstance(obj, bool):
        return "True" if obj else "False"
    elif isinstance(obj, str):
        return json.dumps(obj)
    else:
        return repr(obj)



def _output_path(node_id: str, step_map: dict[str, StepDef], pipeline_id: str | None = None) -> str:
    """Build the Snakemake-visible output path -- a zero-byte sentinel.

    Unified path scheme -- every node, regardless of slot count or slot type::

        .runs/sentinels/{pipeline_id}/{node_id}/{sample}/{variant}/.complete

    The sentinel is touched by ``wfc run-step``'s record phase when the step
    completes or hits the cache, signalling to Snakemake that the rule completed.  Actual method outputs
    live in ``.runs/<run_id>/<slot>/`` (staging) and are content-addressed
    via the DVC cache -- Snakemake never sees them.  Slot-agnostic: every
    slot of a node collapses to one sentinel under the unified contract.

    Args:
        node_id: Node identity within the pipeline.
        step_map: Lookup dict of all steps.
        pipeline_id: Pipeline execution ID.

    Returns:
        Sentinel path with ``{sample}`` and ``{variant}`` wildcards.
        Collapsed (fan-in) steps bake ``__all__`` into the sample segment.
    """
    step = step_map[node_id]
    # Collapsed steps (fan-in) bake COLLAPSED_SAMPLE into the sample
    # segment -- they run once per variant, not once per (sample, variant).
    sample_segment = COLLAPSED_SAMPLE if step.sample_collapsed else "{sample}"
    return layout.run_sentinel_relpath(pipeline_id, node_id, sample_segment, "{variant}")


def _input_path(
    node_id: str,
    step_map: dict[str, StepDef],
    pipeline_id: str | None = None,
) -> str | dict[str, str | list[str]] | None:
    """Build the input path (= upstream step's output path).

    Returns None for a step with no upstream method dependency.
    For multi-input (fan-in) steps, returns a dict mapping slot names
    to lists of upstream output paths.
    """
    step = step_map[node_id]
    if carries_sample_bundle(step):
        # Collapsed root step: its "upstream" is a fan-in input_selector.
        # Emit a list of per-sample restore sentinels bound to
        # the consumer's first input slot so Snakemake expands the bundle
        # as a list of input files on a single rule invocation.
        # Same predicate as the shell's --collapsed-sample flags below: a
        # rule waits on the bundle's sentinels exactly when it is handed the
        # bundle's samples.
        slot_name = next(iter(step.inputs), "data") if step.inputs else "data"
        sentinels = [
            layout.sample_ready_sentinel_relpath(s) for s in step.collapsed_samples
        ]
        return {slot_name: sentinels}
    if not step.depends_on:
        return None

    # Multi-input: fan-in (>1 dep), multi-slot (>1 input slot), or named source_slots
    if (
        len(step.depends_on) > 1
        or any(len(v) > 1 for v in step.inputs.values())
        or len(step.inputs) > 1
    ):
        result: dict[str, str | list[str]] = {}
        for slot, upstream_ids in step.inputs.items():
            result[slot] = [
                _output_path(uid, step_map, pipeline_id=pipeline_id)
                for uid in upstream_ids
            ]
        return result

    # Single input: sentinels are slot-agnostic; no need to
    # consult source_slot here.
    upstream_id = step.depends_on[0]
    return _output_path(upstream_id, step_map, pipeline_id=pipeline_id)


# =============================================================================
# Rule body generator
# =============================================================================

#: Prefix that keeps reference ``input:`` keys in their own namespace, clear
#: of the fan-in branch's ``{slot}_{i}`` and the single-upstream branch's
#: ``data_0``. Without it, a method taking both a wired upstream and a
#: reference on the same slot emits a repeated keyword — a Snakefile that
#: does not parse, not a silent overwrite.
REF_INPUT_KEY_PREFIX = "ref"


def _ref_input_declarations(ref_pairs: list[tuple[str, str]]) -> list[str]:
    """Build the ``input:`` block lines for a step's reference artifacts.

    The keys in a generated rule's ``input:`` block are pure DAG
    declaration — nothing in the emitted shell dereferences them — so they
    are free to differ from the slot label. They are *not* free to repeat:
    a Snakemake ``input:`` block is Python keyword syntax and a duplicated
    key is a syntax error. Each reference therefore gets its own indexed,
    prefixed key while the shell keeps repeating the real slot name.

    Args:
        ref_pairs: ``(label, path)`` per reference, in document order.

    Returns:
        One indented ``key="path"`` line per reference.
    """
    return [
        f'        {REF_INPUT_KEY_PREFIX}{i}_{label}='
        f'"{ref_path.replace(chr(92), "/")}"'
        for i, (label, ref_path) in enumerate(ref_pairs)
    ]


@task(
    purpose="Generate Snakemake rule lines for a single pipeline step (shell-only)",
    inputs="StepDef, step lookup map",
    outputs="List of Snakefile lines for this rule",
)
def _generate_rule(
    step: StepDef,
    step_map: dict[str, StepDef],
    pipeline_id: str | None = None,
    sample_restore_sentinel: str | None = None,
) -> list[str]:
    """Generate a minimal Snakemake rule that delegates to ``wfc run-step``.

    All execution logic (cache check, env dispatch, run registration, error
    capture, output archiving) lives in the ``run-step`` CLI command.  The
    generated rule only handles DAG wiring (input/output/shell).

    Args:
        step: The pipeline step to generate a rule for.
        step_map: Lookup dict of all steps keyed by node_id.
        pipeline_id: Pipeline execution ID.
        sample_restore_sentinel: The restore_sample sentinel, handed to a
            step that reads a per-sample sample; declared as an input beside
            the step's upstream inputs and reference paths.

    Returns:
        List of strings (one per line) for this rule block.
    """
    nid = step.node_id
    out = _output_path(nid, step_map, pipeline_id=pipeline_id)
    inp = _input_path(nid, step_map, pipeline_id=pipeline_id)
    lines: list[str] = []

    # Reference artifacts, flattened to (label, path) in document order.
    # The label repeats when several references target one slot; the ``input:``
    # key does not (see ``_ref_input_declarations``).
    ref_pairs: list[tuple[str, str]] = [
        (label, ref_path)
        for label, ref_paths in step.run_ref_inputs.items()
        for ref_path in ref_paths
    ]

    lines.append(f"rule {nid}:")

    # -- input: declaration --
    # One composition over the three input kinds, in whatever combination
    # the step has: its upstream sentinels (a fan-in or multi-slot step's
    # keyed per slot, a single upstream's as ``data_0``), the sample
    # restore sentinel when the step reads a per-sample sample, and its
    # reference artifacts. A lone upstream or a lone sentinel keeps the
    # unnamed form; anything else is named, so every part has its own key.
    named: list[str] = []
    scalars: list[str] = []
    if isinstance(inp, dict):
        for slot, paths in inp.items():
            for i, p in enumerate(paths):
                named.append(f"        {slot}_{i}=\"{p}\"")
    elif inp is not None:
        named.append(f"        data_0=\"{inp}\"")
        scalars.append(inp)
    if sample_restore_sentinel is not None:
        named.append(f"        sample_ready=\"{sample_restore_sentinel}\"")
        scalars.append(sample_restore_sentinel)
    # Forward slashes, so a backslash never becomes a Python escape.
    named.extend(_ref_input_declarations(ref_pairs))
    if len(named) == 1 and len(scalars) == 1:
        lines.append(f"    input: \"{scalars[0]}\"")
    elif named:
        lines.append("    input:")
        lines.append(",\n".join(named))

    # -- output: declaration --
    # Snakemake-visible output is a single zero-byte sentinel per
    # (pipeline, node, sample, variant). Real method outputs stay in
    # .runs/<run_id>/<slot>/ (staging) and are content-addressed via the
    # DVC cache. `wfc run-step` touches the sentinel when the step completes
    # or hits the cache. The sentinel is uniform, so the Snakefile needs no
    # directory-slot or multi-slot detection.
    lines.append(f"    output: \"{out}\"")

    # -- params: pass variant and node_id through Snakemake params --
    lines.append('    params:')
    lines.append('        variant="{variant}",')
    lines.append(f'        node_id="{nid}"')

    # -- shell: delegate everything to wfc run-step --
    # Use env vars WFC_PIPELINE_JSON and WFC_PIPELINE_ID set in preamble.
    # No {{CONSTANT}} brace patterns in shell strings.
    # Boundary rule: the orchestrator resolves run_reference paths
    # and passes them explicitly via --ref-input so run_step stays
    # topology-agnostic.
    # The label repeats verbatim once per occurrence: the materialize phase
    # parses --ref-input into a duplicate-preserving list of (label, path)
    # pairs and accumulates same-label entries into one slot, so N references
    # wired into one slot arrive as an N-element list under that slot name.
    ref_input_args = ""
    for label, ref_path in ref_pairs:
        ref_input_args += f' --ref-input {label}={ref_path.replace(chr(92), "/")}'
    # Collapsed fan-in root: the Snakemake .sample_ready sentinels in the
    # input: block only gate dependency ordering -- they aren't data paths.
    # Emit one --collapsed-sample <s> per bundled sample. The materialize
    # phase (wfc.execution.materialize) iterates each named sample's
    # data/samples/<s>/ directory at execution time (after restore_sample
    # has populated it) and accumulates the per-sample data files into the
    # fan-in slot. Filesystem inspection deliberately stays out of the
    # generator -- restore_sample is itself a Snakemake rule whose outputs
    # don't exist at Snakefile-generation time.
    collapsed_sample_args = ""
    if carries_sample_bundle(step):
        for s in step.collapsed_samples:
            collapsed_sample_args += f' --collapsed-sample {s}'
    # Collapsed steps have no `{sample}` wildcard (it was baked to __all__).
    # Pass the literal so wfc run-step records the run at COLLAPSED_SAMPLE.
    sample_arg = COLLAPSED_SAMPLE if step.sample_collapsed else "{wildcards.sample}"
    lines.append('    shell:')
    lines.append(f'        "{{sys.executable}} -m wfc run-step '
                 f'--node-id {{params.node_id}} '
                 f'--sample {sample_arg} '
                 f'--variant {{params.variant}}'
                 f'{ref_input_args}'
                 f'{collapsed_sample_args}"')

    lines.append("")

    return lines


# =============================================================================
# Generator
# =============================================================================

@workflow(
    purpose="Generate a wildcard-based Snakefile from a pipeline definition",
    inputs="PipelineDef (steps, samples, param variants), project root path, "
           "the samples' content hashes",
    outputs="Complete Snakefile string content",
)
def generate_snakefile(
    pipeline: PipelineDef,
    project_root: str,
    pipeline_id: str | None = None,
    pipeline_json_path: str | None = None,
    *,
    sample_hashes: dict[str, str] | None = None,
) -> str:
    """Generate a wildcard-based Snakefile.

    One rule per method. Wildcards handle fan-out across samples
    and parameter variants. Snakemake resolves the DAG from file
    name patterns — no explicit wiring needed.

    Unified path scheme (sentinel-only)::

        .runs/sentinels/{pipeline_id}/{node_id}/{sample}/{variant}/.complete

    Every node uses the same sentinel pattern. Actual method outputs
    live in the run-archive directory and are content-addressed via
    the DVC cache; Snakemake never sees them. A single ``{variant}``
    wildcard dimension flows through the entire pipeline. Nodes
    without ``param_sets`` are padded so that every variant name
    maps to their default params.

    Args:
        pipeline: Pipeline definition with steps, samples, named param variants.
        project_root: Absolute path to the wfc project directory (the git repo
            containing method scripts).  Embedded as ``PROJECT_ROOT`` and
            exported to every callback as ``WFC_PROJECT_ROOT``.
        pipeline_id: Pre-generated pipeline ID (UUID string).  When provided,
            the Snakefile embeds this literal value.  When ``None``, a UUID is
            generated here.
        pipeline_json_path: Path to the frozen pipeline document, embedded as
            ``PIPELINE_JSON`` so ``run-step`` finds each node's params.  When
            ``None``, the file reads ``WFC_PIPELINE_JSON`` at load time.
        sample_hashes: Sample name to content hash for every registered
            sample the pipeline names (the composer reads them). Every
            entry carries a real hash -- ``load_sample_hashes`` refuses a
            registered row that has none. A name the pipeline uses but
            nothing registered is simply absent and gets no restore rule.
            The rule and ``SAMPLE_HASHES`` are emitted only when the table
            is non-empty and some step reads a per-sample sample or carries
            a fan-in bundle; ``None`` is an empty table. The emitter reads nothing itself.

    Returns:
        String content of the Snakefile.
    """
    import uuid as _uuid

    口 = AutoStep(step_num=1, name="Resolve variants and params")
    variant_model = resolve_variant_model(pipeline)
    resolved_params, variant_names = variant_model.tables, variant_model.axis

    # Ensure pipeline_id is always concrete — sentinel paths are scoped by it
    _pipeline_id = pipeline_id if pipeline_id is not None else str(_uuid.uuid4())

    step_map = {s.node_id: s for s in pipeline.steps}

    lines: list[str] = []
    leaf_ids = find_leaf_nodes(step_map)

    口 = Step(step_num=2, name="Emit Snakefile preamble",
             purpose="Append the header, imports, config constants (SAMPLES, "
                     "VARIANT_NAMES, paths), per-node PARAMS, the pipeline "
                     "logger, and run-step env vars")

    # ── Header ──────────────────────────────────────────────────────────────
    if pipeline.explicit_combos:
        total_runs_est = len(pipeline.explicit_combos) * len(pipeline.steps)
        mode = "selective"
    else:
        total_runs_est = len(pipeline.samples) * len(variant_names) * len(pipeline.steps)
        mode = "unified"

    lines.append('"""')
    lines.append(f"Auto-generated Snakefile (wildcard-based, {mode} mode)")
    lines.append(f"Steps: {len(pipeline.steps)} methods")
    lines.append(f"Samples: {pipeline.samples}")
    lines.append(f"Variants: {variant_names}")
    lines.append(f"Estimated total runs: {total_runs_est}")
    lines.append('"""')
    lines.append("")
    lines.append("import sys, os, logging")
    lines.append("")

    # ── Config ──────────────────────────────────────────────────────────────
    lines.append(f"SAMPLES = {repr(pipeline.samples)}")
    lines.append(f"VARIANT_NAMES = {repr(variant_names)}")
    lines.append(f'PIPELINE_ID = "{_pipeline_id}"')
    # Pipeline JSON path for run-step to find node config
    if pipeline_json_path is not None:
        lines.append(f'PIPELINE_JSON = r"{pipeline_json_path}"')
    else:
        lines.append('PIPELINE_JSON = os.environ.get("WFC_PIPELINE_JSON", "")')
    lines.append("")

    # ── Project root (resolved at generation time) ──────────────────────────
    _project_root = Path(project_root).resolve()
    lines.append(f'PROJECT_ROOT = r"{str(_project_root)}"')
    # Propagate PROJECT_ROOT to every subprocess the Snakefile spawns so that
    # wfc.persistence.project_root() can resolve it even when Snakemake's shell
    # rules inherit a foreign cwd (notably Windows UNC paths, where cmd.exe
    # silently rewrites cwd to C:\Windows and cwd-based lookup tries to
    # mkdir C:\Windows\.wfc).
    lines.append('os.environ["WFC_PROJECT_ROOT"] = PROJECT_ROOT')
    # Anchor Snakemake itself to the project root so its own relative-path
    # resolution matches the WFC_PROJECT_ROOT contract.
    lines.append('workdir: PROJECT_ROOT')
    lines.append("")

    # ── Params dict (named variants) ───────────────────────────────────────
    lines.append("PARAMS = {")
    for step in pipeline.steps:
        variants = resolved_params[step.node_id]
        lines.append(f"    {repr(step.node_id)}: {{")
        for vname, vparams in variants.items():
            lines.append(f"        {repr(vname)}: {_python_repr(vparams)},")
        lines.append("    },")
    lines.append("}")
    lines.append("")

    # ── Explicit combos (selective mode only) ──────────────────────────────
    if pipeline.explicit_combos:
        lines.append("RUNS = " + _python_repr(pipeline.explicit_combos))
        lines.append("")

    # ── Logger setup ────────────────────────────────────────────────────────
    lines.append(textwrap.dedent('''\
        # Pipeline logger — writes to pipeline.log + stderr
        PIPELINE_LOG_DIR = os.environ.get(
            "WFC_PIPELINE_LOG_DIR",
            os.path.join("__ARTIFACT_STORE__", "__PIPELINES_DIR__", PIPELINE_ID),
        )
        os.makedirs(PIPELINE_LOG_DIR, exist_ok=True)
        os.makedirs(os.path.join(PIPELINE_LOG_DIR, "__RUN_LOGS_DIR__"), exist_ok=True)

        _pipeline_logger = logging.getLogger(f"wfc.pipeline.{PIPELINE_ID}")
        _pipeline_logger.setLevel(logging.DEBUG)
        _pipeline_log_path = os.path.join(PIPELINE_LOG_DIR, "pipeline.log")
        _file_handler = logging.FileHandler(_pipeline_log_path, encoding="utf-8")
        _file_handler.setLevel(logging.DEBUG)
        _stream_handler = logging.StreamHandler(sys.stderr)
        _stream_handler.setLevel(logging.INFO)
        _log_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        _file_handler.setFormatter(_log_fmt)
        _stream_handler.setFormatter(_log_fmt)
        _pipeline_logger.addHandler(_file_handler)
        _pipeline_logger.addHandler(_stream_handler)

        # Run outcomes are tracked via sidecar JSONs (written by run-step)
    ''').replace("__ARTIFACT_STORE__", layout.ARTIFACT_STORE_NAME)
       .replace("__PIPELINES_DIR__", layout.PIPELINES_DIR_NAME)
       .replace("__RUN_LOGS_DIR__", layout.PIPELINE_RUN_LOGS_DIR_NAME))

    # ── Pipeline JSON + env vars for run-step ───────────────────────────────
    # WFC_PIPELINE_JSON and WFC_PIPELINE_ID are set as env vars so run-step
    # can find the pipeline config.  NOT as {CONSTANT} brace patterns in
    # shell strings (Snakemake interprets those as wildcards).
    lines.append(textwrap.dedent('''\
        # Set env vars for run-step (no brace patterns in shell strings)
        os.environ["WFC_PIPELINE_JSON"] = os.path.abspath(PIPELINE_JSON) if PIPELINE_JSON else ""
        os.environ["WFC_PIPELINE_ID"] = PIPELINE_ID
        # PYTHONPATH is deliberately NOT set here.  The shell rule invokes
        # the run-step verb (``{sys.executable} -m wfc``) against the host
        # venv Python, which already has wfc in its own site-packages.
        # run-step dispatches the method into its env's built container
        # image, where the method script runs under the env's own
        # interpreter with no wfc inside; only WFC_* variables are
        # forwarded into the container, never PYTHONPATH.
    '''))

    # ── Rule all (target declaration) ──────────────────────────────────────
    口 = Step(step_num=3, name="Emit rule all (targets)",
             purpose="Declare top-level targets — expand() over leaf nodes × "
                     "samples × variants (unified), or a comprehension over "
                     "explicit RUNS (selective)")
    lines.append("rule all:")
    lines.append("    input:")

    if pipeline.explicit_combos:
        # Selective mode: list comprehension over explicit RUNS
        targets = []
        for lid in leaf_ids:
            leaf_output = _output_path(lid, step_map, pipeline_id=_pipeline_id)
            fmt_path = leaf_output.replace("{sample}", "{r['sample']}").replace("{variant}", "{r['variant']}")
            targets.append(f'[f\"{fmt_path}\" for r in RUNS]')
        lines.append("        " + " + ".join(targets))
    else:
        # Unified mode: expand() over leaf nodes × samples × variants.
        # Collapsed leaves have COLLAPSED_SAMPLE already baked into their path, so
        # the sample axis has no remaining wildcard -- emit a single-element
        # list literal to keep expand() well-formed.
        targets = []
        for lid in leaf_ids:
            leaf_step = step_map[lid]
            leaf_output = _output_path(lid, step_map, pipeline_id=_pipeline_id)
            if leaf_step.sample_collapsed:
                targets.append(
                    f'expand("{leaf_output}", variant=VARIANT_NAMES)'
                )
            else:
                targets.append(
                    f'expand("{leaf_output}", sample=SAMPLES, variant=VARIANT_NAMES)'
                )
        lines.append("        " + " + ".join(targets))

    lines.append("")

    # ── restore_sample rules for steps that read a sample ────────────────
    口 = Step(step_num=4, name="Emit restore_sample rules",
             purpose="When any step reads a per-sample sample or carries a "
                     "fan-in bundle, emit the restore_sample rule over the "
                     "handed-in table, materializing each sample's file from "
                     "the DVC cache by content_hash and writing a readiness "
                     "sentinel",
             critical="The whole --hash FLAG is built in params, not just its "
                      "value: a flag with an empty value collapses to a bare "
                      "--hash in the emitted shell and the parser refuses "
                      "'expected one argument' before restore-sample can say "
                      "anything useful. Shell-independent, unlike quoting")
    # A step reads a sample when the load stamped a selector slot on it --
    # the same answer the claim and materialize classify from -- whatever
    # upstream methods or references also feed it. A per-sample reader waits
    # on its own sample's sentinel; a bundle carrier waits on every bundled
    # sample's sentinel (see _input_path).
    sample_readers = [s for s in pipeline.steps
                      if reads_per_sample(s) or carries_sample_bundle(s)]
    sample_restore_sentinel = None
    if sample_readers:
        # The hashes are values in: the composer read them in its session.
        hashes = dict(sample_hashes or {})

        if hashes:
            sample_restore_sentinel = layout.sample_ready_sentinel_relpath("{sample}")

            lines.append("SAMPLE_HASHES = " + repr(hashes))
            lines.append("")

            # Generate restore_sample rule
            lines.append("rule restore_sample:")
            lines.append(f'    output: "{sample_restore_sentinel}"')
            lines.append("    params:")
            # The whole flag, not the value. Every entry in SAMPLE_HASHES
            # carries a real hash (load_sample_hashes refuses a registered
            # row without one), so the empty arm should be unreachable --
            # but building the flag rather than interpolating `--hash
            # {params.hash}` keeps an empty value from collapsing to a bare
            # `--hash`, where the parser refuses "expected one argument"
            # before restore-sample can name the sample. Shell-independent,
            # which quoting is not on Windows.
            lines.append(
                '        hash_arg=lambda wildcards: '
                '("--hash " + SAMPLE_HASHES[wildcards.sample]) '
                'if SAMPLE_HASHES.get(wildcards.sample) else ""'
            )
            # wfc restore-sample itself creates the Snakemake sentinel marker
            # at <project_root>/data/samples/<name>/.sample_ready (mkparents,
            # absolute path via get_project_root()), so the shell rule is
            # a single cwd-independent wfc invocation.
            lines.append("    shell:")
            lines.append(
                '        "{sys.executable} -m wfc restore-sample '
                '--name {wildcards.sample} {params.hash_arg}"'
            )
            lines.append("")

    # ── Rules (one per node) ─────────────────────────────────────────────
    口 = Step(step_num=5, name="Emit per-node method rules",
             purpose="Emit one Snakemake rule per pipeline node via _generate_rule, "
                     "wiring sentinel inputs/outputs and the sample-restore "
                     "dependency for every step that reads a per-sample sample")
    for step in pipeline.steps:
        lines.extend(_generate_rule(
            step, step_map,
            pipeline_id=_pipeline_id,
            sample_restore_sentinel=(
                sample_restore_sentinel if reads_per_sample(step) else None
            ),
        ))

    # ── onsuccess / onerror handlers ─────────────────────────────────────
    口 = Step(step_num=6, name="Emit lifecycle handlers",
             purpose="Append the onsuccess log line and the onerror handler "
                     "delegating to wfc fail_pipeline; return the joined "
                     "Snakefile. The pipeline summary is not called here: it "
                     "counts run rows, and the rows the pipeline-end walk "
                     "writes only exist after the engine returns")
    # The handler runs the callback verb through the interpreter running
    # Snakemake ({sys.executable} is formatted by the Snakefile's own
    # f-string), the same program the rules' run-step and restore-sample
    # lines name, so a run launched from an unactivated environment does
    # not depend on a `wfc` on PATH to be recorded the way it ended.
    lines.append("onsuccess:")
    lines.append('    _pipeline_logger.info("Pipeline %s completed successfully", PIPELINE_ID)')
    lines.append("")
    lines.append("onerror:")
    lines.append("    try:")
    lines.append('        shell(f"{sys.executable} -m wfc fail_pipeline --pipeline-id {PIPELINE_ID}")')
    lines.append("    except Exception:")
    lines.append('        _pipeline_logger.exception("fail_pipeline command failed")')
    lines.append('    _pipeline_logger.error("Pipeline %s FAILED", PIPELINE_ID)')
    lines.append("")

    return "\n".join(lines)
