"""The History payload carries what went into a run's cache key.

``/api/wfc/runs`` reports, per run, the inputs its key was built over -- the
version's code fingerprint, the run's env name and env fingerprint, each
parent's cache key and each sample read's content hash -- and the values the
run recorded that are not in the key: its commit, the wfc version and the
pipeline document's hash. A run keyed under an earlier key recipe is flagged
yes/no; the recipe number itself never reaches the payload. Computed when
History loads: the params hash, the inputs hash over the recorded inputs
(the one the key was built over) and each param's declared type.

Declared fixture deviations: ``_make_pre_column_db`` hand-rolls a database
from before the recorded columns existed (no production path writes that
shape any more); production's engine bootstrap then upgrades it.
``test_param_types_are_the_declared_ones`` drives a step whose params include
one its contract does not declare through ``drive_target``, which runs
production ``run_step`` directly: a document carrying that param is refused at
validation, so a direct step drive is the shape such a run row comes from.
Everything else is built by the harness through production registration and
``run_step``.
"""

from __future__ import annotations

import importlib.metadata
import json
import sqlite3
import subprocess
from pathlib import Path

from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures.fakes import bind_provider
from tests.fixtures.routes import canvas_client
from tests.harness import (
    ModuleSpec,
    Scenario,
    build_project,
    drive_target,
    node,
    run_scenario,
    selector,
    wire,
)
from wfc import layout
from wfc.demo.remove import remove_demo
from wfc.canvas.wfc_provider import recorded_inputs_hash, recorded_params_hash
from wfc.identity import (
    SampleIdentity,
    UpstreamRunIdentity,
    build_cache_key,
    build_input_fingerprint,
    canonical_json_hash,
)
from wfc.persistence import MethodVersion, Run, RunInput, Sample, get_session, reset_engine

#: Payload keys that would leak the recipe number to the client.
RECIPE_KEYS = {"keyRecipe", "key_recipe", "recipe"}


def _head(root: Path) -> str:
    """The repository's HEAD commit."""
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit_file(root: Path, name: str, text: str) -> None:
    """Commit one new file, so HEAD moves and the tree stays clean."""
    (root / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", f"add {name}"], cwd=root,
                   check=True, capture_output=True)


def _row(run_id: int) -> Run:
    with get_session() as session:
        row = session.get(Run, run_id)
        session.expunge(row)
        return row


def _code_fingerprint(version_id: int) -> str:
    with get_session() as session:
        return session.get(MethodVersion, version_id).code_fingerprint


def _sample_hash(name: str) -> str:
    with get_session() as session:
        return session.exec(select(Sample.content_hash)
                            .where(Sample.name == name)).one()


def _runs_payload(root: Path, monkeypatch) -> dict[str, dict]:
    """GET /api/wfc/runs from a canvas served on ``root``, by run id."""
    bind_provider(monkeypatch, None)
    with canvas_client(root, monkeypatch) as client:
        resp = client.get("/api/wfc/runs")
    assert resp.status_code == 200, resp.text
    return {r["id"]: r for r in resp.json()}


@workflow(purpose="A fresh run and its cache-hit audit row reach the History "
                  "payload with every cache-key input -- code fingerprint, env "
                  "name and fingerprint, params, each parent's cache key, each "
                  "sample read's content hash -- and the commit, wfc version and "
                  "pipeline document hash each recorded; neither is flagged as "
                  "keyed under an earlier rule, and no recipe number is sent",
          inputs="A pipeline sel -> a -> b where b also reads its sample; b "
                 "runs, HEAD moves, b is claimed again (a cache hit)",
          outputs="The two b rows of the runs payload")
def test_a_run_and_its_cache_hit_carry_their_key_inputs(git_project, monkeypatch):
    口 = Step(step_num=1, name="Run a, then b",
             purpose="b's key is over its code, its env, its params, a's key "
                     "and the sample's content hash")
    project = build_project(
        Scenario(nodes=[selector("sel"),
                        node("a", inputs=[wire("sel")]),
                        node("b", params={"k": 2},
                             inputs=[wire("sel", target_slot="raw"),
                                     wire("a", target_slot="table")])],
                 samples=["s1"]),
        root=git_project, monkeypatch=monkeypatch)
    root = project.root
    a = drive_target(project, ("a", "s1", "default"), monkeypatch=monkeypatch)
    b_target = ("b", "s1", "default")
    fresh_obs = drive_target(project, b_target, monkeypatch=monkeypatch)
    assert fresh_obs.runs[b_target].ending == "completed"
    fresh_head = _head(root)

    口 = Step(step_num=2, name="Move HEAD and claim b again",
             purpose="The commit is not in the key: a cache hit whose audit "
                     "row records its own commit")
    _commit_file(root, "notes.txt", "a later commit\n")
    audit_obs = drive_target(project, b_target, monkeypatch=monkeypatch)
    audit_head = _head(root)

    口 = Step(step_num=3, name="Read both rows from the runs payload",
             purpose="Expected values come from the rows production wrote, the "
                     "registered sample and the document on disk")
    a_row = _row(a.runs[("a", "s1", "default")].run_id)
    fresh_row = _row(fresh_obs.runs[b_target].run_id)
    audit_row = _row(audit_obs.runs[b_target].run_id)
    assert audit_row.cache_source_run_id == fresh_row.id
    try:
        payload = _runs_payload(root, monkeypatch)
    finally:
        reset_engine()
    fresh, audit = payload[str(fresh_row.id)], payload[str(audit_row.id)]

    口 = Step(step_num=4, name="Both carry the same key inputs",
             purpose="What the key was built over is the same on the audit row "
                     "as on the run it reused")
    document_hash = canonical_json_hash(
        json.loads(project.pipeline_json.read_text(encoding="utf-8")))
    installed = importlib.metadata.version("workflow-canvas")
    for run, row in ((fresh, fresh_row), (audit, audit_row)):
        assert run["codeFingerprint"] == _code_fingerprint(row.version_id)
        assert run["envName"] == project.scenario.env_name
        assert run["envFingerprint"] == fresh_row.env_fingerprint
        assert run["envFingerprint"]
        assert run["inputs"] == {"k": 2}
        assert run["parents"] == [{"slot": "table", "sourceRunId": str(a_row.id),
                                   "sourceSlot": "data",
                                   "cacheKey": a_row.cache_key}]
        assert run["sampleInputs"] == [{"slot": "raw", "sample": "s1",
                                        "contentHash": _sample_hash("s1")}]
        assert run["wfcVersion"] == installed
        assert run["pipelineDocHash"] == document_hash
        assert run["keyedUnderEarlierRule"] is False
        assert not RECIPE_KEYS & set(run)

    口 = Step(step_num=5, name="Each records its own commit",
             purpose="The audit row's commit is its claim's, not the source's")
    assert (fresh["gitCommit"], audit["gitCommit"]) == (fresh_head, audit_head)

    口 = Step(step_num=6, name="The hashes are the ones the key was built over",
             purpose="Identity's key over the payload's code fingerprint, params, "
                     "inputs hash, env fingerprint and method is the run's own "
                     "recorded key; the params hash is Identity's over the params")
    for run, row in ((fresh, fresh_row), (audit, audit_row)):
        assert run["paramsHash"] == canonical_json_hash({"k": 2})
        assert run["inputsHash"]
        assert build_cache_key(
            run["codeFingerprint"], run["inputs"], run["inputsHash"],
            run["envFingerprint"], f"{run['module']}.{run['method']}",
        ) == row.cache_key


def _make_pre_column_db(db_path: Path) -> None:
    """Write a database from before runs recorded what they ran with.

    LEGACY-SHAPE FIXTURE (declared deviation from building state through
    production paths): no production path writes a ``runs`` table without
    ``key_recipe`` and the recorded columns any more, so the old state is
    hand-rolled DDL. Run 1 is keyed, run 2 is keyless and reads run 1, and
    run 1's method version predates nothing the payload needs.
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        f"""
        CREATE TABLE modules (id INTEGER PRIMARY KEY, name TEXT, description TEXT);
        CREATE TABLE methods (
            id INTEGER PRIMARY KEY, module_id INTEGER, name TEXT, script_path TEXT
        );
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            method_id INTEGER NOT NULL,
            params TEXT,
            sample TEXT,
            status TEXT,
            pipeline_id TEXT,
            started_at TEXT,
            finished_at TEXT,
            cache_key VARCHAR(64),
            env_fingerprint VARCHAR(32)
        );
        CREATE TABLE run_inputs (
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            source_run_id INTEGER,
            input_name TEXT,
            artifact_path TEXT
        );
        INSERT INTO modules (id, name) VALUES (1, 'mod');
        INSERT INTO methods (id, module_id, name) VALUES (1, 1, 'method_a');
        INSERT INTO runs (id, method_id, sample, status, cache_key)
            VALUES (1, 1, 'S1', 'completed', '{"a" * 64}');
        INSERT INTO runs (id, method_id, sample, status)
            VALUES (2, 1, 'S1', 'completed');
        INSERT INTO run_inputs (id, run_id, source_run_id, input_name)
            VALUES (1, 2, 1, 'table');
        """
    )
    conn.commit()
    conn.close()


@workflow(purpose="A database from before runs recorded what they ran with "
                  "opens in History: its keyed run is flagged as keyed under an "
                  "earlier rule, its keyless run is not, every value it never "
                  "recorded reads null, and no recipe number is sent",
          inputs="A pre-column database in place of a project's own, upgraded "
                 "by production's engine bootstrap",
          outputs="The two runs of the runs payload")
def test_an_old_run_reads_not_recorded_and_keyed_under_an_earlier_rule(
        tmp_project, monkeypatch):
    口 = Step(step_num=1, name="Put an old database in the project",
             purpose="The bootstrap's backfill reads its keyed run as recipe 1")
    reset_engine()
    db_path = layout.db_path(tmp_project)
    db_path.unlink()
    _make_pre_column_db(db_path)

    口 = Step(step_num=2, name="Read the runs payload",
             purpose="The provider reads each table once and sends what is "
                     "there; nothing falls back to the method version's env")
    try:
        payload = _runs_payload(tmp_project, monkeypatch)
    finally:
        reset_engine()
    keyed, keyless = payload["1"], payload["2"]

    口 = Step(step_num=3, name="Old rows read null; only the keyed one is flagged",
             purpose="A keyless row has no key to have been built under any rule")
    for run in (keyed, keyless):
        for field in ("codeFingerprint", "envName", "envFingerprint",
                      "gitCommit", "wfcVersion", "pipelineDocHash"):
            assert run[field] is None, (run["id"], field)
        assert not RECIPE_KEYS & set(run)
    assert keyed["keyedUnderEarlierRule"] is True
    assert keyless["keyedUnderEarlierRule"] is False
    assert keyless["parents"] == [{"slot": "table", "sourceRunId": "1",
                                   "sourceSlot": None, "cacheKey": "a" * 64}]

    口 = Step(step_num=4, name="Neither has an inputs hash",
             purpose="A key under an earlier rule was not built over today's "
                     "fingerprint, and a keyless run has no key at all")
    assert keyed["inputsHash"] is None
    assert keyless["inputsHash"] is None


@workflow(purpose="A keyed run whose parent entry named no output slot reaches "
                  "History with no inputs hash: the slot its key was built over "
                  "was resolved by Execution at the claim and is not recorded, "
                  "and the provider never resolves it again from today's rows",
          inputs="A pipeline sel -> a -> b where b's wire from a names no output",
          outputs="a's and b's rows of the runs payload")
def test_a_parent_naming_no_output_leaves_the_inputs_hash_null(git_project, monkeypatch):
    口 = Step(step_num=1, name="Run a, then b over an unnamed output of a",
             purpose="b's key resolves a's one output at the claim; the input "
                     "row records no source slot")
    project = build_project(
        Scenario(nodes=[selector("sel"),
                        node("a", inputs=[wire("sel")]),
                        node("b", inputs=[wire("a", source_slot=None)])],
                 samples=["s1"]),
        root=git_project, monkeypatch=monkeypatch)
    a = drive_target(project, ("a", "s1", "default"), monkeypatch=monkeypatch)
    b = drive_target(project, ("b", "s1", "default"), monkeypatch=monkeypatch)
    a_row = _row(a.runs[("a", "s1", "default")].run_id)
    b_row = _row(b.runs[("b", "s1", "default")].run_id)

    口 = Step(step_num=2, name="Read both rows from the runs payload",
             purpose="b is keyed under today's rule, so only the unnamed slot "
                     "can leave its inputs hash null")
    try:
        payload = _runs_payload(project.root, monkeypatch)
    finally:
        reset_engine()
    a_run, b_run = payload[str(a_row.id)], payload[str(b_row.id)]
    assert b_row.cache_key and b_run["keyedUnderEarlierRule"] is False
    assert b_run["parents"][0]["sourceSlot"] is None
    assert b_run["inputsHash"] is None
    # a read its sample with a recorded content hash: its inputs hash is the
    # one its key was built over.
    assert build_cache_key(
        a_run["codeFingerprint"], a_run["inputs"], a_run["inputsHash"],
        a_run["envFingerprint"], f"{a_run['module']}.{a_run['method']}",
    ) == a_row.cache_key


def test_recorded_inputs_hash_is_null_unless_every_recorded_input_is_complete():
    """The inputs hash is Identity's fingerprint over the recorded identities, or null.

    Null for a parent with no source slot, a sample read with no content
    hash, a keyless run and a run keyed under an earlier rule.
    """
    parent = {"slot": "table", "sourceRunId": "12", "sourceSlot": "data",
              "cacheKey": "k" * 64}
    legacy_parent = {"slot": "ref", "sourceRunId": "7", "sourceSlot": "out",
                     "cacheKey": None}
    read = {"slot": "raw", "sample": "s1", "contentHash": "c" * 32}
    expected = build_input_fingerprint(
        [UpstreamRunIdentity("table", "data", 12, "k" * 64),
         UpstreamRunIdentity("ref", "out", 7, None)],
        [SampleIdentity("raw", "c" * 32)])

    assert recorded_inputs_hash("key", False, [parent, legacy_parent], [read]) == expected
    assert recorded_inputs_hash("key", False, [{**parent, "sourceSlot": None}], [read]) is None
    assert recorded_inputs_hash("key", False, [parent], [{**read, "contentHash": None}]) is None
    assert recorded_inputs_hash(None, False, [parent], [read]) is None
    assert recorded_inputs_hash("key", True, [parent], [read]) is None
    # An input the key was built over is no longer readable.
    assert recorded_inputs_hash("key", False, [parent], [read], inputs_complete=False) is None


@workflow(purpose="A keyed run one of whose inputs was removed reaches History "
                  "with no inputs hash: the inputs that remain would hash to a "
                  "fingerprint its key was never built over",
          inputs="A demo method's run feeding a user step beside a plain "
                 "parent and a sample read; then wfc demo --remove",
          outputs="The user step's row of the runs payload")
def test_a_run_whose_input_was_removed_has_no_inputs_hash(git_project, monkeypatch):
    口 = Step(step_num=1, name="Run the steps, then remove the demo",
             purpose="The teardown deletes the demo method's run and nulls the "
                     "source of the user step's input row that recorded it")
    obs = run_scenario(Scenario(
        nodes=[
            selector("sel"),
            node("segment", method="__demo__segment", module="__demo__",
                 inputs=[wire("sel")]),
            node("norm", inputs=[wire("sel")]),
            node("quantify", inputs=[wire("sel", target_slot="raw"),
                                     wire("segment", target_slot="mask"),
                                     wire("norm", target_slot="table")]),
        ],
        samples=["s1"], modules={"__demo__": ModuleSpec(demo_owned=True)},
    ), root=git_project, monkeypatch=monkeypatch)
    quantify = obs.runs[("quantify", "s1", "default")].run_id
    assert remove_demo(git_project, assume_yes=True) == 0

    口 = Step(step_num=2, name="Read the step's row from the runs payload",
             purpose="Its key, parent and sample read are as recorded; only "
                     "the removed input is gone")
    try:
        with get_session() as session:
            assert any(r.source_run_id is None and r.sample_name is None
                       for r in session.exec(select(RunInput).where(
                           RunInput.run_id == quantify)))
        payload = _runs_payload(git_project, monkeypatch)
    finally:
        reset_engine()
    run = payload[str(quantify)]
    assert _row(quantify).cache_key and run["keyedUnderEarlierRule"] is False
    assert run["parents"][0]["sourceSlot"] and run["sampleInputs"][0]["contentHash"]
    assert run["inputsHash"] is None


def test_recorded_params_hash_ignores_key_order_and_is_null_when_unhashable():
    """The params hash is Identity's canonical JSON hash of the params."""
    assert recorded_params_hash({"x": 1, "y": [1, 2]}) == canonical_json_hash({"x": 1, "y": [1, 2]})
    assert recorded_params_hash({"y": [1, 2], "x": 1}) == recorded_params_hash({"x": 1, "y": [1, 2]})
    assert recorded_params_hash({"x": 2, "y": [1, 2]}) != recorded_params_hash({"x": 1, "y": [1, 2]})
    # No recorded params are the empty params the key was built over.
    assert recorded_params_hash(None) == canonical_json_hash({})
    assert recorded_params_hash({"x": float("nan")}) is None


@workflow(purpose="Two runs whose params are equal but written in another key "
                  "order carry one params hash in History, and a run whose "
                  "params differ carries another",
          inputs="Three methods over one sample: x,y / y,x in the other order "
                 "/ a different x",
          outputs="The three rows of the runs payload")
def test_equal_params_in_another_key_order_share_a_params_hash(git_project, monkeypatch):
    口 = Step(step_num=1, name="Run three steps with the params written three ways",
             purpose="The params reach the run rows in the order written")
    project = build_project(
        Scenario(nodes=[selector("sel"),
                        node("p1", inputs=[wire("sel")], params={"x": 1, "y": [1, 2]}),
                        node("p2", inputs=[wire("sel")], params={"y": [1, 2], "x": 1}),
                        node("p3", inputs=[wire("sel")], params={"x": 2, "y": [1, 2]})],
                 samples=["s1"]),
        root=git_project, monkeypatch=monkeypatch)
    ids = {}
    for name in ("p1", "p2", "p3"):
        obs = drive_target(project, (name, "s1", "default"), monkeypatch=monkeypatch)
        ids[name] = str(obs.runs[(name, "s1", "default")].run_id)

    口 = Step(step_num=2, name="Compare the params hashes",
             purpose="The order differs on the wire, the hash does not")
    try:
        payload = _runs_payload(project.root, monkeypatch)
    finally:
        reset_engine()
    p1, p2, p3 = (payload[ids[n]] for n in ("p1", "p2", "p3"))
    assert list(p1["inputs"]) == ["x", "y"] and list(p2["inputs"]) == ["y", "x"]
    assert p1["paramsHash"] == p2["paramsHash"] == canonical_json_hash({"x": 1, "y": [1, 2]})
    assert p3["paramsHash"] != p1["paramsHash"]


@workflow(purpose="History names each param's type as the run's method version "
                  "declares it",
          inputs="A method declaring int, float, list and any params",
          outputs="The run's paramTypes in the runs payload")
def test_param_types_are_the_declared_ones(git_project, monkeypatch):
    口 = Step(step_num=1, name="Register and run a method with typed params",
             purpose="Registration stores the version's contract; the run "
                     "records its params")
    project = build_project(
        Scenario(nodes=[selector("sel"),
                        node("t", inputs=[wire("sel")],
                             params={"n": 3, "f": 2.0, "l": [1], "a": "x"},
                             param_types={"n": "int", "f": "float", "l": "list"})],
                 samples=["s1"]),
        root=git_project, monkeypatch=monkeypatch)
    obs = drive_target(project, ("t", "s1", "default"), monkeypatch=monkeypatch)
    run_id = str(obs.runs[("t", "s1", "default")].run_id)

    口 = Step(step_num=2, name="Read the run's declared types",
             purpose="Read from the version's stored contract, never off the "
                     "values: 2.0 is declared float")
    try:
        payload = _runs_payload(project.root, monkeypatch)
    finally:
        reset_engine()
    run = payload[run_id]
    assert set(run["inputs"]) == {"n", "f", "l", "a"}
    assert run["paramTypes"] == {"n": "int", "f": "float", "l": "list", "a": "any"}
