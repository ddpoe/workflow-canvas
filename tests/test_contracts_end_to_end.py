"""Contracts unit: the diverse end-to-end path.

One project, two method nodes bound to one registered env. Node ``a`` is
the modern shape: a ``.csv`` output beside a ``.tar.gz`` output and a
directory output, an input ``columns`` block naming a strict column and a
parameter-derived one, and the bare env name in ``method.yaml``. Node
``b`` is the legacy shape: its stored ``Method.env`` row and its frozen
pipeline document both carry ``container:<name>``, the spelling
registration refuses. Both register, enrich, claim and dispatch
through the one grammar -- the same image, the same env fingerprint -- and
the legacy value is never rewritten where it is stored.

Catalog: ``env-spec-dispatch`` (the read-side strip at claim and dispatch).
Also the ``testing.requirements`` diverse end-to-end path. Stub rung,
unmarked: no container daemon.
"""
from __future__ import annotations

import json

from axiom_annotations import Step, workflow

from tests.harness import (
    DEFAULT_ENV_NAME,
    DEFAULT_MODULE,
    STUB_DIGEST,
    Phase,
    Project,
    Scenario,
    build_project,
    drive_target,
    node,
    selector,
    wire,
)
from wfc.contracts import enrich_pipeline, is_directory_slot, resolve_columns
from wfc.persistence import get_session
from wfc.environments import get as env_record
from wfc.persistence import Method
from wfc.registration import load_contract_map

ENV = DEFAULT_ENV_NAME
#: The legacy spelling: refused at registration, stripped once by every reader.
LEGACY_ENV = f"container:{ENV}"
#: The manifest's image as dispatch hands it to ``docker run``: the byo
#: record's ``local/<name>`` repo at the stub digest, ``docker://`` stripped.
IMAGE_REF = f"local/{ENV}@sha256:{STUB_DIGEST}"
COLUMNS = {"strict": ["cell_id"],
           "from_params": [{"params": ["marker"], "pattern": "{}_mean"}]}
A = ("a", "s1", "default")
B = ("b", "s1", "default")


def _stored_env(project: Project, name: str) -> str:
    """Read one registered method's ``Method.env`` row value."""
    with get_session() as session:
        return session.get(Method, project.method_ids[name]).env


def _stored_input_columns(project: Project, name: str, slot: str) -> dict:
    """Read the ``columns`` block registration stored for one input slot."""
    with get_session() as session:
        row = session.get(Method, project.method_ids[name])
        return row.contract.input_slots[slot]["columns"]


def _seed_legacy_env(project: Project, name: str, value: str) -> None:
    """Rewrite one method's stored ``env`` to the legacy prefixed spelling.

    Registration refuses the prefixed spelling, so a row carrying it can
    only come from an older project database; the test seeds it by hand.

    Args:
        project: The built project.
        name: The method whose row is rewritten.
        value: The stored value to seed.
    """
    with get_session() as session:
        row = session.get(Method, project.method_ids[name])
        row.env = value
        session.add(row)
        session.commit()


@workflow(purpose="One env, two nodes -- a modern declaration with a directory, "
                  "a .tar.gz and a column block, and a legacy node whose stored "
                  "row and frozen document still say container:<name> -- "
                  "register, enrich, claim and dispatch through the one grammar "
                  "to the same image and the same env fingerprint, with the "
                  "legacy value never rewritten",
          inputs="A selector feeding nodes a and b; b's Method.env row seeded "
                 "to the legacy spelling after registration",
          outputs="Both dispatch argvs name the manifest's image; both run rows "
                  "carry the manifest's env_fingerprint; b's document and row "
                  "keep the prefix",
          critical="Stub rung, stopped after dispatch: the method process is "
                   "the harness's local stand-in and no container starts")
def test_modern_and_legacy_nodes_dispatch_to_one_image_with_one_fingerprint(
        git_project, monkeypatch):
    口 = Step(step_num=1, name="Declare the two nodes and register the project",
             purpose="a: .csv, .tar.gz and directory outputs, a strict plus "
                     "parameter-derived column block, the bare env name; b: "
                     "the same env spelled container:<name> in its pipeline "
                     "document. build_project writes both declarations, "
                     "registers them and freezes the document",
             outputs="A built project with both methods registered")
    scn = Scenario(
        nodes=[selector(),
               node("a", inputs=[wire("sel")],
                    outputs={"table": ".csv", "bundle": ".tar.gz",
                             "figs": "directory"},
                    input_columns={"data": COLUMNS},
                    params={"marker": "cd3"}),
               node("b", inputs=[wire("sel")], document_env=LEGACY_ENV)],
        samples=["s1"],
    )
    project = build_project(scn, root=git_project, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="Seed b's stored row with the legacy spelling",
             purpose="Registration refuses container:<name>, so the row is "
                     "seeded directly; the claim step reads Method.env, and a "
                     "stored container:<name> value must still resolve",
             outputs="b's Method.env is container:<name>; the tree stays "
                     "clean because the database is not tracked")
    _seed_legacy_env(project, "b", LEGACY_ENV)

    口 = Step(step_num=3, name="Enrich through the unit over the stored contracts",
             purpose="The canvas's sparse document, enriched by enrich_pipeline "
                     "over load_contract_map: a's slot filenames derive from "
                     "the declared types, its directory slot is recognised, "
                     "its stored column block resolves the strict and the "
                     "parameter-derived names, and b's legacy env passes "
                     "through untouched",
             outputs="The enriched node dicts for a and b")
    sparse = {
        "nodes": [
            {"id": "sel", "type": "input_selector", "samples": ["s1"]},
            {"id": "a", "method": "a", "module": DEFAULT_MODULE,
             "params": {"marker": "cd3"}},
            {"id": "b", "method": "b", "module": DEFAULT_MODULE},
        ],
        "links": [{"source": "sel", "target": "a", "targetHandle": "data"},
                  {"source": "sel", "target": "b", "targetHandle": "data"}],
    }
    with get_session() as session:
        contract_map = load_contract_map(session)
    enriched = {n["id"]: n for n in enrich_pipeline(sparse, contract_map)["nodes"]}

    assert enriched["a"]["slot_outputs"] == {"table": "table.csv",
                                             "bundle": "bundle.tar.gz",
                                             "figs": "figs"}
    assert is_directory_slot(enriched["a"], "figs")
    assert not is_directory_slot(enriched["a"], "bundle")
    assert enriched["a"]["env"] == ENV
    assert enriched["b"]["env"] == LEGACY_ENV
    assert resolve_columns(_stored_input_columns(project, "a", "data"),
                           {"marker": "cd3"}) == {"cell_id", "cd3_mean"}

    口 = Step(step_num=4, name="Drive both nodes through the dispatch phase",
             purpose="Claim resolves each node's env fingerprint from its "
                     "stored row; dispatch resolves the image from the frozen "
                     "document's value -- the bare name for a, the legacy "
                     "spelling for b",
             outputs="One observation bundle per node, each with its "
                     "dispatch argv")
    obs_a = drive_target(project, "a", monkeypatch=monkeypatch,
                         through=Phase.DISPATCH)
    obs_b = drive_target(project, "b", monkeypatch=monkeypatch,
                         through=Phase.DISPATCH)
    for obs, target in ((obs_a, A), (obs_b, B)):
        assert obs.runs[target].dispatch_cmd is not None, (
            f"{target[0]}: dispatch did not reach the subprocess "
            f"(ending={obs.runs[target].ending!r}, rc={obs.exit_code(target)})"
        )

    口 = Step(step_num=5, name="Same image, same fingerprint, legacy value intact",
             purpose="Both argvs name the manifest's image; both run rows carry "
                     "the manifest's env_fingerprint; b's frozen document and "
                     "stored row still hold container:<name> -- the read side "
                     "strips the prefix and never writes the bare name back")
    assert IMAGE_REF in obs_a.dispatch_cmd(A)
    assert IMAGE_REF in obs_b.dispatch_cmd(B)

    manifest_fingerprint = env_record(ENV, project.root).env_fingerprint
    assert obs_a.run_row(A)["env_fingerprint"] == manifest_fingerprint
    assert obs_b.run_row(B)["env_fingerprint"] == manifest_fingerprint

    document = json.loads(project.pipeline_json.read_text())
    assert next(n for n in document["nodes"] if n["id"] == "b")["env"] == LEGACY_ENV
    assert _stored_env(project, "b") == LEGACY_ENV
