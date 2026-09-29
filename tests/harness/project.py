"""Build a project on disk from a scenario declaration.

Everything here goes through production code or the plain (pytest-free)
helpers already in ``tests/fixtures/conftest.py`` — ``register_test_method``
(init + register_module + register_method), ``write_env_record`` (the
production env-manifest serialization), and ``build_pipeline_json`` (the
enrichment the canvas export produces). Nothing hand-builds a project,
a pipeline document, or a method.

The container-backed fixtures in that module (``pipeline_factory``,
``register_fixture_methods``) are deliberately NOT used: they depend on the
session-scoped ``fixture_container_image`` fixture, which is a real
``docker build``, and pulling that chain in would silently turn every
stub-rung test into an integration test.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from axiom_annotations import AutoStep, Step, task

from tests.fixtures.conftest import (
    build_pipeline_json,
    project_archive_dir,
    register_test_method,
    sample_source_dir,
    write_env_record,
)

from .scenario import NodeSpec, Scenario


def _layout():
    """Return production's Layout module, the one owner of every derived path.

    The harness lays down the tree production reads (samples, methods, the
    database URL) and reads production's completion signals back from it
    (sentinels, outcome sidecars, the frozen pipeline document). Reaching
    Layout through one accessor mirrors ``drivers._expansion``: the harness
    is a consumer of the catalog, not a copy of it, so a catalog change
    reaches every harness site through this function and no test.

    Returns:
        The ``wfc.layout`` module.
    """
    from wfc import layout

    return layout

#: Placeholder image digest for the stub rung. Registration and dispatch
#: validate the container ref's SHAPE only, so no image is pulled or built.
STUB_DIGEST = "c" * 64


@dataclass
class Project:
    """A built project on disk plus the identities a run needs.

    Attributes:
        root: Project root directory.
        scenario: The declaration this project was built from.
        pipeline_json: Path to the enriched pipeline document.
        pipeline_id: Pipeline execution id.
        env_name: The container env every node binds to.
        method_scripts: Method name -> the generated script path.
        method_ids: Method name -> its ``methods`` table row id, read back
            after registration. A ``Run`` row names its method by id, so a
            test asserting which node a row belongs to needs the mapping
            production wrote rather than one it invented.
        sample_ids: Sample name -> its ``samples`` table row id. Carries
            every declared sample, including ones the scenario declared
            missing or empty: registration and staging are separate acts
            in production, so an absent data file is a workspace state
            and never a reason for the row to be missing.
    """

    root: Path
    scenario: Scenario
    pipeline_json: Path
    pipeline_id: str
    env_name: str
    method_scripts: dict[str, Path] = field(default_factory=dict)
    method_ids: dict[str, int] = field(default_factory=dict)
    sample_ids: dict[str, int] = field(default_factory=dict)

    @property
    def runs_dir(self) -> Path:
        """The project's ``.runs`` directory."""
        return _layout().artifact_store(self.root)

    def sample_file(self, sample: str) -> Path:
        """Return the data file this project registered for one sample.

        The builder writes it; a test that asserts on the identity of the
        file a slot resolved to reads it back from here rather than
        re-composing the path.

        Args:
            sample: Sample identifier.

        Returns:
            Path to the sample's registered data file.
        """
        return _layout().sample_dir(self.root, sample) / f"{sample}.csv"

    def sentinel_dir(self, node_id: str, sample: str, variant: str) -> Path:
        """Return the sentinel directory for one target.

        Args:
            node_id: Node identity.
            sample: Sample identifier.
            variant: Variant name.

        Returns:
            The directory holding ``.complete`` and ``run_id.txt``.
        """
        return _layout().run_sentinel_dir(self.root, self.pipeline_id, node_id,
                                          sample, variant)


@task(purpose="Construct the scenario's project on disk and execute nothing — "
              "the third invocation door, where production drives itself",
      inputs="A scenario declaration and a target directory",
      outputs="The project handle, with cwd and WFC_* env pinned to it")
def build_project(scn: Scenario, *, root: Path, monkeypatch) -> Project:
    """Construct the scenario's project on disk. Executes nothing.

    Args:
        scn: The scenario declaration.
        root: Directory to build into. Initialised as a git repo when it
            is not one already.
        monkeypatch: An active ``pytest.MonkeyPatch`` used to pin
            ``WFC_PROJECT_ROOT`` / ``DATABASE_URL`` and the working
            directory for the duration of the test.

    Returns:
        The :class:`Project` handle.
    """
    from wfc.persistence import reset_engine
    from wfc.init import init_project
    from wfc.storage.restore import restore_sample

    口 = Step(step_num=1, name="Resolve the root and ensure it is a git repo",
             purpose="Every later phase resolves paths against an absolute "
                     "root, and the claim phase's clean-tree check needs a "
                     "repo to read",
             outputs="An absolute project root that is a git repository")
    root = Path(root).resolve()
    _ensure_git_repo(root)

    口 = Step(step_num=2,
             name="Pin the working directory and the WFC_* environment",
             purpose="Production reads the project root and the database URL "
                     "from the environment, so pinning them here is what "
                     "makes every subsequent production call land in this "
                     "scenario's project rather than the developer's",
             outputs="cwd, WFC_PROJECT_ROOT and DATABASE_URL bound to this "
                     "root for the duration of the test")
    monkeypatch.chdir(root)
    monkeypatch.setenv(_layout().ROOT_ENV_VAR, str(root))
    monkeypatch.setenv("DATABASE_URL", _layout().database_url(root))

    口 = Step(step_num=3,
             name="Initialize the project and write the env manifest",
             purpose="init_project lays down the .wfc scaffolding and the "
                     "database; write_env_record names the scenario's env "
                     "and pins its image digest",
             outputs=".wfc/ scaffolding, the project database, and "
                     ".wfc/envs.json naming the scenario's env",
             critical="The env manifest must exist BEFORE the first "
                      "register_method call, because registration resolves "
                      "the method's env against it. reset_engine() then "
                      "drops any engine still bound to a previous test's "
                      "DATABASE_URL")
    # An explicit out-of-tree archive: without one init_project resolves
    # ~/.wfc/archives/<project> and init_dvc pre-creates it, so a scenario
    # run would leave a directory in the developer's home.
    init_project(root, archive=str(project_archive_dir(root)), assume_yes=True)
    write_env_record(root, scn.env_name, digest=scn.image_digest or STUB_DIGEST,
                     backend=scn.env_backend,
                     legacy_no_python=scn.env_legacy_no_python)
    reset_engine()

    口 = Step(step_num=4, name="Write each declared method's script",
             purpose="Turn every method node's declared behavior into an "
                     "executable script on disk",
             outputs="methods/<name>/ populated, and method name -> script "
                     "path for the project handle")
    method_scripts: dict[str, Path] = {}
    by_method: dict[str, list[NodeSpec]] = {}
    for spec in scn.method_nodes():
        by_method.setdefault(spec.method_name, []).append(spec)
    for specs in by_method.values():
        口 = AutoStep(step_num=4.1,
                      name="Write one method, for every node that names it")
        method_scripts[specs[0].method_name] = _write_method(root, scn, specs)

    口 = Step(step_num=5,
             name="Register the methods through production's own path",
             purpose="register_test_method runs production's init + "
                     "register_module + register_method, so the rows the "
                     "claim phase later looks up are ones production wrote",
             outputs="Module and method rows in the project database",
             critical="A second pass, after step 4: every declared script is "
                      "on disk before any registration runs")
    for spec in scn.method_nodes():
        module_spec = scn.modules.get(spec.module)
        register_test_method(
            project_dir=root,
            module_name=spec.module,
            method_dir=_layout().method_dir(root, spec.method_name),
            method_name=spec.method_name,
            module_contracts=(list(module_spec.contracts) if module_spec else None),
            module_description=(module_spec.description if module_spec else None),
            allow_reserved=bool(module_spec and module_spec.demo_owned),
        )

    口 = Step(step_num=6, name="Stage each declared sample's source file",
             purpose="Write every declared sample's content to a source file "
                     "OUTSIDE the project tree — the user's own copy, which "
                     "registration reads, hashes and caches",
             outputs="<project>-sources/<sample>/<sample>.csv per declared "
                     "sample",
             critical="Outside data/, and outside it deliberately. "
                      "register_sample caches the source's bytes and records "
                      "data/samples/<s>/<s>.csv as where restore_sample will "
                      "later put them, so a source staged at its own "
                      "registered_path makes every restore the "
                      "dest-already-valid skip — a state no user can produce. "
                      "Content comes from Scenario.content_for, which defaults "
                      "to bytes derived from the sample NAME: identical content "
                      "across samples would give every content-addressed row "
                      "the same hash part, and a same-size membership swap "
                      "between two bundles would compute one cache key. "
                      "write_bytes, not write_text — LF->CRLF translation on "
                      "Windows would make the staged file's md5 differ from "
                      "the content the scenario declared")
    for sample in scn.samples:
        source_file = _sample_source_file(root, sample)
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_bytes(scn.content_for(sample).encode("utf-8"))

    口 = Step(step_num=7, name="Register the samples, build the document",
             purpose="Read back the method row ids registration assigned and "
                     "register one sample per declared sample through "
                     "production, then hand the scenario's nodes, links, "
                     "samples and param sets to build_pipeline_json — the same "
                     "enrichment the canvas export produces",
             outputs="samples rows for every declared sample, and "
                     "pipeline.json, the document the engine and the "
                     "expansion step both load",
             critical="The sample rows are what production guarantees before "
                      "any root step runs: restore_sample exits non-zero on "
                      "a name with no row. Without them pre_run resolves an "
                      "empty sample_ids and every scenario fingerprints its "
                      "root against an empty parts list — the harness would "
                      "be blind to the sample axis of the cache key")
    method_ids = _read_method_ids(scn)
    sample_ids = _record_samples(root, scn)

    口 = AutoStep(step_num=7.1, name="Translate the node specs")
    nodes = _pipeline_nodes(scn)

    口 = AutoStep(step_num=7.2, name="Translate the declared wiring")
    links = _pipeline_links(scn)

    pipeline_json = build_pipeline_json(
        root,
        scn.name,
        nodes,
        links,
        list(scn.samples),
        _param_sets(scn),
        default_module=scn.method_nodes()[0].module if scn.method_nodes() else "harness_mod",
    )

    口 = Step(step_num=8, name="Materialize the sample tree from the cache",
             purpose="Run production restore_sample for every sample the "
                     "scenario did not declare missing or empty, honoring the "
                     "missing_samples, empty_samples and sample_ready_sentinel "
                     "declarations so a scenario can still state an absent or "
                     "empty input",
             outputs="data/samples/<sample>/<sample>.csv for every staged "
                     "sample and, when declared, a .sample_ready sentinel",
             critical="restore_sample is the ONLY writer under data/samples/ — "
                      "the harness stages its sources elsewhere and never "
                      "copies one in. The readiness sentinel a restore leaves "
                      "is removed again unless the scenario declares it: "
                      ".sample_ready is the Snakemake restore rule's OUTPUT, "
                      "so a pre-existing one makes the engine rung skip the "
                      "rule — and that rule is what proves the sample's bytes "
                      "actually reached the DVC cache")
    for sample in scn.samples:
        if sample in scn.missing_samples:
            continue
        _layout().sample_dir(root, sample).mkdir(parents=True, exist_ok=True)
        if sample not in scn.empty_samples:
            口 = AutoStep(step_num=8.1, name="Restore one sample")
            restore_sample(sample, project_root=root)
        sentinel = _layout().sample_ready_sentinel(root, sample)
        if scn.sample_ready_sentinel:
            sentinel.write_text("")
        else:
            sentinel.unlink(missing_ok=True)

    口 = Step(step_num=9,
             name="Commit the tree, then dirty it if the scenario declares it",
             purpose="The claim phase refuses a dirty working tree, so a "
                     "scenario that is not testing that refusal must start "
                     "clean",
             outputs="A committed tree — plus one uncommitted edit to a "
                     "TRACKED file when the scenario declares dirty_repo",
             critical="The clean-tree check looks at TRACKED files only, so a "
                      "dirty scenario commits the file FIRST and then "
                      "modifies it. An untracked file would leave the tree "
                      "'clean' and the scenario would silently not be dirty")
    if scn.dirty_repo:
        (root / "_dirty.txt").write_text("committed\n")
    commit_everything(root)
    if scn.dirty_repo:
        (root / "_dirty.txt").write_text("uncommitted\n")

    return Project(
        root=root,
        scenario=scn,
        pipeline_json=pipeline_json,
        pipeline_id=scn.pipeline_id,
        env_name=scn.env_name,
        method_scripts=method_scripts,
        method_ids=method_ids,
        sample_ids=sample_ids,
    )


def _sample_source_file(root: Path, sample: str) -> Path:
    """Return where the harness stages one sample's source file.

    Beside the project, never inside it: registration reads this file and
    records ``data/samples/<sample>/<sample>.csv`` as where a restore will
    later materialize it. The file NAME matters — ``register_sample``
    derives ``registered_path`` from the source's own name, so calling it
    ``<sample>.csv`` is what puts the restored file exactly where
    :meth:`Project.sample_file` and the collapsed fan-in resolver look.

    Args:
        root: Project root.
        sample: Sample identifier.

    Returns:
        The source path. Its parent is not created here.
    """
    return sample_source_dir(root) / sample / f"{sample}.csv"


def _read_method_ids(scn: Scenario) -> dict[str, int]:
    """Read back the row id registration assigned to each declared method.

    Args:
        scn: The scenario declaration.

    Returns:
        Method name -> its ``methods`` table row id.
    """
    from sqlmodel import select

    from wfc.persistence import get_session, Method

    wanted = {spec.method_name for spec in scn.method_nodes()}
    with get_session() as session:
        rows = session.exec(select(Method)).all()
    return {m.name: int(m.id) for m in rows if m.name in wanted and m.id is not None}


@task(purpose="Record one samples row per declared sample by running production "
              "register_sample over the staged source, so a scenario's root "
              "fingerprints against rows the product itself can produce",
      inputs="The project root and the scenario declaration",
      outputs="Sample name -> the row id registration wrote",
      critical="Registers the DECLARED content from its source OUTSIDE the "
               "project, never the data/samples/ copy. register_sample caches "
               "the source's bytes and records where restore_sample will later "
               "materialize them, so registration and staging stay separate "
               "acts and a sample the scenario declared missing or empty under "
               "data/samples/ still gets a row. A missing SOURCE is a harness "
               "bug and raises. Nothing here constructs a Sample row: the row's "
               "shape, its push status and its cached bytes are all production's")
def _record_samples(root: Path, scn: Scenario) -> dict[str, int]:
    """Register one sample per declared sample, through production.

    Every row is written by ``wfc.registration.register_sample`` — the
    same call ``wfc register-sample`` makes. A row the harness builds by
    hand can describe a state the product cannot produce: no bytes in the
    DVC cache for its content hash (so the engine rung's ``restore_sample``
    rule fails), and ``push_status`` left at its ``deferred`` default. Both
    were real: the engine rung failed on the missing cache object.

    The registration *source* is the staged declared content under
    :func:`sample_source_dir`, beside the project and outside its ``data/``
    tree. ``register_sample`` copies nothing into ``data/samples/``; it
    records there as ``registered_path`` where the ``restore_sample`` rule
    will put the file later. So ``registered_path != source_path`` and a
    restore is a real copy rather than the dest-already-valid skip.

    A sample the scenario declared missing or empty still gets a row —
    row-present/file-absent under ``data/samples/`` is the normal
    pre-restore state, and those declarations are about that staged path,
    not about the source, which step 6 writes for every declared sample. A
    missing source is therefore a harness inconsistency and raises.

    Re-registration on rebuild: ``build_project`` re-runs against an
    existing project whenever a scenario needs a second execution over the
    same database, and ``register_sample`` refuses a duplicate name. A row
    whose ``content_hash`` still matches the declared content is left
    alone; one that does not is deleted and re-registered, so a rebuild
    that changes content moves the key instead of silently keeping the
    first build's hash.

    Args:
        root: Project root.
        scn: The scenario declaration.

    Returns:
        Sample name -> the row id it was registered under.

    Raises:
        FileNotFoundError: When a declared sample has no staged source
            file.
    """
    import hashlib

    from sqlmodel import select

    from wfc.persistence import get_session, Sample
    from wfc.registration import register_sample

    ids: dict[str, int] = {}
    for sample in scn.samples:
        source_file = _sample_source_file(root, sample)
        if not source_file.exists():
            raise FileNotFoundError(
                f"sample {sample!r} has no staged source file at "
                f"{source_file} — the harness staged the tree "
                f"inconsistently with its own declaration"
            )
        declared_hash = hashlib.md5(
            scn.content_for(sample).encode("utf-8")
        ).hexdigest()

        with get_session() as session:
            existing = session.exec(
                select(Sample).where(Sample.name == sample)
            ).first()
            if existing is not None and existing.content_hash == declared_hash:
                ids[sample] = int(existing.id)
                continue
            if existing is not None:
                session.delete(existing)
                session.commit()

        register_sample(sample, source_file, project_root=root)

        with get_session() as session:
            row = session.exec(
                select(Sample).where(Sample.name == sample)
            ).first()
            ids[sample] = int(row.id)
    return ids


# =============================================================================
# Pipeline document
# =============================================================================

@task(purpose="Translate the scenario's node specs into the node dicts "
              "build_pipeline_json expects — where a declaration becomes a "
              "document",
      inputs="The scenario declaration",
      outputs="Node dicts: method nodes carrying method/module/env/params, "
              "system nodes carrying their type-specific keys",
      critical="A fan_mode='in' selector that names no samples is a document "
               "validate_workflow rejects, so one declaring none inherits the "
               "scenario's full sample list — its own sample list IS the "
               "bundle the loader reads to set the consumer's "
               "collapsed_samples")
def _pipeline_nodes(scn: Scenario) -> list[dict]:
    """Translate node specs into ``build_pipeline_json`` node dicts."""
    out: list[dict] = []
    for spec in scn.nodes:
        if spec.type != "method":
            entry: dict = {"id": spec.id, "type": spec.type,
                           "params": dict(spec.params)}
            if spec.samples is not None:
                entry["samples"] = list(spec.samples)
            elif spec.fan_mode == "in":
                # A fan-in selector's own sample list IS the bundle. The
                # loader reads it to set the consumer's collapsed_samples,
                # and the generator turns it into one --collapsed-sample
                # flag per member; a fan_mode="in" selector naming no
                # samples is a document validate_workflow rejects.
                entry["samples"] = list(scn.samples)
            if spec.fan_mode is not None:
                entry["fan_mode"] = spec.fan_mode
            if spec.run_id is not None:
                entry["run_id"] = spec.run_id
            out.append(entry)
            continue
        entry = {
            "id": spec.id,
            "method": spec.method_name,
            "module": spec.module,
            "params": dict(spec.params),
            # The document's env is what dispatch looks up in the manifest;
            # method.yaml's env is what registration validated. They agree
            # unless the node declares a document_env, which is how a node
            # reaches dispatch naming an env nothing built.
            "env": spec.document_env or spec.env or scn.env_name,
        }
        if spec.label is not None:
            entry["label"] = spec.label
        if spec.output_files:
            entry["slot_outputs"] = dict(spec.output_files)
        if spec.script_name is not None:
            entry["script"] = f"methods/{spec.method_name}/{spec.script_name}"

        out.append(entry)
    return out


@task(purpose="Translate the nodes' declared wiring into pipeline links",
      inputs="The scenario declaration",
      outputs="Link dicts carrying source, target and both slot names — the "
              "edges the loader turns into step dependencies")
def _pipeline_links(scn: Scenario) -> list[dict]:
    """Translate the nodes' wiring into pipeline links."""
    links: list[dict] = []
    for spec in scn.nodes:
        for w in spec.inputs:
            link: dict = {"source": w.source, "target": spec.id,
                          "target_slot": w.target_slot}
            if w.source_slot is not None:
                link["source_slot"] = w.source_slot
            links.append(link)
    return links


def _param_sets(scn: Scenario) -> dict | None:
    """Build the ``param_sets`` map from the scenario's variant sweep."""
    if not scn.variants:
        return None
    return {
        spec.id: {v: dict(params) for v, params in scn.variants.items()}
        for spec in scn.method_nodes()
    }


# =============================================================================
# Method generation
# =============================================================================

@task(purpose="Write one node's method.yaml contract and its generated stub "
              "script — the two artifacts a declared method becomes on disk",
      inputs="The project root, the scenario, and the node being written",
      outputs="methods/<name>/method.yaml and methods/<name>/<name>.py",
      critical="The script is the SAME artifact on both fidelity rungs: the "
               "stub rung runs it as a local subprocess, the engine rung runs "
               "it inside the fixture container. It is stdlib-only and reads "
               "the WFC_* env the dispatch phase built")
def _write_method(root: Path, scn: Scenario, specs: list[NodeSpec]) -> Path:
    """Write one method's ``method.yaml`` and generated stub script.

    One method serves every node that names it: the contract is written
    once, from the first node, and the script carries a per-node table so
    each node's declared behavior is what its process plays. The script is
    the same artifact on both fidelity rungs: the stub rung runs it as a
    local subprocess, the engine rung runs it inside the fixture
    container. It is stdlib-only and reads the ``WFC_*`` env the dispatch
    phase built.

    Args:
        root: Project root.
        scn: The scenario (supplies the default env name).
        specs: Every node that names this method, in declaration order.

    Returns:
        Path to the generated script.

    Raises:
        ValueError: When a node declares both override maps, or when two
            nodes sharing the method declare different contracts -- a
            method has one contract, so a difference would be silently
            resolved by whichever node came first.
    """
    spec = specs[0]
    for other in specs:
        if other.behavior_by_sample and other.behavior_by_variant:
            raise ValueError(
                f"node {other.id!r} declares both behavior_by_sample and "
                f"behavior_by_variant; declare one axis per node"
            )
        if _contract_shape(other) != _contract_shape(spec):
            raise ValueError(
                f"nodes {spec.id!r} and {other.id!r} share method "
                f"{spec.method_name!r} but declare different contracts; a "
                f"method has one contract, so declare the same slots, "
                f"files, columns, env, executor and gpus on both"
            )
    method_dir = _layout().method_dir(root, spec.method_name)
    method_dir.mkdir(parents=True, exist_ok=True)

    口 = Step(step_num=1, name="Write the method.yaml contract",
             purpose="Declare the input and output slots, the executor and "
                     "the env — what registration reads and what the "
                     "materialize phase resolves each slot against",
             outputs="methods/<name>/method.yaml",
             critical="env is written as the bare name of a registered env, "
                      "the only form a method.yaml accepts, and the written "
                      "file is read back through the Contracts parser and its "
                      "env-name rule, so an "
                      "off-grammar declaration fails here, at project build, "
                      "rather than at dispatch. A slotless node still "
                      "declares a default 'data' slot, because a method.yaml "
                      "with no inputs block is not a document production "
                      "accepts. A slot's declared columns block is dumped "
                      "verbatim beneath it")
    input_slots = ([(w.target_slot, True) for w in _unique_target_slots(spec)]
                   or [("data", False)])
    inputs_yaml = "".join(
        _slot_yaml(slot, ".csv", required, spec.input_columns.get(slot))
        for slot, required in input_slots
    )
    outputs_yaml = "".join(
        _slot_yaml(slot, slot_type, True, spec.output_columns.get(slot))
        for slot, slot_type in spec.outputs.items()
    ) or "  data:\n    type: .csv\n    required: true\n"

    env_spec = spec.env or scn.env_name
    (method_dir / "method.yaml").write_text(
        f"inputs:\n{inputs_yaml}"
        f"outputs:\n{outputs_yaml}"
        "params: {}\n"
        f"executor: {spec.executor}\n"
        f"env: {env_spec}\n"
        f"gpus: {'true' if spec.gpus else 'false'}\n"
    )
    # Round-trip through the owner: the declaration just written is read
    # back by the Contracts parser and its env checked against the
    # env-name rule, so a scenario that authors an off-grammar method.yaml
    # fails here with the unit's own message rather than at dispatch.
    from wfc.contracts import parse_method_yaml, validate_env_name
    validate_env_name(parse_method_yaml(method_dir)["env"])

    口 = Step(step_num=2, name="Write the generated stub script",
             purpose="Turn the node's declared behavior — its exit code, its "
                     "outputs, whatever it is meant to do or fail to do — "
                     "into an executable the dispatch phase can launch",
             outputs="methods/<name>/<name>.py, returned as the script path")
    script = method_dir / f"{spec.method_name}.py"
    script.write_text(_stub_script(specs))
    return script


def _contract_shape(spec: NodeSpec) -> tuple:
    """Return everything ``method.yaml`` is written from, for one node.

    Args:
        spec: The node.

    Returns:
        A comparable tuple of the node's contract-bearing declarations.
    """
    return (
        sorted(w.target_slot for w in _unique_target_slots(spec)),
        dict(spec.outputs), dict(spec.output_files),
        dict(spec.input_columns), dict(spec.output_columns),
        spec.env, spec.executor, spec.gpus,
    )


def _slot_yaml(slot: str, slot_type: str, required: bool,
               columns: dict | None) -> str:
    """Render one slot's declaration for the ``inputs:`` or ``outputs:`` mapping.

    Args:
        slot: The slot name.
        slot_type: The declared type string (``".csv"``, ``"directory"``).
        required: Whether the slot is declared ``required``.
        columns: The slot's ``columns`` block, or ``None`` for none. Dumped
            verbatim beneath the slot, indented into place.

    Returns:
        The slot's ``method.yaml`` lines.
    """
    text = (f"  {slot}:\n    type: {slot_type}\n"
            f"    required: {'true' if required else 'false'}\n")
    if columns:
        dumped = yaml.safe_dump(dict(columns), sort_keys=False)
        text += "    columns:\n" + "".join(
            f"      {line}\n" for line in dumped.splitlines()
        )
    return text


def _unique_target_slots(spec: NodeSpec) -> list:
    """Return the node's incoming wires, one per distinct target slot."""
    seen: set[str] = set()
    keep = []
    for w in spec.inputs:
        if w.target_slot in seen:
            continue
        seen.add(w.target_slot)
        keep.append(w)
    return keep


_STUB_TEMPLATE = '''\
"""Generated harness method. Stdlib only; reads the WFC_* env."""
import json
import os
import shutil
import sys
from pathlib import Path

BY_NODE = {by_node}

run_dir = Path(os.environ["WFC_RUN_DIR"])
run_dir.mkdir(parents=True, exist_ok=True)
sample = os.environ.get("WFC_SAMPLE", "")
variant = os.environ.get("WFC_VARIANT", "")
node_id = os.environ.get("WFC_NODE_ID", "")
project_root = Path(__file__).resolve().parents[2]

# One script serves every node that names this method. The node's entry
# resolves its behavior by sample, then by variant, then the default --
# the rule NodeSpec.behavior_for applies. An unknown node id is loud.
if node_id not in BY_NODE:
    sys.stderr.write(
        "WFC_NODE_ID %r names no node of this method (have %s)\\n"
        % (node_id, sorted(BY_NODE))
    )
    sys.exit(2)
NODE = BY_NODE[node_id]
if sample in NODE["by_sample"]:
    BEHAVIOR = NODE["by_sample"][sample]
elif variant in NODE["by_variant"]:
    BEHAVIOR = NODE["by_variant"][variant]
else:
    BEHAVIOR = NODE["behavior"]

if BEHAVIOR["stdout"]:
    sys.stdout.write(BEHAVIOR["stdout"])
if BEHAVIOR["stderr"]:
    sys.stderr.write(BEHAVIOR["stderr"])

echo_content = None
if BEHAVIOR["echo_input"]:
    slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{{}}"))
    echo_paths = slot_paths.get(BEHAVIOR["echo_input"], [])
    if not echo_paths:
        sys.stderr.write(
            "WFC_INPUT_PATHS had no %s slot\\n" % BEHAVIOR["echo_input"]
        )
        sys.exit(2)
    echo_content = Path(echo_paths[0]).read_text()

if BEHAVIOR["concat_input"]:
    slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{{}}"))
    concat_paths = slot_paths.get(BEHAVIOR["concat_input"], [])
    if not concat_paths:
        sys.stderr.write(
            "WFC_INPUT_PATHS had no %s slot\\n" % BEHAVIOR["concat_input"]
        )
        sys.exit(2)
    merged_lines = []
    for member in concat_paths:
        member_lines = Path(member).read_text().splitlines()
        if not member_lines:
            continue
        if not merged_lines:
            merged_lines.append(member_lines[0])
        merged_lines.extend(member_lines[1:])
    echo_content = "\\n".join(merged_lines) + "\\n"

if BEHAVIOR["read_inputs"]:
    slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{{}}"))
    for slot in BEHAVIOR["read_inputs"]:
        paths = slot_paths.get(slot, [])
        if isinstance(paths, str):
            paths = [paths]
        missing = [p for p in paths if not Path(p).exists()]
        if not paths or missing:
            sys.stderr.write(
                "input slot %s did not arrive (paths %r, missing %r)\\n"
                % (slot, paths, missing)
            )
            sys.exit(2)

manifest_outputs = {{}}
for slot, filename in BEHAVIOR["slot_files"].items():
    if slot in BEHAVIOR["skip_slots"]:
        continue
    if slot in BEHAVIOR["saved"]:
        filename = BEHAVIOR["saved"][slot]
        target = run_dir / "_workdir" / filename
        manifest_outputs[slot] = "_workdir/" + filename
    elif slot in BEHAVIOR["nested"]:
        target = run_dir / "_workdir" / filename
        manifest_outputs[slot] = "_workdir/" + filename
    else:
        target = run_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    content = BEHAVIOR["outputs"].get(slot)
    if content is None:
        content = echo_content
    if content is None:
        content = "slot,sample\\n%s,%s\\n" % (slot, sample)
    if BEHAVIOR["dir_slots"].get(slot):
        target.mkdir(parents=True, exist_ok=True)
        children = content if isinstance(content, dict) else {{"part.csv": content}}
        for child_name, child_text in children.items():
            # A child name may carry subdirectories ("nested/b.csv").
            (target / child_name).parent.mkdir(parents=True, exist_ok=True)
            (target / child_name).write_bytes(child_text.encode("utf-8"))
    else:
        # write_bytes, not write_text: newline translation on Windows would
        # make the artifact's bytes platform-dependent, and a test that
        # hashes a declared output needs exactly the declared bytes.
        target.write_bytes(content.encode("utf-8"))

if BEHAVIOR["write_manifest"]:
    (run_dir / "_wfc_results.json").write_text(
        json.dumps({{"outputs": manifest_outputs, "metrics": BEHAVIOR["metrics"]}})
    )

for rel in BEHAVIOR["delete_paths"]:
    victim = project_root / rel
    if victim.is_dir():
        shutil.rmtree(victim, ignore_errors=True)
    elif victim.exists():
        victim.unlink()

if BEHAVIOR["raises"]:
    raise RuntimeError(BEHAVIOR["raises"])

sys.exit(BEHAVIOR["exit_code"])
'''


def _stub_script(specs: list[NodeSpec]) -> str:
    """Render the generated method script for every node naming one method.

    One script serves every node, sample and variant: a per-node table of
    each node's default behavior and its sample and variant override maps,
    which the script selects from with ``WFC_NODE_ID``, ``WFC_SAMPLE`` and
    ``WFC_VARIANT`` -- all three set by production's dispatch phase. The
    method process is the same artifact for all of a method's targets, so
    the branch has to live inside it.

    Args:
        specs: The nodes whose behaviors the script plays.

    Returns:
        The script source.
    """
    # repr, not json.dumps: the payload is embedded as a Python literal in
    # generated source, and JSON's true/false/null are not Python names.
    by_node = {
        spec.id: {
            "behavior": _behavior_payload(spec, spec.behavior),
            "by_sample": {
                sample: _behavior_payload(spec, behavior)
                for sample, behavior in spec.behavior_by_sample.items()
            },
            "by_variant": {
                variant: _behavior_payload(spec, behavior)
                for variant, behavior in spec.behavior_by_variant.items()
            },
        }
        for spec in specs
    }
    # A node of a legacy numeric-id document is scheduled -- and so
    # dispatched with WFC_NODE_ID -- under its method name, which only a
    # method one node names can stand for. Only a numeric id gets the alias:
    # a string-id node is dispatched under its own id, so one dispatched
    # under its method name must still fail loudly.
    if len(specs) == 1 and str(specs[0].id).isdigit():
        by_node.setdefault(specs[0].method_name, by_node[specs[0].id])
    return _STUB_TEMPLATE.format(by_node=repr(by_node))


def _behavior_payload(spec: NodeSpec, behavior) -> dict:
    """Render one behavior into the literal the generated script reads.

    Args:
        spec: The node the behavior belongs to (supplies the slot shapes,
            which are a property of the node's contract rather than of the
            behavior).
        behavior: The behavior to render.

    Returns:
        The payload dict.
    """
    # ``build_pipeline_json`` names each declared slot's file ``<slot>.csv``
    # from the method.yaml contract; the stub writes exactly those names so
    # the collect phase's slot scan finds them.
    slot_files = {
        slot: spec.output_files.get(slot, f"{slot}.csv") for slot in spec.outputs
    }
    return {
        "raises": behavior.raises,
        "echo_input": behavior.echo_input,
        "concat_input": behavior.concat_input,
        "read_inputs": list(behavior.read_inputs),
        "exit_code": behavior.exit_code,
        "outputs": dict(behavior.outputs or {}),
        "skip_slots": list(behavior.skip_slots),
        "metrics": dict(behavior.metrics),
        "write_manifest": behavior.write_manifest,
        "stdout": behavior.stdout,
        "stderr": behavior.stderr,
        "nested": list(behavior.nested_outputs),
        "saved": dict(behavior.saved_files or {}),
        "delete_paths": list(behavior.delete_paths),
        "slot_files": slot_files,
        "dir_slots": {
            slot: str(t).lower() in ("directory", "dir")
            for slot, t in spec.outputs.items()
        },
    }


# =============================================================================
# git
# =============================================================================

def _ensure_git_repo(root: Path) -> None:
    """Initialise ``root`` as a git repo with one commit when it is not one."""
    if (root / ".git").exists():
        return
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "wfc@wfc"], cwd=root,
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "wfc"], cwd=root,
                   check=True, capture_output=True)
    (root / ".gitkeep").write_text("")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True,
                   capture_output=True)


def commit_everything(root: Path) -> str:
    """Commit everything in the project and return the resulting commit sha.

    Args:
        root: Project root.

    Returns:
        The new HEAD sha.
    """
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "harness project", "--allow-empty"],
                   cwd=root, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True)
    return head.stdout.strip()


__all__ = ["Project", "STUB_DIGEST", "build_project", "commit_everything"]
