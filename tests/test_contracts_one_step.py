"""The one-step document and the one rule for which input a sample feeds.

``wfc try`` and the one-method run both run a single method on a sample;
Contracts answers once which input the sample feeds (the one left to feed,
never ``data`` and never the first one left) and writes the document in the
form a user writes, so its link keeps its slot through enrichment.
"""
from __future__ import annotations

import pytest

from wfc.contracts import (
    MethodNotFoundError,
    SampleInputError,
    enrich_pipeline,
    one_step_document,
)

_OUT = {"table": {"type": ".csv"}}


def _contract_map(inputs: dict, *, draft_folder: str | None = None) -> dict:
    """A contract map holding ``mod.m`` with one version (and a draft)."""
    contract = {"input_slots": inputs, "output_slots": _OUT, "params": None,
                "env": "py", "label": 1}
    versions = {"v1id": contract}
    if draft_folder is not None:
        versions["draft"] = {**contract, "label": None, "folder": draft_folder}
    return {"mod.m": {**contract, "version": "v1id", "versions": versions}}


def _fed_slot(document: dict) -> str | None:
    links = document["links"]
    assert len(links) <= 1
    return links[0]["targetHandle"] if links else None


@pytest.mark.parametrize(("inputs", "fed", "expected"), [
    ({"reads": {"type": ".csv"}}, (), "reads"),
    ({"opt": {"type": ".csv", "required": False}, "main": {"type": ".csv"}},
     (), "main"),
    ({"opt": {"type": ".csv", "required": False}}, (), "opt"),
    ({"a": {"type": ".csv"}, "b": {"type": ".csv"}}, ("a",), "b"),
    ({"a": {"type": ".csv"}, "b": {"type": ".csv"}}, ("a", "b"), None),
], ids=["one-input", "one-required-plus-optional", "only-optional",
        "the-other-required-fed", "every-input-fed"])
def test_the_sample_feeds_the_one_input_left_to_feed(inputs, fed, expected):
    document = one_step_document("mod.m", "s1", _contract_map(inputs), fed=fed)

    assert _fed_slot(document) == expected
    selectors = [n for n in document["nodes"] if n["type"] == "input_selector"]
    if expected is None:
        assert [n["id"] for n in document["nodes"]] == ["m"]
    else:
        assert [s["samples"] for s in selectors] == [["s1"]]
        assert document["links"][0]["source"] == selectors[0]["id"]
        assert document["links"][0]["target"] == "m"
    assert document["samples"] == ["s1"]


@pytest.mark.parametrize(("inputs", "named"), [
    ({"data": {"type": ".csv"}, "b": {"type": ".csv"},
      "o": {"type": ".csv", "required": False}}, ["data", "b"]),
    ({"x": {"type": ".csv", "required": False},
      "y": {"type": ".csv", "required": False}}, ["x", "y"]),
], ids=["two-required", "two-optional-none-required"])
def test_several_candidate_inputs_are_refused_naming_them(inputs, named):
    with pytest.raises(SampleInputError) as caught:
        one_step_document("mod.m", "s1", _contract_map(inputs))

    assert caught.value.candidates == named
    assert caught.value.qualified == "mod.m"
    for name in named:
        assert f"'{name}'" in str(caught.value)


def test_an_unknown_method_or_version_is_refused_naming_it():
    contract_map = _contract_map({"reads": {"type": ".csv"}})

    with pytest.raises(MethodNotFoundError, match=r"mod\.nope"):
        one_step_document("mod.nope", "s1", contract_map)
    with pytest.raises(ValueError, match="v9id"):
        one_step_document("mod.m", "s1", contract_map, version="v9id")


def test_the_document_keeps_its_slot_through_enrichment():
    contract_map = _contract_map({"opt": {"type": ".csv", "required": False},
                                  "reads": {"type": ".csv"}})
    document = one_step_document("mod.m", "s1", contract_map,
                                 params={"k": 1}, name="one")

    enriched = enrich_pipeline(document, contract_map)

    assert enriched["links"] == [{"source": "samples", "target": "m",
                                  "target_slot": "reads"}]
    assert enriched["name"] == "one"
    method_node = next(n for n in enriched["nodes"] if n["id"] == "m")
    assert method_node["version"] == "v1id"
    assert method_node["params"] == {"k": 1}


def test_a_draft_node_keeps_its_pin_and_folder():
    contract_map = _contract_map({"reads": {"type": ".csv"}}, draft_folder="/w/m")

    document = one_step_document("mod.m", "s1", contract_map, version="draft",
                                 folder="/w/m")
    method_node = next(n for n in document["nodes"] if n["id"] == "m")
    enriched = next(n for n in enrich_pipeline(document, contract_map)["nodes"]
                    if n["id"] == "m")

    assert (method_node["version"], method_node["folder"]) == ("draft", "/w/m")
    assert (enriched["version"], enriched["folder"]) == ("draft", "/w/m")
    with pytest.raises(ValueError, match="draft"):
        one_step_document("mod.m", "s1", contract_map, version="draft",
                          folder="/elsewhere/m")


def test_a_try_feeds_its_sample_to_the_input_no_parent_entry_feeds(tmp_path):
    """``wfc try``'s document is the one-step document: parent entries feed
    some inputs, the sample the one left, and several left are refused."""
    from pathlib import Path

    from wfc.execution.try_run import TryRequest, try_document

    folder = str(Path("/w/m"))
    contract_map = _contract_map({"a": {"type": ".csv"}, "b": {"type": ".csv"}},
                                 draft_folder=folder)

    def request(*entries: str) -> TryRequest:
        return TryRequest(module="mod", method="m", folder=Path(folder),
                          params={}, sample="s1", parent_entries=list(entries))

    document = try_document(request("a:table:12"), contract_map)
    draft = next(n for n in document["nodes"] if n["id"] == "m")

    assert document["name"] == "try-m"
    assert _fed_slot(document) == "b"
    assert (draft["version"], draft["folder"]) == ("draft", folder)
    assert _fed_slot(try_document(request("a:table:12", "b:table:13"),
                                  contract_map)) is None
    with pytest.raises(SampleInputError, match="'a', 'b'"):
        try_document(request(), contract_map)
