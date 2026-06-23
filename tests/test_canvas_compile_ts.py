"""
Compile-correctness regression guard for the canvas's
``compilePipelineToJSON`` (wfc/canvas/static/src/lib/compile.ts).

Why this file exists
--------------------
Iteration 1 of pev-2026-04-17-parameter-sweeps-chip-ux shipped a compile
function that silently dropped every (sample × sweep_variant) row from
``explicit_combos`` whenever any per-sample override was defined on the
same node.  Because the engine treats ``explicit_combos`` as the
exclusive run list in selective mode (``wfc/snakemake_gen.py`` L604-606),
the bug caused the engine to run ONLY the override rows, dropping every
sweep run.

User deferred adding vitest, so we drive the real TS function via a
small Node harness (``tests/js_harness/compile_harness.mjs``) using
``node --experimental-strip-types``.  The harness imports from
``compile.ts`` directly (not ``pipeline.ts``) to avoid pulling in
svelte-store runtime deps that would need ``node_modules`` installed.

If ``node`` is unavailable or too old (<22.6), the test skips.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from dflow.core.decorators import workflow


PROJECT_ROOT = Path(__file__).resolve().parent.parent
HARNESS = PROJECT_ROOT / "tests" / "js_harness" / "compile_harness.mjs"
COMPILE_TS = (
    PROJECT_ROOT / "wfc" / "canvas" / "static" / "src" / "lib" / "compile.ts"
)


def _node_available() -> bool:
    """Return True iff a Node.js binary exists on PATH supporting --experimental-strip-types."""
    node = shutil.which("node")
    if node is None:
        return False
    try:
        result = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=10
        )
    except (subprocess.SubprocessError, OSError):
        return False
    if result.returncode != 0:
        return False
    # "v22.21.0" -> 22
    try:
        major = int(result.stdout.strip().lstrip("v").split(".")[0])
    except (ValueError, IndexError):
        return False
    return major >= 22


def _run_compile(state: dict) -> dict:
    """Invoke the Node harness, feed ``state`` as JSON on stdin, return compiled dict."""
    assert HARNESS.exists(), f"harness missing at {HARNESS}"
    assert COMPILE_TS.exists(), f"compile.ts missing at {COMPILE_TS}"
    result = subprocess.run(
        ["node", "--experimental-strip-types", str(HARNESS)],
        input=json.dumps(state),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"harness failed (rc={result.returncode}):\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return json.loads(result.stdout)


# =============================================================================
# Test 1: Pitch fixture — sweep + single-sample override produces 7-row matrix
# =============================================================================


@workflow(
    purpose="Regression guard for the iteration-1 bug where compilePipelineToJSON "
    "dropped (sample × sweep_variant) rows from explicit_combos whenever "
    "any per-sample override existed.  Verifies the canonical pitch "
    "fixture emits all 6 cartesian rows plus the 1 override row (7 total)."
)
def test_compile_sweep_plus_override_emits_full_matrix():
    """Pitch fixture:
    - samples = [A, B, C]
    - filter node sweeps threshold in {0.5, 0.7} -> v1, v2
    - override on A: threshold=0.9 -> A__o1

    Expected:
      param_sets.filter = {v1:{threshold:0.5}, v2:{threshold:0.7}, A__o1:{threshold:0.9}}
      explicit_combos = [{A,v1},{A,v2},{B,v1},{B,v2},{C,v1},{C,v2},{A,A__o1}]
      = 7 rows.
    """
    if not _node_available():
        pytest.skip("Node.js >=22 with --experimental-strip-types not available")

    authoring_state = {
        "name": "sweep-plus-override",
        "nodes": [
            {
                "id": "filter",
                "position": {"x": 0, "y": 0},
                "data": {
                    "method": "filter_cells",
                    "module": "demo",
                    "paramValues": {"threshold": 0.5},
                    "variants": {"threshold": {"v1": 0.5, "v2": 0.7}},
                    "sampleOverrides": {"A": {"threshold": 0.9}},
                },
            }
        ],
        "edges": [],
        "samples": ["A", "B", "C"],
    }

    compiled = _run_compile(authoring_state)

    # param_sets contains exactly the 3 variants.
    assert compiled["param_sets"] == {
        "filter": {
            "v1": {"threshold": 0.5},
            "v2": {"threshold": 0.7},
            "A__o1": {"threshold": 0.9},
        }
    }

    # explicit_combos contains 6 cartesian rows + 1 override row = 7 rows.
    combos = compiled["explicit_combos"]
    assert len(combos) == 7, f"expected 7 combos, got {len(combos)}: {combos}"

    combo_set = {(c["sample"], c["variant"]) for c in combos}
    expected_cartesian = {
        ("A", "v1"), ("A", "v2"),
        ("B", "v1"), ("B", "v2"),
        ("C", "v1"), ("C", "v2"),
    }
    assert expected_cartesian.issubset(combo_set), (
        "missing cartesian rows (iteration-1 regression): "
        f"{expected_cartesian - combo_set}"
    )
    assert ("A", "A__o1") in combo_set, "override row missing"


# =============================================================================
# Test 2: Sweep-only — no overrides anywhere -> empty explicit_combos
# =============================================================================


@workflow(
    purpose="Pipelines with only sweeps (no overrides) must leave "
    "explicit_combos omitted so the engine stays in cartesian mode "
    "with its natural variant padding.  Guards against over-emitting "
    "a redundant matrix when no override forces selective mode."
)
def test_compile_sweep_only_omits_explicit_combos():
    """Two samples, one sweep on one node, zero overrides.

    Expected: explicit_combos is either absent or empty.  The engine
    handles the cartesian via expand_variant_combos cartesian mode.
    """
    if not _node_available():
        pytest.skip("Node.js >=22 with --experimental-strip-types not available")

    authoring_state = {
        "name": "sweep-only",
        "nodes": [
            {
                "id": "filter",
                "position": {"x": 0, "y": 0},
                "data": {
                    "method": "filter_cells",
                    "module": "demo",
                    "paramValues": {"threshold": 0.5},
                    "variants": {"threshold": {"v1": 0.5, "v2": 0.7}},
                },
            }
        ],
        "edges": [],
        "samples": ["A", "B"],
    }

    compiled = _run_compile(authoring_state)
    assert compiled["param_sets"] == {
        "filter": {"v1": {"threshold": 0.5}, "v2": {"threshold": 0.7}}
    }
    # explicit_combos should be omitted entirely (cartesian-mode engine path).
    assert "explicit_combos" not in compiled or compiled["explicit_combos"] == []


# =============================================================================
# Test 3: Override-only — no sweeps, one override row per overridden sample
# =============================================================================


@workflow(
    purpose="Override-only case: no sweep variants exist, so each "
    "non-overridden sample is emitted against a synthetic 'default' "
    "variant. Without that row the engine's selective mode silently "
    "drops the sample (there's no per-sample padding on the explicit-"
    "combos path — verified against wfc.snakemake_gen.expand_variant_combos)."
)
def test_compile_override_only_emits_default_rows_for_unreferenced_samples():
    """One sample override, no sweep variants on the node.

    Earlier behaviour emitted only the override row; non-overridden
    samples vanished because ``expand_variant_combos`` returns
    ``explicit_combos`` as-is (no padding) whenever selective mode is
    active. The fix emits ``{sample, variant: 'default'}`` for every
    sample not named in an override row so all samples still run; nodes
    that don't list 'default' in their param_sets fall back to
    ``node.params`` via the run_step variant lookup.
    """
    if not _node_available():
        pytest.skip("Node.js >=22 with --experimental-strip-types not available")

    authoring_state = {
        "name": "override-only",
        "nodes": [
            {
                "id": "filter",
                "position": {"x": 0, "y": 0},
                "data": {
                    "method": "filter_cells",
                    "module": "demo",
                    "paramValues": {"threshold": 0.5},
                    "sampleOverrides": {"A": {"threshold": 0.9}},
                },
            }
        ],
        "edges": [],
        "samples": ["A", "B"],
    }

    compiled = _run_compile(authoring_state)
    assert compiled["param_sets"] == {
        "filter": {"A__o1": {"threshold": 0.9}}
    }
    # B (non-overridden) runs on the synthetic 'default' variant; A runs
    # on its override variant. Default rows are emitted before override
    # rows — the ordering matters for deterministic test assertions only.
    assert compiled["explicit_combos"] == [
        {"sample": "B", "variant": "default"},
        {"sample": "A", "variant": "A__o1"},
    ]


# =============================================================================
# Test 4: Override whose resolved params match a sweep variant is deduplicated
# =============================================================================


@workflow(
    purpose="When a per-sample override resolves to the same param dict "
    "as an existing sweep variant, skip emitting a redundant override "
    "variant. The sample still runs that combo via the cartesian, so "
    "emitting X__o1 would just produce the same output twice under a "
    "different NID."
)
def test_compile_dedupes_override_matching_sweep_variant():
    """Override on A sets threshold=0.5, which already equals v1.

    Expected: no ``A__o1`` variant in ``param_sets``; no ``(A, A__o1)``
    row in ``explicit_combos``. The cartesian rows (A×v1, A×v2, B×v1,
    B×v2) still stand because another override elsewhere is not present
    — but this fixture has no such forcing override, so explicit_combos
    should be omitted entirely (cartesian mode).
    """
    if not _node_available():
        pytest.skip("Node.js >=22 with --experimental-strip-types not available")

    authoring_state = {
        "name": "dedup-matching-override",
        "nodes": [
            {
                "id": "filter",
                "position": {"x": 0, "y": 0},
                "data": {
                    "method": "filter_cells",
                    "module": "demo",
                    "paramValues": {"threshold": 0.5},
                    "variants": {"threshold": {"v1": 0.5, "v2": 0.7}},
                    # A's override resolves to {threshold: 0.5} — identical to v1.
                    "sampleOverrides": {"A": {"threshold": 0.5}},
                },
            }
        ],
        "edges": [],
        "samples": ["A", "B"],
    }

    compiled = _run_compile(authoring_state)
    # Only the two sweep variants; no redundant A__o1.
    assert compiled["param_sets"] == {
        "filter": {"v1": {"threshold": 0.5}, "v2": {"threshold": 0.7}}
    }
    # No override survived dedup → no forcing of selective mode → omit combos.
    assert "explicit_combos" not in compiled or compiled["explicit_combos"] == []


# =============================================================================
# Test 5: Per-sample sweep → cartesian expanded into X__o{n} variants
# =============================================================================


@workflow(
    purpose="Per-sample sweeps: sample A authors its own variant list on a "
    "param and the compiler emits one X__o{n} per variant value. Sample A "
    "still participates in the global cartesian — the per-sample sweep is "
    "additive and layers on top of the global sweep."
)
def test_compile_per_sample_sweep_emits_multiple_override_variants():
    """One sample with per-sample variants on a param (no base override).

    authoring:
      samples = [A, B]
      filter node: paramValues={threshold: 0.5}
                   (no global variants)
                   sampleVariants: {A: {threshold: {v1: 0.9, v2: 0.95}}}

    Expected param_sets:
      filter: {A__o1: {threshold: 0.9}, A__o2: {threshold: 0.95}}

    Expected explicit_combos (overrides force selective mode; no sweep
    variants means the 'else' branch emits the synthetic 'default' row
    for the non-overridden sample):
      [{B, default}, {A, A__o1}, {A, A__o2}]
    """
    if not _node_available():
        pytest.skip("Node.js >=22 with --experimental-strip-types not available")

    authoring_state = {
        "name": "per-sample-sweep",
        "nodes": [
            {
                "id": "filter",
                "position": {"x": 0, "y": 0},
                "data": {
                    "method": "filter_cells",
                    "module": "demo",
                    "paramValues": {"threshold": 0.5},
                    "sampleVariants": {
                        "A": {"threshold": {"v1": 0.9, "v2": 0.95}},
                    },
                },
            }
        ],
        "edges": [],
        "samples": ["A", "B"],
    }

    compiled = _run_compile(authoring_state)
    assert compiled["param_sets"] == {
        "filter": {
            "A__o1": {"threshold": 0.9},
            "A__o2": {"threshold": 0.95},
        }
    }
    assert compiled["explicit_combos"] == [
        {"sample": "B", "variant": "default"},
        {"sample": "A", "variant": "A__o1"},
        {"sample": "A", "variant": "A__o2"},
    ]


# =============================================================================
# Test 5: null/undefined in base params is stripped at export time
# =============================================================================


@workflow(
    purpose="Optional numeric params committed blank in the inspector arrive "
    "as null in paramValues; they must be omitted from the submitted "
    "params dict so wfc's params.get(name) returns None and the method "
    "takes its unset path. Regression guard for the 0.2.13 fix that "
    "unblocked blank-commit on type: float/int rows in ValueList.svelte."
)
def test_compile_strips_null_base_params():
    """Node with one real param + one null + one undefined.

    Expected: only the real param survives in the submitted params dict.
    """
    if not _node_available():
        pytest.skip("Node.js >=22 with --experimental-strip-types not available")

    authoring_state = {
        "name": "strip-null-params",
        "nodes": [
            {
                "id": "label",
                "position": {"x": 0, "y": 0},
                "data": {
                    "method": "binary_feature_labeling",
                    "module": "demo",
                    "paramValues": {
                        "positive_threshold": 2.4,
                        "negative_threshold": None,  # blank-committed optional
                        "mode": None,                 # also blank
                    },
                },
            }
        ],
        "edges": [],
        "samples": ["A"],
    }

    compiled = _run_compile(authoring_state)
    node = next(n for n in compiled["nodes"] if n["id"] == "label")
    assert node["params"] == {"positive_threshold": 2.4}, (
        f"nulls should be stripped from base params, got: {node['params']}"
    )
