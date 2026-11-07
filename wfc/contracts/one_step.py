"""The one-step document: one method run on one sample, in the form a user writes.

Two doors run a single method without a pipeline the user wrote: ``wfc
try`` (a draft, with parent entries feeding some of its inputs) and the
one-method run (a registered method on a sample). Both ask the same two
questions, answered here once:

- which input the sample feeds (:func:`sample_input`): of the inputs no
  parent entry feeds, the single required one; with none required, the
  single optional one; with none left, none. Several candidates are refused
  naming them -- never ``data``, never the first one left;
- the document that runs it (:func:`one_step_document`): the method node,
  an input selector for the sample and a link naming the input it feeds,
  in the sparse form a user (or the canvas) writes, so the link keeps its
  slot through enrichment.

Pure: values in, a document out; the contract map is an argument.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from axiom_annotations import Step, task

from .enrichment import resolve_node_version
from .slots import input_is_optional

#: The input selector's node id (another when the method itself is so named).
_SELECTOR_ID = "samples"


class SampleInputError(ValueError):
    """The sample could feed more than one input, and wfc does not pick one.

    Attributes:
        qualified: The ``<module>.<method>``.
        candidates: The inputs the sample could feed, in declaration order.
    """

    def __init__(self, qualified: str, candidates: list[str], required: bool):
        """Build the refusal naming the method and the candidate inputs.

        Args:
            qualified: The ``<module>.<method>``.
            candidates: The inputs the sample could feed.
            required: Whether the candidates are required inputs.
        """
        self.qualified = qualified
        self.candidates = list(candidates)
        kind = "required" if required else "optional"
        names = ", ".join(f"'{name}'" for name in candidates)
        super().__init__(
            f"The sample feeds one input of {qualified}, but "
            f"{len(candidates)} {kind} inputs are left to feed: {names}. wfc "
            f"does not pick one.")


def sample_input(qualified: str, inputs: Mapping[str, Any] | None,
                 fed: Iterable[str] = ()) -> str | None:
    """The input a sample feeds: the one input left to feed.

    Of the declared inputs no parent entry feeds: the single required one;
    with none required, the single optional one; with none left, none.
    Required-ness is the declaration's (``required: false`` is optional; an
    input is required by default).

    Args:
        qualified: The ``<module>.<method>``, for the refusal.
        inputs: The version's declared inputs (``input_slots``, or a parsed
            ``method.yaml``'s ``inputs``).
        fed: The inputs parent entries already feed.

    Returns:
        The input's name, or ``None`` when every input is fed (or none is
        declared).

    Raises:
        SampleInputError: Two or more required inputs are left, or none is
            required and two or more optional ones are left.
    """
    fed_names = set(fed)
    unfed = [str(name) for name in (inputs or {}) if name not in fed_names]
    required = [name for name in unfed if not input_is_optional((inputs or {})[name])]
    candidates = required or unfed
    if len(candidates) > 1:
        raise SampleInputError(qualified, candidates, required=bool(required))
    return candidates[0] if candidates else None


@task(purpose="Build the one-step pipeline document a single method runs as on a "
              "sample: the method node (its version or the draft pin with its "
              "folder, its params), an input selector for the sample and a link "
              "naming the one input left to feed, in the user-facing form",
      inputs="'<module>.<method>', the sample, the contract map, an optional "
             "version or draft pin and folder, params, the inputs parent entries "
             "already feed, an optional pipeline name",
      outputs="The sparse document: name, nodes, links (targetHandle), samples",
      critical="Never feeds 'data' or the first input left: several candidates "
               "are refused naming them; an unknown method or version is refused "
               "naming it")
def one_step_document(
    method: str,
    sample: str,
    contract_map: Mapping[str, Mapping[str, Any]],
    *,
    version: str | None = None,
    folder: str | None = None,
    params: Mapping[str, Any] | None = None,
    fed: Iterable[str] = (),
    name: str | None = None,
) -> dict[str, Any]:
    """The document that runs one method on one sample.

    The method node's id is the method's name. When an input is left to
    feed, an input selector holding the sample feeds it through a link that
    names it as ``targetHandle`` (the form enrichment carries to the
    engine's ``target_slot``). When every input is fed by a parent entry (or
    none is declared) the document is the method node alone: its feeds are
    outside the document. Graph's structural checks are the caller's to run
    (Contracts imports nothing from ``wfc``); a document that holds the
    selector passes them by construction (one link, to a declared input).

    Args:
        method: ``<module>.<method>``.
        sample: The sample the step runs as.
        contract_map: Registration's contract map.
        version: The version to run (an identity or ``draft``), or ``None``
            for the newest.
        folder: A draft's folder, written on the node with the ``draft`` pin.
        params: The step's parameter values.
        fed: The inputs parent entries already feed.
        name: The pipeline's name; ``<module>.<method>-<sample>`` when
            omitted.

    Returns:
        The sparse pipeline document (``name``, ``nodes``, ``links``,
        ``samples``).

    Raises:
        MethodNotFoundError: The map holds no such method.
        SampleInputError: The sample could feed more than one input (named).
        ValueError: The version is not one of the method's, or the node
            cannot run the draft it names (named).
    """
    module, _, method_name = method.partition(".")

    口 = Step(step_num=1, name="Write the method node",
             purpose="The node a user would write: module, method, params, the "
                     "version or the draft pin and its folder")
    node: dict[str, Any] = {"id": method_name, "type": "method", "method": method_name,
                            "module": module, "params": dict(params or {})}
    if version:
        node["version"] = version
    if folder:
        node["folder"] = str(folder)

    口 = Step(step_num=2, name="Resolve its version's contract",
             purpose="An unknown method or version is refused naming it")
    _identity, contract = resolve_node_version(node, contract_map)

    口 = Step(step_num=3, name="Pick the input the sample feeds",
             purpose="The one input left to feed (sample_input); several "
                     "candidates are refused naming them")
    slot = sample_input(method, contract.get("input_slots"), fed)

    口 = Step(step_num=4, name="Write the selector and its link",
             purpose="Only when an input is left to feed; the link names it as "
                     "targetHandle so enrichment keeps its slot")
    nodes: list[dict[str, Any]] = [node]
    links: list[dict[str, Any]] = []
    if slot is not None:
        selector_id = _SELECTOR_ID if method_name != _SELECTOR_ID else f"{_SELECTOR_ID}-input"
        nodes.insert(0, {"id": selector_id, "type": "input_selector",
                         "samples": [sample]})
        links.append({"source": selector_id, "target": method_name,
                      "targetHandle": slot})
    document = {"name": name or f"{method}-{sample}", "nodes": nodes,
                "links": links, "samples": [sample]}
    return document
