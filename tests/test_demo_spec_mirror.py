"""The demo's browser smoke must name the methods the demo registers.

``wfc/canvas/static/tests/e2e/demo-pipeline.spec.ts`` cannot read the demo's
package assets from the Vite server, so it inlines a copy of the demo pipeline
document and the demo module registry and serves them to the app through
``page.route``. Nothing in the spec ties that copy to the real demo, so when
the demo changes the spec keeps asserting the old shape — and stays green
while asserting a rendering the product no longer produces. That already
happened once: the ``__demo__`` method prefix landed and the mirror kept the
bare names.

This gate makes the drift detectable. It does not remove the duplication —
whether the spec can read the demo off disk instead is a separate, unverified
lead.

**The oracle is what the scaffold registers, not what the asset holds.**
``wfc/demo/assets/pipeline.json`` carries the prefixed names already --
``scaffold.py`` copies that file verbatim (``shutil.copy2``) and applies no
prefix, so it had no other option. The prefix-at-the-copy-step rule is true
of the five ``assets/methods/<name>/`` directories, whose names the scaffold
does rewrite as it registers them; it was never true of the pipeline
document. The asset is therefore a second *derived* copy of the same fact,
not the registration source of truth, and pinning the spec to it would pin
one copy to another with nothing anchoring either. The comparison is against
:data:`wfc.demo.scaffold.DEMO_METHODS`, which is what the scaffold registers.
The asset gets its own gate below, against the same oracle.

**Slot names are deliberately out of scope.** ``data`` / ``clean`` /
``filtered`` / ``labeled`` / ``summary`` / ``figure`` are each method's
contract, not its name, and the demo's namespacing leaves them alone. A gate
coupling them to the prefix would be asserting something untrue.

The spec is read as text. No TypeScript parser, no Node process, no
dependency — that is the whole reason this route was taken over reading the
demo's assets from inside the spec.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from wfc.demo.scaffold import DEMO_METHODS, DEMO_MODULE, DEMO_SAMPLES

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_REL = "wfc/canvas/static/tests/e2e/demo-pipeline.spec.ts"
SPEC_PATH = REPO_ROOT / Path(SPEC_REL)


def _brace_block(text: str, anchor: str) -> str:
    """Return the ``{...}`` object literal that follows *anchor* in *text*.

    Args:
        text: Full source of the spec file.
        anchor: Literal substring the object literal follows, e.g.
            ``"const MODULES ="``.

    Returns:
        The object literal including its outer braces.
    """
    start = text.index(anchor)
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at:i + 1]
    raise AssertionError(f"{SPEC_REL}: unterminated object literal after {anchor!r}")


def _top_level_keys(block: str) -> list[str]:
    """Return the keys declared at the outermost level of an object literal.

    Depth is tracked per line, so nested objects are skipped however they are
    formatted.

    Args:
        block: An object literal including its outer braces.

    Returns:
        The outermost keys, in source order.
    """
    keys: list[str] = []
    depth = 0
    for line in block.splitlines():
        if depth == 1:
            match = re.match(r"\s*([A-Za-z_]\w*)\s*:", line)
            if match:
                keys.append(match.group(1))
        depth += line.count("{") - line.count("}")
    return keys


def _drift_message(site: str, found: set[str], expected: set[str]) -> str:
    """Build the failure text naming the drifted names and both files.

    Args:
        site: Human-readable name of the place in the spec that drifted.
        found: Method names the spec carries at that site.
        expected: Method names the scaffold registers.

    Returns:
        A multi-line message telling the next reader what to change.
    """
    missing = sorted(expected - found)
    extra = sorted(found - expected)
    lines = [
        "DEMO MIRROR DRIFT - the demo's browser smoke names methods the demo "
        "does not register.",
        f"    site:   {site}",
        f"    spec:   {SPEC_REL}",
        "    oracle: wfc/demo/scaffold.py::DEMO_METHODS",
    ]
    if missing:
        lines.append(f"    registered by the demo, absent from the spec: {missing}")
    if extra:
        lines.append(f"    in the spec, not registered by the demo: {extra}")
    lines.append(
        "    The spec inlines a copy of the demo pipeline and the demo module "
        "registry. It must mirror what the scaffold REGISTERS - DEMO_METHODS, "
        "which carries the __demo__ prefix. wfc/demo/assets/pipeline.json "
        "carries the prefixed names too, but it is a second derived copy "
        "(the scaffold copies that file verbatim), not the source of truth, "
        "so it is not the oracle - it is gated against DEMO_METHODS itself. "
        "Fix the spec, unless DEMO_METHODS itself changed."
    )
    return "\n".join(lines)


def test_demo_browser_smoke_mirrors_the_methods_the_scaffold_registers():
    """All three inlined method-name sites in the spec match ``DEMO_METHODS``."""
    spec = SPEC_PATH.read_text(encoding="utf-8")
    expected = set(DEMO_METHODS)
    assert len(expected) == 5, f"DEMO_METHODS is no longer five names: {DEMO_METHODS}"

    pipeline = _brace_block(spec, "const DEMO_PIPELINE =")
    modules = _brace_block(spec, "const MODULES =")
    label_list = re.search(r"for \(const label of \[(.*?)\]", spec, re.S)
    assert label_list is not None, (
        f"{SPEC_REL}: no `for (const label of [...]` node-label list found. "
        "This gate reads that list as text; if the spec restructured it, "
        "re-point the gate rather than deleting it."
    )

    sites = {
        "DEMO_PIPELINE.nodes[].method":
            {m for m in re.findall(r"method:\s*'([^']*)'", pipeline) if m},
        "MODULES.__demo__.methods (keys)":
            set(_top_level_keys(_brace_block(modules, "methods:"))),
        "the node-label list in the test body":
            set(re.findall(r"'([^']+)'", label_list.group(1))),
    }
    for site, found in sites.items():
        assert found == expected, _drift_message(site, found, expected)


def test_the_shipped_demo_asset_names_the_methods_and_samples_the_demo_registers():
    """``wfc/demo/assets/pipeline.json`` agrees with the demo registry.

    The asset is a *product* file, not a test fixture: ``scaffold.py`` copies
    it verbatim into the user's project and ``wfc/canvas/routes/history.py``
    returns it to ``?pipeline=demo``. Nothing rewrites it on the way, so a
    method name that drifts from ``DEMO_METHODS`` misses the registry, the
    enrichment pass emits the node with an empty slot map and a fallback
    script path, and the user sees generic slots in the canvas and a
    missing-script failure on run. Quiet, and user-visible.

    Same oracle as the spec gate above, for the same reason: the registry is
    what the demo actually creates.
    """
    asset = json.loads(
        (REPO_ROOT / "wfc" / "demo" / "assets" / "pipeline.json")
        .read_text(encoding="utf-8")
    )
    methods = {n["method"] for n in asset["nodes"] if n.get("method")}
    assert methods == set(DEMO_METHODS), (
        "DEMO ASSET DRIFT - wfc/demo/assets/pipeline.json names methods the "
        f"demo does not register.\n    asset:  {sorted(methods)}\n"
        f"    oracle: {sorted(DEMO_METHODS)}\n"
        "    The scaffold copies this file verbatim and applies no prefix, "
        "so whatever it holds is what the user's project gets. A name the "
        "registry does not carry enriches to an empty slot map and a "
        "fallback script path."
    )

    expected_samples = {f"{DEMO_MODULE}{s}" for s in DEMO_SAMPLES}
    assert set(asset["samples"]) == expected_samples, (
        "DEMO ASSET DRIFT - the asset's `samples` list names samples the "
        f"demo does not register.\n    asset:  {sorted(asset['samples'])}\n"
        f"    oracle: {sorted(expected_samples)}"
    )
    selector_samples = {
        s for n in asset["nodes"] for s in n.get("samples", [])
    }
    assert selector_samples == expected_samples, (
        "DEMO ASSET DRIFT - the input selector names samples the demo does "
        f"not register.\n    asset:  {sorted(selector_samples)}\n"
        f"    oracle: {sorted(expected_samples)}"
    )
