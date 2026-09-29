"""The parse stage's refusals (Graph unit, catalog cases ``parse-env-required``
and ``parse-unknown-node``).

Literal canonical documents through ``wfc.graph.load_pipeline`` with a literal
(empty) contract map and no reference outputs: the parse stage refuses a
method node that declares no env, accepts the deprecated ``env_strategy``
alias, and refuses a link that names a node the document does not have --
source first, then target -- naming the id in each message.
"""

from __future__ import annotations

import pytest
from axiom_annotations import workflow

from wfc.graph import load_pipeline


def _method(node_id: str, **fields) -> dict:
    """A canonical method node (enriched shape: script and module present)."""
    node = {
        "id": node_id,
        "type": "method",
        "method": node_id,
        "module": "demo",
        "script": f"methods/{node_id}/{node_id}.py",
        "params": {},
    }
    node.update(fields)
    return node


def _selector_rooted(method_nodes: list[dict], links: list[dict]) -> dict:
    """A document rooted at one per-sample selector carrying sample ``S1``."""
    return {
        "nodes": [
            {"id": "sel", "type": "input_selector", "samples": ["S1"]},
            *method_nodes,
        ],
        "links": links,
        "samples": [],
    }


def _load(document: dict):
    return load_pipeline(document, contract_map={}, reference_outputs={})


@workflow(purpose="A method node declaring no env is refused at parse naming "
                  "the node and its method; the deprecated env_strategy alias "
                  "still loads and lands on the step's env")
def test_parse_requires_an_env_on_every_method_node():
    no_env = _selector_rooted(
        [_method("clean")],
        [{"source": "sel", "target": "clean", "target_slot": "data"}],
    )
    with pytest.raises(ValueError) as refused:
        _load(no_env)
    message = str(refused.value)
    assert "env required" in message
    assert "'clean'" in message and "method=clean" in message

    aliased = _selector_rooted(
        [_method("clean", env_strategy="container:demo")],
        [{"source": "sel", "target": "clean", "target_slot": "data"}],
    )
    pipeline = _load(aliased)
    (step,) = pipeline.steps
    assert step.env == "container:demo"


@workflow(purpose="A link naming a node the document does not have is refused "
                  "at parse naming the unknown id -- an unknown source, then an "
                  "unknown target")
def test_parse_refuses_a_link_naming_an_unknown_node():
    unknown_source = _selector_rooted(
        [_method("a", env="container:demo"), _method("b", env="container:demo")],
        [
            {"source": "sel", "target": "a", "target_slot": "data"},
            {"source": "ghost", "target": "b", "target_slot": "data"},
        ],
    )
    with pytest.raises(ValueError, match="unknown source node ghost"):
        _load(unknown_source)

    unknown_target = _selector_rooted(
        [_method("a", env="container:demo")],
        [
            {"source": "sel", "target": "a", "target_slot": "data"},
            {"source": "a", "target": "ghost", "target_slot": "data"},
        ],
    )
    with pytest.raises(ValueError, match="unknown target node ghost"):
        _load(unknown_target)
