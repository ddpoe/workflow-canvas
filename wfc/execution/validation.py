"""Document validation: the one verdict every door gives a pipeline document.

The canvas's validate route, its submission, ``wfc run-pipeline`` and the
runs preview all ask this module, after variable substitution, whether a
document may run. The verdict is the list of refusals, each naming the node
and the field:

- Graph's key check: a top-level or node key wfc does not read (a node's
  ``script`` included) is refused by name;
- Contracts' parameter check: each method node's params against its
  resolved version's contract (its pin, or the newest version when it has
  none), asked of Registration -- an undeclared parameter, a missing
  required one, a wrong type, a value outside ``min``/``max``/``enum``.
  Each of the node's variants under the document's ``param_sets`` runs in
  place of its params, so each gets the same check against the same
  version, its refusals naming the variant; a variant that is not a
  mapping of parameters is refused naming node and variant.
  A pin that names no version of the method is refused naming both.

A node whose method is not registered or has no version list is left to
the structure check and to enrichment, which refuse it by name. A version
with no contract declares no params, so its node's params and variants are
not checked. Nothing is filled in: a parameter the document leaves out
stays out.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from axiom_annotations import Step, task

from ..contracts import param_refusals
from ..graph import document_key_refusals
from ..registration import MalformedMethodRecordError, chosen_version, version_contracts
from .composer import _composer_session

_SYSTEM_NODE_TYPES = ("input_selector", "run_reference")


class PipelineRefusedError(ValueError):
    """A pipeline document the validator refuses; nothing was prepared or run.

    Attributes:
        refusals: Every refusal, each naming the node (or the document) and
            the field.
    """

    def __init__(self, refusals: list[str]):
        """Build the message: one refusal per line.

        Args:
            refusals: The validator's refusals (non-empty).
        """
        self.refusals = list(refusals)
        super().__init__("\n".join(self.refusals))


def _method_nodes(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The document's method nodes that name a module and a method."""
    return [
        n for n in document.get("nodes") or []
        if isinstance(n, Mapping)
        and (n.get("type") or "method") not in _SYSTEM_NODE_TYPES
        and n.get("module") and n.get("method")
    ]


@task(purpose="Give a substituted pipeline document its one validation verdict: "
              "Graph's unknown-key refusals, then each method node's params "
              "and each of its param_sets variants against its resolved "
              "version's contract through Contracts",
      inputs="A pipeline document after variable substitution",
      outputs="Every refusal, naming node, field and version (empty when the "
              "document may run)",
      critical="The same function at every door: canvas validate, canvas "
               "submit, run-pipeline and the runs preview")
def validation_refusals(document: Mapping[str, Any]) -> list[str]:
    """List every refusal of a substituted pipeline document.

    Args:
        document: The document after ``{$var}`` substitution.

    Returns:
        The refusals: unknown keys first, then, per method node in document
        order, its params' refusals followed by each of its ``param_sets``
        variants' refusals in document order. Empty when the document may
        run.

    Raises:
        DatabaseUnreachableError: A method node's contract is needed and the
            project database cannot be reached.
    """
    口 = Step(step_num=1, name="Refuse unknown keys",
             purpose="Graph names every top-level and node key wfc does not read")
    refusals = document_key_refusals(document)

    口 = Step(step_num=2, name="Check each method node's params and variants against its version",
             purpose="Resolve the node's pin (or the newest version) from "
                     "Registration's versions and contracts, then hand its "
                     "params schema to Contracts' parameter check for the "
                     "node's params and once per param_sets variant",
             critical="A method with no versions is skipped here: the structure "
                      "check and enrichment refuse it by name. A version with "
                      "no contract declares no params and is not checked")
    nodes = _method_nodes(document)
    if not nodes:
        return refusals
    param_sets = document.get("param_sets")
    param_sets = param_sets if isinstance(param_sets, Mapping) else {}
    with _composer_session():
        pass  # the probe: an unreachable database is named before any read
    versions_of: dict[tuple[str, str], list | None] = {}
    for node in nodes:
        module, method = str(node["module"]), str(node["method"])
        if (module, method) not in versions_of:
            try:
                versions_of[(module, method)] = version_contracts(module, method)
            except MalformedMethodRecordError:
                versions_of[(module, method)] = None
        versions = versions_of[(module, method)]
        if not versions:
            continue
        pin = node.get("version")
        chosen = chosen_version(versions, pin, entry_of=lambda vc: vc[0].entry)
        if chosen is None:
            labels = ", ".join(f"v{v.entry.label}" for v, _ in versions)
            refusals.append(
                f"Node '{node.get('id')}' ({module}.{method}): pinned version "
                f"{pin} is not a version of the method (its versions: "
                f"{labels})")
            continue
        version, contract = chosen
        if contract is None:
            continue  # a version with no contract declares no params
        node_id, label = str(node.get("id")), f"v{version.entry.label}"
        refusals.extend(param_refusals(
            node_id, f"{module}.{method}", label,
            node.get("params"), contract.params_schema))
        refusals.extend(_variant_refusals(
            node_id, f"{module}.{method}", label,
            param_sets.get(node_id), contract.params_schema))
    return refusals


def _variant_refusals(node_id: str, method_ref: str, version_label: str,
                      variants: Any, params_schema: Mapping[str, Any] | None,
                      ) -> list[str]:
    """Check each of a node's ``param_sets`` variants like its params.

    Args:
        node_id: The node's document id.
        method_ref: ``module.method``.
        version_label: The resolved version's label, e.g. ``"v2"``.
        variants: The document's ``param_sets[node_id]``: variant name to
            params, or ``None`` when the node has none.
        params_schema: The version's declared params.

    Returns:
        The refusals, variants in document order; a variant that is not a
        mapping of parameters is refused naming node and variant.
    """
    if variants is None:
        return []
    if not isinstance(variants, Mapping):
        return [f"Node '{node_id}': its param_sets entry must map variant "
                f"names to params, got {type(variants).__name__}"]
    refusals: list[str] = []
    for name, values in variants.items():
        if not isinstance(values, Mapping):
            refusals.append(f"Node '{node_id}' variant '{name}': must be a "
                            f"mapping of parameters, got "
                            f"{type(values).__name__}")
            continue
        refusals.extend(param_refusals(node_id, method_ref, version_label,
                                       values, params_schema, variant=str(name)))
    return refusals


@task(purpose="Raise the validator's refusals for a substituted pipeline "
              "document, so nothing is prepared or run from a refused one",
      inputs="A pipeline document after variable substitution",
      outputs="None; PipelineRefusedError carrying every refusal when the "
              "document has any")
def refuse_invalid(document: Mapping[str, Any]) -> None:
    """Raise the validator's refusals for a substituted document, if any.

    Args:
        document: The document after ``{$var}`` substitution.

    Raises:
        PipelineRefusedError: The document has at least one refusal.
        DatabaseUnreachableError: The database cannot be reached.
    """
    refusals = validation_refusals(document)
    if refusals:
        raise PipelineRefusedError(refusals)
