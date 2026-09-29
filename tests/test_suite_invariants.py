"""The suite's own rules about sample state, and every sanctioned shortcut.

This module is two things in one file, deliberately: the **allowlist** of
every place the test suite is permitted to build sample state the short way,
and the **gates** that refuse a new one. A reader who wants to know "what
shortcuts does this suite take, and why is each legitimate?" reads the
dictionaries below and nothing else.

The rules exist because a test that builds sample state directly can model a
project no user could have: a ``Sample`` row whose ``content_hash`` addresses
bytes that are nowhere, a file under ``data/samples/`` that no restore put
there, a registration whose source is already its own destination so every
restore takes the dest-already-valid skip. Each of those was green in this
suite while the product was broken.

The rules and where each is enforced, numbered as the gates' failure
messages number them:

1. **No test builds a persistence row by hand** -- a constructor call on
   any of the seven models (``Run``, ``RunOutput``, ``Method``, ``Module``,
   ``MethodContract``, ``RunInput``, ``Sample``) resolved to its
   ``wfc.persistence`` binding, or a call to a registered row shortcut --
   outside the routes and fakes packages and the allowlist below --
   :func:`test_no_test_builds_a_persistence_row_outside_the_allowlist`.
2. **Nothing but ``restore_sample`` writes under ``<project>/data/samples/``**
   — :func:`test_restore_sample_is_the_only_writer_under_data_samples`.
3. **A sample is registered from a source outside the project's ``data/``**,
   so ``registered_path != source_path``. Callers of the
   ``register_sample_row`` fixture helper are already refused at runtime by
   that helper's own guard, which raises an ``AssertionError`` naming the
   path; this module closes the remaining hole — a test calling production
   ``register_sample`` directly from a staged path. See
   :func:`test_no_test_registers_a_sample_from_inside_data_samples`.
4. **No test resolves ``init_project``'s default archive**, which is
   ``~/.wfc/archives/<project>`` in the developer's real home directory. The
   route-agnostic gate is the session-scoped ``_home_archive_leak_guard``
   fixture in ``tests/conftest.py``, which compares the directory listing at
   session start and session end and fails naming what appeared. The route
   every fixture actually takes is covered here by
   :func:`test_the_suite_scaffolds_projects_with_an_out_of_tree_archive`.
5. **No test installs a raw fake outside the registry**, beyond the per-module
   count frozen in ``FAKE_SITE_ALLOWLIST`` -- a ``monkeypatch.setattr``,
   ``patch(``, ``mock.patch`` or ``MagicMock(`` call node outside
   ``tests/fixtures/fakes/`` -- :func:`test_no_test_installs_a_raw_fake_outside_the_allowlist`.
   ``setenv`` / ``delenv`` are environment plumbing and exempt. The count
   only falls; the fake to use instead is an entry of ``tests.fixtures.fakes``.
7. **Every registry entry is exported and its backing witness resolves** --
   :func:`test_every_registry_entry_is_exported_from_the_package` and
   :func:`test_every_backing_witness_resolves`; ``owed`` entries are counted
   against the only-falling ``OWED_BACKING_WITNESSES``.

Not a numbered gate: **a directory sample's content reaches the archive.**
``tests/integration/test_real_path_sample_lifecycle.py::test_a_directory_sample_reaches_the_archive``
witnesses it directly; a second gate here would only duplicate it.

These are static scans of ``tests/`` source. They see what is written, not
what runs, so they are a floor rather than a proof.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TESTS_ROOT.parent


# =============================================================================
# The allowlists — the deliverable
# =============================================================================

#: Modules permitted to build a persistence row by hand -- a constructor call
#: on one of the seven models resolved to ``wfc.persistence``, or a call to a
#: registered row shortcut (``seed_sample_row``, ``typed_rows``) -- outside the
#: routes and fakes packages, which are exempt by directory.
#:
#: Each entry says why the row itself is the input under test, or names the
#: state no writer produces. Every entry is a stay until the module moves
#: onto a route or onto ``fakes.typed_rows`` with the reason at the call, and
#: the entry goes. A new module is a new decision, not an addition.
ROW_BUILDER_ALLOWLIST: dict[str, str] = {
    "tests/integration/test_demo_integration.py":
        "typed Run/RunInput/RunOutput rows standing in for a demo history the "
        "teardown-precision test must tell apart from user rows",
    "tests/test_cache_key_sensitivity.py":
        "cache-key sensitivity: the row's content_hash is the independent "
        "variable and nothing restores or runs; two seeded method rows "
        "sharing a name across modules is a state registration refuses",
    "tests/test_cache_provenance_primitives.py":
        "typed Module/Method/Run/RunOutput rows as the input to the "
        "cache-provenance primitives, pure derivations over rows",
    "tests/test_cancelled_rows.py":
        "typed run rows in each status as the cancelled-rows walk's input",
    "tests/test_canvas_api_artifacts.py":
        "a stay: typed Run and RunOutput rows whose artifacts are then "
        "archived for real — the listing and export routes read the archive, "
        "and the rows are what points them at it",
    "tests/test_canvas_api_builder.py":
        "the output-columns route reads a referenced run's recorded params; "
        "the typed row carries the params under test",
    "tests/test_canvas_api_history.py":
        "routes serialising the samples, runs and methods tables — the rows "
        "ARE the input to the readers under test",
    "tests/test_canvas_api_project.py":
        "the status route counts runs by status; two typed Run rows are the "
        "count's input",
    "tests/test_canvas_api_registry.py":
        "the registry listing route — the row IS the input, and the case is "
        "about ordering two rows",
    "tests/test_canvas_api_runs.py":
        "the status route's row-state table: rows in each status the route "
        "classifies, some of which (cancelled without started_at) no route "
        "leaves behind",
    "tests/test_canvas_cachehit_artifacts.py":
        "a canvas reader over the registry — the row IS the input; see the "
        "comment above the seed_sample_row import",
    "tests/test_canvas_logs.py":
        "the log route's terminal event reads a run row's status and error "
        "columns; a failed run carrying a chosen error_message and traceback "
        "is the state under test, typed beside the registration-built method",
    "tests/test_canvas_pipeline_document_endpoint.py":
        "a typed Run row beside the route-built runs, the document reader's "
        "input",
    "tests/test_canvas_registry_endpoints.py":
        "typed run rows in three statuses for the env listing's run-stats "
        "column",
    "tests/test_canvas_run.py":
        "a run-submission route reading the registry and a status route "
        "reading run rows — the rows ARE the input",
    "tests/test_claim_refusals.py":
        "the claim's row resolution — each refusal turns on whether a row is "
        "found; see the comment above the seed_sample_row import",
    "tests/test_content_hash.py":
        "typed Module/Method/Run/RunOutput rows for complete_run's "
        "content_hash column under test",
    "tests/test_contracts_env_spec_grammar.py":
        "typed Module/Method rows carrying the env spec under test",
    "tests/test_deferred_archiving.py":
        "typed Module/Method/Run/RunOutput rows in each archive state as the "
        "deferred archiver's input",
    "tests/test_demo_canvas.py":
        "typed demo rows the demo canvas routes read",
    "tests/test_demo_guard.py":
        "the demo guard counts pre-existing rows to decide whether a project "
        "is empty; the row is the guard's input",
    "tests/test_dev_routes_demo.py":
        "a typed completed run the reference-seeding lookup finds; the seed's "
        "own submission is a registry fake",
    "tests/test_env_fingerprint.py":
        "cache-key composition — the row's content_hash IS the input; see the "
        "comment above the seed_sample_row import",
    "tests/test_export_cli.py":
        "typed run and output rows the export resolvers read",
    "tests/test_input_identity_fetch.py":
        "input-identity fetch over a hashed row and a malformed one — the "
        "malformed shape cannot be registered at all",
    "tests/test_legacy_samples.py":
        "the malformed NULL-content_hash row, a deviation production cannot "
        "produce; see the comment above the seed_sample_row import",
    "tests/test_lineage_synthesizer.py":
        "typed Module/Method/Run/RunInput rows: the run DAG the synthesizer "
        "reads is the input",
    "tests/test_list_artifacts_children.py":
        "typed rows whose artifact paths the children listing reads",
    "tests/test_output_slot_records.py":
        "a slotless RunOutput row — a state the production write path does "
        "not produce; the reader's refusal is the case",
    "tests/test_pipeline_logging.py":
        "typed Module/Method/Run rows the pipeline logger reads",
    "tests/test_pipeline_name_passthrough.py":
        "typed rows carrying the pipeline name column under test",
    "tests/test_project_root.py":
        "project-root resolution: the row is written into one project's "
        "database to prove a later read reached that database and not another",
    "tests/test_push_worker.py":
        "the push worker's queue: a row in each push_status is the input, and "
        "deferred/failed are states registration does not leave behind",
    "tests/test_registration.py":
        "typed Module/Method rows setting up the one-module-per-name state "
        "registration is then asked to refuse",
    "tests/test_resolve.py":
        "typed Run and RunOutput rows with content_hash as the resolver's "
        "input",
    "tests/test_session_transaction.py":
        "a typed Module row as the session-transaction semantics' input",
    "tests/test_stdout_metrics.py":
        "a typed RunOutput row the stdout-metrics reader parses",
    "tests/test_versioning.py":
        "cache-key composition and comparison — the row's content_hash IS the "
        "input; see the comment above the seed_sample_row import",
}


#: Modules permitted raw fake sites — ``monkeypatch.setattr`` / ``delattr``,
#: ``patch(`` and its spellings, ``MagicMock(`` — outside the fakes package,
#: as ``(distinct lines, reason)``. The reason names the mechanism and the
#: registry entry a conversion writes it onto. The count only ever falls:
#: rewriting a site onto its entry lowers the number, and ``tests/fixtures/fakes/`` is exempt by
#: directory and by nothing else.
FAKE_SITE_ALLOWLIST: dict[str, tuple[int, str]] = {
    "tests/harness/drivers.py": (2,
        "the two harness spies (fakes.interpose_phases, "
        "fakes.snapshot_output_writers) keep their bodies here because the "
        "wrappers close over the TargetRun record; the registry delegates"),
    "tests/test_check_catalog_coverage.py": (8,
        "the gate's own constants (REPO_ROOT, EXPECTED_CASES, MAX_WIDE_CASES) "
        "pinned for a synthetic tree — a tool-test pin, no production boundary; "
        "no entry: the patched object is the tool under test"),
    "tests/test_check_layering.py": (3,
        "the gate's REPO_ROOT pinned for a synthetic tree; no entry, as above"),
}


#: Modules permitted to create files under a project's ``data/samples/``
#: without going through ``restore_sample``.
#:
#: One entry, and it should stay that way. ``restore_sample`` is the only
#: writer in production; a test that stages there by hand is building a
#: project state no user can reach.
DATA_SAMPLES_WRITER_ALLOWLIST: dict[str, str] = {
    "tests/harness/project.py":
        "the scenario harness materializes two DECLARED deviation states that "
        "no restore produces: an empty sample directory for a scenario's "
        "empty_samples (mkdir with no restore behind it), and a pre-existing "
        ".sample_ready sentinel for sample_ready_sentinel=True (the Snakemake "
        "restore rule's own OUTPUT, present before the rule runs). Every "
        "sample the scenario does NOT declare deviant goes through production "
        "restore_sample at step 8.1; see that step's `critical` note",
    "tests/test_materialize_failures_recorded.py":
        "`_add_sibling` writes one DECLARED deviation state no production verb "
        "writes: a user's stray file beside a sample that restore_sample "
        "already delivered, sorting before the recorded name, so the test can "
        "show materialize reads the recorded name and never a listing's first "
        "entry. The sample itself is registered and restored by production",
}

#: Modules permitted to register a sample from a source that sits under the
#: project's own ``data/samples/``.
#:
#: Empty. Registering a file that already sits at its own ``registered_path``
#: makes ``registered_path == source_path``, so every restore takes
#: ``restore_from_cache``'s dest-already-valid skip and the real-copy path is
#: never exercised. ``tests/fixtures/conftest.py::sample_source_dir`` is where
#: a fixture stages one instead.
REGISTER_FROM_DATA_ALLOWLIST: dict[str, str] = {}


# =============================================================================
# Scanning machinery
# =============================================================================

#: The seven persistence models a test may not construct by hand, and the
#: registered row shortcuts whose callers carry the same duty.
_PERSISTENCE_MODELS = frozenset({
    "Run", "RunOutput", "Method", "Module", "MethodContract", "RunInput",
    "Sample",
})
_ROW_SHORTCUTS = frozenset({"seed_sample_row", "typed_rows"})

#: Directories whose modules build state for the suite: the production-path
#: routes and the registry of fakes and declared shortcuts. Exempt from the
#: row scan and the fake-site scan by directory and by nothing else.
_STATE_PACKAGE_DIRS = ("tests/fixtures/routes/", "tests/fixtures/fakes/")

_PATH_MUTATORS = frozenset({
    "write_text", "write_bytes", "mkdir", "touch", "open",
    "symlink_to", "hardlink_to", "rename",
})

_COPY_FUNCS = frozenset({"copy", "copy2", "copyfile", "copytree", "move"})

#: Layout helpers that RETURN a path under ``data/samples/``. A caller that
#: goes through one never writes the segments ``data`` and ``samples`` in its
#: own source, so a scanner keyed on those literals is blind to the route
#: production code and the harness actually take — which is how invariant 2
#: sat at zero detections while the harness wrote under data/samples/ twice.
#: Recognising the callee name closes the route instead of excusing it.
_LAYOUT_SAMPLE_HELPERS = frozenset({"sample_dir", "sample_ready_sentinel"})


def _test_modules() -> list[tuple[str, ast.Module]]:
    """Parse every Python module under ``tests/``.

    Returns:
        ``(repo-relative posix path, parsed module)`` pairs, sorted by path.
    """
    parsed: list[tuple[str, ast.Module]] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        parsed.append((rel, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
    return parsed


def _callee_name(func: ast.expr) -> str | None:
    """Return the bare name a call's callee resolves to, if it has one."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _strings_in(node: ast.expr) -> list[str]:
    """Return every string constant appearing anywhere inside an expression."""
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _names_in(node: ast.expr) -> set[str]:
    """Return every bare name appearing anywhere inside an expression."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _looks_like_data_samples(node: ast.expr, tainted: set[str]) -> bool:
    """Say whether an expression denotes a path under ``data/samples/``.

    Three signatures: the literal segments ``data`` and ``samples`` appearing
    together in the expression (``project / "data" / "samples" / name``, or a
    single ``"data/samples/..."`` string); a call to one of the Layout helpers
    that returns such a path (``layout.sample_dir(root, s)``), which is the
    route a caller takes when it does NOT spell the segments itself; or a name
    previously bound to either.

    Args:
        node: The expression to classify.
        tainted: Names already known to denote a ``data/samples/`` path.

    Returns:
        ``True`` when the expression denotes such a path.
    """
    strings = _strings_in(node)
    flat = " ".join(strings).replace("\\", "/")
    if "data/samples" in flat:
        return True
    if "data" in strings and "samples" in strings:
        return True
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call) and _callee_name(inner.func) in _LAYOUT_SAMPLE_HELPERS:
            return True
    return bool(_names_in(node) & tainted)


def _tainted_names(tree: ast.Module) -> set[str]:
    """Collect the names in a module bound to a ``data/samples/`` path.

    Assignments are visited in source order so a chain
    (``d = p / "data" / "samples" / n`` then ``f = d / "x.csv"``) propagates.
    """
    tainted: set[str] = set()
    assigns = sorted(
        (n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))
         and n.value is not None),
        key=lambda n: (n.lineno, n.col_offset),
    )
    for node in assigns:
        if not _looks_like_data_samples(node.value, tainted):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    tainted.add(name.id)
    return tainted


def _receiver(func: ast.Attribute) -> ast.expr:
    """Return the expression a method call is made on."""
    return func.value


def _format(hits: list[tuple[str, int, str]]) -> str:
    """Render offenders one per line as ``path:line — detail``."""
    return "\n".join(f"  {rel}:{line} — {detail}" for rel, line, detail in hits)


# =============================================================================
# Invariant 1 — a persistence row is produced by its writer, not typed in
# =============================================================================

def _persistence_bindings(tree: ast.Module) -> tuple[dict[str, str], set[str]]:
    """Resolve the names a module binds to ``wfc.persistence`` models.

    A model is counted only when the name a call resolves to is bound to
    ``wfc.persistence`` -- by a direct import (``from wfc.persistence import
    Run``), a submodule import (``from wfc.persistence.models import Run as
    R``) or a module alias (``import wfc.persistence as p`` then ``p.Run(``).
    ``Sample(``, ``Method(`` and ``Module(`` also name harness types and
    canvas records, so a same-named class bound elsewhere is reported as
    unresolved rather than counted.

    Args:
        tree: The parsed module.

    Returns:
        ``(names, modules)``: local name -> model name for the bound model
        classes, and the local names bound to the ``wfc.persistence``
        package itself (for attribute calls).
    """
    names: dict[str, str] = {}
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module \
                and node.module.split(".")[:2] == ["wfc", "persistence"]:
            for alias in node.names:
                if alias.name in _PERSISTENCE_MODELS:
                    names[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "wfc.persistence":
                    modules.add(alias.asname or "wfc.persistence")
    return names, modules


def _scan_row_builders(rel: str, tree: ast.Module
                       ) -> tuple[list[tuple[str, int, str]], list[tuple[str, int, str]]]:
    """Return every hand-built persistence row one parsed module constructs.

    Args:
        rel: Repo-relative path, carried into each hit for the message.
        tree: The parsed module.

    Returns:
        ``(hits, unresolved)``: hits are ``(rel, line, detail)`` per model
        constructor resolved to ``wfc.persistence`` or per registered row
        shortcut call, allowlist not applied; unresolved are same-named
        constructors whose binding the scan could not trace, reported and
        never counted.
    """
    names, modules = _persistence_bindings(tree)
    hits: list[tuple[str, int, str]] = []
    unresolved: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in names:
                hits.append((rel, node.lineno, f"{names[func.id]}(...)"))
            elif func.id in _PERSISTENCE_MODELS:
                unresolved.append((rel, node.lineno,
                                   f"{func.id}(...) not bound to wfc.persistence"))
            elif func.id in _ROW_SHORTCUTS:
                hits.append((rel, node.lineno, f"{func.id}(...) shortcut"))
        elif isinstance(func, ast.Attribute) and func.attr in _PERSISTENCE_MODELS:
            receiver = ast.unparse(func.value)
            if receiver in modules:
                hits.append((rel, node.lineno, f"{func.attr}(...)"))
            else:
                unresolved.append((rel, node.lineno,
                                   f"{receiver}.{func.attr}(...) not bound to wfc.persistence"))
        elif isinstance(func, ast.Attribute) and func.attr in _ROW_SHORTCUTS:
            hits.append((rel, node.lineno, f"{func.attr}(...) shortcut"))
    hits.sort(key=lambda h: h[1])
    unresolved.sort(key=lambda h: h[1])
    return hits, unresolved


def _in_state_package(rel: str) -> bool:
    """Say whether a module lives in the routes or fakes package."""
    return any(rel.startswith(prefix) for prefix in _STATE_PACKAGE_DIRS)


def test_no_test_builds_a_persistence_row_outside_the_allowlist():
    """Only allowlisted modules may build a persistence row the short way."""
    offenders: list[tuple[str, int, str]] = []
    seen: set[str] = set()

    for rel, tree in _test_modules():
        if _in_state_package(rel):
            continue
        hits, _unresolved = _scan_row_builders(rel, tree)
        if hits:
            seen.add(rel)
        if rel not in ROW_BUILDER_ALLOWLIST:
            offenders.extend(hits)

    assert not offenders, (
        "SUITE INVARIANT 1 — a test may not build a persistence row by hand.\n"
        f"{_format(offenders)}\n\n"
        "A typed-in row models a state its writer may never produce: a Sample "
        "whose content_hash addresses nothing, a Run that never ran. Produce "
        "the state through tests.fixtures.routes (completed_run / claimed_run "
        "for a run and its outputs, register_test_method for a method and its "
        "module, create_sample_csv for a sample) or, where the row itself is "
        "the input under test, through the registered shortcut "
        "tests.fixtures.fakes.typed_rows / seed_sample_row and add the module "
        "to ROW_BUILDER_ALLOWLIST in tests/test_suite_invariants.py with the "
        "reason."
    )

    stale = sorted(set(ROW_BUILDER_ALLOWLIST) - seen)
    assert not stale, (
        "SUITE INVARIANT 1 — these modules hold an allowlist entry but no "
        f"longer build a persistence row: {stale}. Remove the entry; an "
        "allowlist nobody prunes stops being a review record."
    )


#: A module that constructs two models bound to ``wfc.persistence`` (one by
#: alias, one through the package), calls the registered shortcut, and also
#: constructs a same-named class bound elsewhere -- which is reported, not
#: counted.
_SYNTHETIC_ROW_BUILDER = (
    "from wfc.persistence import Run as R\n"
    "import wfc.persistence as p\n"
    "from tests.harness import Sample\n"
    "def seed(session):\n"
    "    session.add(R(status='completed'))\n"
    "    session.add(p.RunOutput(run_id=1))\n"
    "    seed_sample_row('s')\n"
    "    return Sample('not a row')\n"
)


def test_invariant_1_scanner_detects_a_row_builder_it_is_shown():
    """The invariant-1 scan body flags bound constructors and reports unbound ones."""
    hits, unresolved = _scan_row_builders(
        "tests/_synthetic_rows.py", ast.parse(_SYNTHETIC_ROW_BUILDER))
    assert [line for _, line, _ in hits] == [5, 6, 7], (
        "SUITE INVARIANT 1 (self-test) — the scan body no longer flags a model "
        "constructor bound to wfc.persistence by alias, through the package "
        f"alias, or the registered shortcut. Detections: {hits}."
    )
    assert [line for _, line, _ in unresolved] == [8], (
        "SUITE INVARIANT 1 (self-test) — a same-named class bound outside "
        "wfc.persistence must be reported as unresolved, never counted. "
        f"Unresolved: {unresolved}."
    )


# =============================================================================
# Invariant 2 — restore_sample is the only writer under data/samples/
# =============================================================================

def _scan_data_samples_writers(rel: str, tree: ast.Module) -> list[tuple[str, int, str]]:
    """Return every write under ``data/samples/`` one parsed module performs.

    The scan body itself, so the real gate and its self-test run the same
    code. A scanner exercised only over a tree that happens to contain no
    offenders asserts nothing about its own ability to detect one.

    Args:
        rel: Repo-relative path, carried into each hit for the message.
        tree: The parsed module.

    Returns:
        ``(rel, line, detail)`` per detection, allowlist not applied.
    """
    hits: list[tuple[str, int, str]] = []
    tainted = _tainted_names(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        detail = None
        if isinstance(node.func, ast.Attribute) and node.func.attr in _PATH_MUTATORS:
            if _looks_like_data_samples(_receiver(node.func), tainted):
                detail = f".{node.func.attr}(...) on a data/samples/ path"
        elif isinstance(node.func, ast.Name) and node.func.id == "open":
            mode = "".join(_strings_in(node)[1:2])
            if node.args and _looks_like_data_samples(node.args[0], tainted) \
                    and any(c in mode for c in "wax"):
                detail = f"open(..., {mode!r}) on a data/samples/ path"
        elif _callee_name(node.func) in _COPY_FUNCS and len(node.args) >= 2:
            if _looks_like_data_samples(node.args[1], tainted):
                detail = f"{_callee_name(node.func)}(..., dst) into data/samples/"
        if detail is not None:
            hits.append((rel, node.lineno, detail))
    return hits


def test_restore_sample_is_the_only_writer_under_data_samples():
    """No test may create a file under a project's ``data/samples/``."""
    hits = [hit for rel, tree in _test_modules()
            for hit in _scan_data_samples_writers(rel, tree)]
    seen = {rel for rel, _, _ in hits}
    offenders = [hit for hit in hits if hit[0] not in DATA_SAMPLES_WRITER_ALLOWLIST]

    assert not offenders, (
        "SUITE INVARIANT 2 — restore_sample is the only sanctioned writer "
        "under <project>/data/samples/.\n"
        f"{_format(offenders)}\n\n"
        "Registration copies nothing there; registered_path records where a "
        "run's restore_sample rule will materialize the file. A test that "
        "stages the file by hand models a project a user cannot reach, and it "
        "will keep passing after the restore path breaks. Stage the source "
        "under tests.fixtures.conftest.sample_source_dir, register it, and "
        "either run the pipeline or call restore_sample explicitly."
    )

    stale = sorted(set(DATA_SAMPLES_WRITER_ALLOWLIST) - seen)
    assert not stale, (
        "SUITE INVARIANT 2 — these modules hold an allowlist entry but no "
        f"longer write under data/samples/: {stale}. Remove the entry."
    )


#: A module that writes under ``data/samples/`` by spelling the segments.
_SYNTHETIC_LITERAL_WRITER = (
    "def stage(project, name):\n"
    "    d = project / 'data' / 'samples' / name\n"
    "    d.mkdir(parents=True)\n"
    "    (d / 'x.csv').write_text('a,b\\n')\n"
)

#: The same write, taken through Layout instead. A separate witness because
#: the two routes are separate code paths in the scanner and have already
#: drifted apart once: the literal arm worked and the helper arm did not
#: exist, so the gate read as clean over a tree that was writing there.
_SYNTHETIC_LAYOUT_WRITER = (
    "def stage(root, name):\n"
    "    layout.sample_dir(root, name).mkdir(parents=True, exist_ok=True)\n"
    "    s = layout.sample_ready_sentinel(root, name)\n"
    "    s.write_text('')\n"
)


def test_invariant_2_scanner_detects_a_writer_it_is_shown():
    """The invariant-2 scan body flags both routes into ``data/samples/``."""
    literal = _scan_data_samples_writers(
        "tests/_synthetic_literal.py", ast.parse(_SYNTHETIC_LITERAL_WRITER))
    assert [line for _, line, _ in literal] == [3, 4], (
        "SUITE INVARIANT 2 (self-test) — the scan body no longer flags a "
        f"write to project/'data'/'samples'/name. Detections: {literal}. "
        "The gate above cannot be trusted while this is empty: it would read "
        "as clean over any tree."
    )

    via_layout = _scan_data_samples_writers(
        "tests/_synthetic_layout.py", ast.parse(_SYNTHETIC_LAYOUT_WRITER))
    assert [line for _, line, _ in via_layout] == [2, 4], (
        "SUITE INVARIANT 2 (self-test) — the scan body no longer flags a "
        "write through layout.sample_dir / layout.sample_ready_sentinel. "
        f"Detections: {via_layout}. A caller that goes through Layout never "
        "spells 'data' or 'samples', so losing this arm makes the gate blind "
        "to the route the harness and production both take."
    )


# =============================================================================
# Invariant 3 — a sample is registered from outside the project's data/
# =============================================================================

def _scan_register_from_data(rel: str, tree: ast.Module) -> list[tuple[str, int, str]]:
    """Return every registration from a ``data/samples/`` source in a module.

    Extracted for the same reason as :func:`_scan_data_samples_writers`: the
    gate below finds nothing in the real tree, so without a self-test over a
    known offender it proves only that the tree is clean, not that the scan
    works.

    Args:
        rel: Repo-relative path, carried into each hit for the message.
        tree: The parsed module.

    Returns:
        ``(rel, line, detail)`` per detection, allowlist not applied.
    """
    hits: list[tuple[str, int, str]] = []
    tainted = _tainted_names(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _callee_name(node.func) not in ("register_sample", "register_sample_row"):
            continue
        sources = list(node.args) + [kw.value for kw in node.keywords
                                     if kw.arg in ("source_path", "data_file", None)]
        if not any(_looks_like_data_samples(src, tainted) for src in sources):
            continue
        hits.append((rel, node.lineno, "registers a source under data/samples/"))
    return hits


def test_no_test_registers_a_sample_from_inside_data_samples():
    """No test may register a sample whose source is already its destination.

    The fixture helper ``register_sample_row`` refuses this at runtime with an
    ``AssertionError`` naming the path, which covers every caller of *that*
    helper. This closes the other door: a test calling production
    ``register_sample`` directly.
    """
    hits = [hit for rel, tree in _test_modules()
            for hit in _scan_register_from_data(rel, tree)]
    seen = {rel for rel, _, _ in hits}
    offenders = [hit for hit in hits if hit[0] not in REGISTER_FROM_DATA_ALLOWLIST]

    assert not offenders, (
        "SUITE INVARIANT 3 — a sample is registered from a source outside the "
        "project's data/ tree.\n"
        f"{_format(offenders)}\n\n"
        "Registering a file that already sits at its own registered_path makes "
        "registered_path == source_path, so every restore takes "
        "restore_from_cache's dest-already-valid skip and the real-copy path is "
        "never exercised. Stage the source under "
        "tests.fixtures.conftest.sample_source_dir instead."
    )

    stale = sorted(set(REGISTER_FROM_DATA_ALLOWLIST) - seen)
    assert not stale, (
        "SUITE INVARIANT 3 — these modules hold an allowlist entry but no "
        f"longer register from under data/samples/: {stale}. Remove the entry."
    )


#: Two registrations from inside the project's own data/ tree: one spelling
#: the segments positionally, one through Layout by keyword. Both are shapes
#: the scanner claims to catch, and the keyword arm is the one a real caller
#: is most likely to write.
_SYNTHETIC_REGISTER_FROM_DATA = (
    "def stage(project, root, name):\n"
    "    staged = project / 'data' / 'samples' / name / 'x.csv'\n"
    "    register_sample(name, staged)\n"
    "    register_sample_row(name, source_path=layout.sample_dir(root, name))\n"
)


def test_invariant_3_scanner_detects_a_registration_it_is_shown():
    """The invariant-3 scan body flags a source under ``data/samples/``."""
    hits = _scan_register_from_data(
        "tests/_synthetic_register.py", ast.parse(_SYNTHETIC_REGISTER_FROM_DATA))
    assert [line for _, line, _ in hits] == [3, 4], (
        "SUITE INVARIANT 3 (self-test) — the scan body no longer flags a "
        f"registration whose source is under data/samples/. Detections: "
        f"{hits}. The gate above finds nothing in the real tree, so with this "
        "empty it asserts nothing at all."
    )


# =============================================================================
# Invariant 4 — nothing lands in the developer's ~/.wfc/archives/
# =============================================================================

def test_the_suite_scaffolds_projects_with_an_out_of_tree_archive(tmp_path, monkeypatch):
    """``init_test_project`` names an archive beside the project, not in ``~``.

    ``init_project`` defaults the archive to ``~/.wfc/archives/<project>`` and
    ``init_dvc`` pre-creates it, so a fixture that does not name one writes
    into the developer's real home directory and never cleans up. This is the
    route every fixture in the suite takes; the session-scoped
    ``_home_archive_leak_guard`` in ``tests/conftest.py`` is the backstop for
    every other route.
    """
    import subprocess

    from tests.fixtures.conftest import init_test_project, project_archive_dir

    home_archives = Path.home() / ".wfc" / "archives"
    before = sorted(p.name for p in home_archives.iterdir()) if home_archives.is_dir() else []

    project = tmp_path / "archive_scope"
    project.mkdir()
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(project))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{project / '.wfc' / 'wfc.db'}")

    init_test_project(project)

    expected = project_archive_dir(project)
    assert expected.is_dir(), (
        f"SUITE INVARIANT 4 — init_test_project should have created the archive "
        f"at {expected}, beside the project and inside the pytest temp root."
    )

    after = sorted(p.name for p in home_archives.iterdir()) if home_archives.is_dir() else []
    added = [name for name in after if name not in set(before)]
    assert not added, (
        "SUITE INVARIANT 4 — scaffolding a test project created "
        f"{home_archives / added[0]} and {len(added)} entr(y/ies) in the "
        f"developer's home directory: {added}. init_project's archive default "
        "is ~/.wfc/archives/<project>; a test must name one explicitly."
    )


# =============================================================================
# Invariant 5 — a fake is a registry entry, not a raw patch site
# =============================================================================

#: Attribute calls that install a fake through a MonkeyPatch. ``setenv`` /
#: ``delenv`` / ``chdir`` / ``setitem`` are environment plumbing, not fakes.
_MONKEYPATCH_FAKES = frozenset({"setattr", "delattr"})
#: Callee spellings of ``unittest.mock.patch`` and its variants.
_PATCH_CALLEES = frozenset({
    "patch", "patch.object", "patch.dict", "patch.multiple",
    "mock.patch", "mock.patch.object", "mock.patch.dict", "mock.patch.multiple",
    "unittest.mock.patch", "unittest.mock.patch.object",
    "unittest.mock.patch.dict", "unittest.mock.patch.multiple",
})
#: Mock constructors.
_MOCK_CLASSES = frozenset({"MagicMock", "Mock", "AsyncMock", "NonCallableMock"})
_MOCK_MODULES = frozenset({"mock", "unittest.mock"})


def _dotted(node: ast.expr) -> str | None:
    """Render a Name / Attribute chain as ``a.b.c``; ``None`` for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _scan_fake_sites(rel: str, tree: ast.Module) -> list[tuple[str, int, str]]:
    """Return every raw fake site one parsed module contains.

    Call nodes only, never text: the gate self-tests embed ``patch(``-shaped
    strings in synthetic sources, and a string is not a site. Four shapes --
    ``<anything>.setattr(`` / ``.delattr(`` (a MonkeyPatch), ``patch(`` and
    its ``mock.patch`` / ``patch.object`` spellings, and a mock constructor.
    An HTTP ``client.patch(...)`` is not ``unittest.mock.patch`` and is not
    matched.

    Args:
        rel: Repo-relative path, carried into each hit for the message.
        tree: The parsed module.

    Returns:
        ``(rel, line, detail)`` per detection, allowlist not applied.
    """
    hits: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        detail = None
        if isinstance(func, ast.Attribute) and func.attr in _MONKEYPATCH_FAKES:
            target = ", ".join(ast.unparse(a) for a in node.args[:2])
            detail = f".{func.attr}({target})"
        else:
            callee = _dotted(func)
            if callee in _PATCH_CALLEES:
                target = ast.unparse(node.args[0]) if node.args else ""
                detail = f"{callee}({target})"
            elif callee in _MOCK_CLASSES or (
                isinstance(func, ast.Attribute) and func.attr in _MOCK_CLASSES
                and _dotted(func.value) in _MOCK_MODULES
            ):
                detail = f"{callee}(...)"
        if detail is not None:
            hits.append((rel, node.lineno, detail))
    hits.sort(key=lambda h: h[1])
    return hits


def _sites_per_module() -> dict[str, list[tuple[str, int, str]]]:
    """Collect the raw fake sites of every test module outside the fakes package."""
    sites: dict[str, list[tuple[str, int, str]]] = {}
    for rel, tree in _test_modules():
        if rel.startswith("tests/fixtures/fakes/"):
            continue
        hits = _scan_fake_sites(rel, tree)
        if hits:
            sites[rel] = hits
    return sites


def _distinct_lines(hits: list[tuple[str, int, str]]) -> int:
    """Count the lines carrying at least one site (a nested mock is one line)."""
    return len({line for _, line, _ in hits})


def test_no_test_installs_a_raw_fake_outside_the_allowlist():
    """Raw patch sites per module may not exceed the allowlisted count."""
    sites = _sites_per_module()
    offenders: list[str] = []
    for rel, hits in sorted(sites.items()):
        measured = _distinct_lines(hits)
        allowed = FAKE_SITE_ALLOWLIST.get(rel, (0, ""))[0]
        if measured > allowed:
            offenders.append(
                f"  {rel}: {measured} line(s) with a raw fake, allowed {allowed}\n"
                + _format(hits)
            )
    assert not offenders, (
        "SUITE INVARIANT 5 — a test may not install a raw fake outside the "
        "registry.\n" + "\n".join(offenders) + "\n\n"
        "monkeypatch.setattr / patch( / mock.patch / MagicMock( outside "
        "tests/fixtures/fakes/ is a fake nobody declared. Install it through "
        "the registry entry for that boundary (tests.fixtures.fakes -- its "
        "__init__ docstring indexes the entries by boundary: the readiness "
        "probes, subprocess.run, the engine process, the docker registry "
        "calls, the transport, the server's provider binding, ...) or produce "
        "the state through tests.fixtures.routes. FAKE_SITE_ALLOWLIST only "
        "ever falls."
    )

    stale = []
    for rel, (allowed, _reason) in sorted(FAKE_SITE_ALLOWLIST.items()):
        measured = _distinct_lines(sites.get(rel, []))
        if measured < allowed:
            stale.append(f"{rel}: allowed {allowed}, measured {measured}")
    assert not stale, (
        "SUITE INVARIANT 5 — these allowlist entries are above the measured "
        f"count; lower each to its measured count (or remove it at zero) in "
        f"this change, since the count only ever falls: {stale}"
    )


#: A raw site of each shape, plus an HTTP client.patch and a setenv that
#: must not count, and a string that looks like a patch call.
_SYNTHETIC_FAKE_SITES = (
    "def test_x(monkeypatch, client):\n"
    "    monkeypatch.setenv('X', '1')\n"
    "    monkeypatch.setattr(readiness, 'check_git', lambda: None)\n"
    "    with patch('subprocess.Popen', return_value=MagicMock()):\n"
    "        pass\n"
    "    with mock.patch.object(os, 'rename'):\n"
    "        pass\n"
    "    client.patch('/api/wfc/run/1', json={})\n"
    "    src = \"with patch('x'):\"\n"
)


def test_invariant_5_scanner_detects_a_fake_site_it_is_shown():
    """The invariant-5 scan body flags each raw shape and nothing else."""
    hits = _scan_fake_sites("tests/_synthetic_fakes.py",
                            ast.parse(_SYNTHETIC_FAKE_SITES))
    assert [line for _, line, _ in hits] == [3, 4, 4, 6], (
        "SUITE INVARIANT 5 (self-test) — the scan body must flag a "
        "monkeypatch.setattr, a patch( with its MagicMock( on the same line, "
        "and a mock.patch.object( -- and must not flag setenv, an HTTP "
        f"client.patch or a string. Detections: {hits}."
    )
    assert _distinct_lines(hits) == 3


def test_invariant_5_flags_a_stale_allowlist_entry():
    """An allowlist count above the measured one is a stale entry, not slack."""
    sites = _sites_per_module()
    some_module = next(iter(FAKE_SITE_ALLOWLIST))
    measured = _distinct_lines(sites.get(some_module, []))
    assert measured == FAKE_SITE_ALLOWLIST[some_module][0], (
        f"{some_module} is allowlisted at {FAKE_SITE_ALLOWLIST[some_module][0]} "
        f"but measures {measured}; the gate above should have failed"
    )
    # The stale arm is the second assertion of the gate; drive it directly
    # with a count one above the measurement.
    inflated = {**FAKE_SITE_ALLOWLIST, some_module: (measured + 1, "inflated")}
    stale = [rel for rel, (allowed, _) in inflated.items()
             if _distinct_lines(sites.get(rel, [])) < allowed]
    assert stale == [some_module]


# =============================================================================
# The registry — every entry exported, every backing witness resolving
# =============================================================================

#: Committed number of registry entries whose backing witness is ``owed``.
#: Only ever decreases: an entry whose boundary a new test drives unfaked
#: names that test and lowers the count in the same change. ``undrivable``
#: entries are declared, not counted.
OWED_BACKING_WITNESSES = 0


def _registry_entries() -> list[tuple[str, str, object]]:
    """Return ``(module name, entry name, entry)`` for every entry in the package."""
    import importlib
    import pkgutil

    import tests.fixtures.fakes as fakes
    from tests.fixtures.fakes._entry import is_entry

    found: list[tuple[str, str, object]] = []
    for info in pkgutil.iter_modules(fakes.__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"tests.fixtures.fakes.{info.name}")
        for name, obj in vars(module).items():
            if is_entry(obj) and getattr(obj, "__module__", "") == module.__name__:
                found.append((info.name, name, obj))
    return found


def test_every_registry_entry_is_exported_from_the_package():
    """An entry cannot hide in a submodule nobody imports."""
    import tests.fixtures.fakes as fakes

    missing = [f"{module}.{name}" for module, name, obj in _registry_entries()
               if name not in fakes.__all__ or getattr(fakes, name, None) is not obj]
    assert not missing, (
        "SUITE INVARIANT 7 — these registry entries are defined in a submodule "
        f"but not exported from tests/fixtures/fakes/__init__.py: {missing}. "
        "Add each to the import block and __all__, and to the docstring index."
    )
    assert _registry_entries(), "the registry harvest found no entries at all"


def _check_backing(entries, repo_root: Path) -> tuple[list[str], int]:
    """Classify every entry's ``backed_by``.

    Args:
        entries: ``(module, name, entry)`` triples.
        repo_root: Repository root to resolve node ids against.

    Returns:
        ``(problems, owed)``: the entries whose node id does not resolve to
        a test, and the number carrying ``owed``.
    """
    from tools.check_catalog_coverage import resolve_node_id

    problems: list[str] = []
    owed = 0
    cache: dict = {}
    for module, name, entry in entries:
        backed_by = entry.backed_by
        if backed_by.startswith("owed:"):
            owed += 1
            if not backed_by[len("owed:"):].strip():
                problems.append(f"{module}.{name}: 'owed:' needs a reason")
            continue
        if backed_by.startswith("undrivable:"):
            if not backed_by[len("undrivable:"):].strip():
                problems.append(f"{module}.{name}: 'undrivable:' needs a reason")
            continue
        link = resolve_node_id(repo_root, backed_by, cache)
        if not link.resolved:
            problems.append(f"{module}.{name}: {backed_by} -- {link.problem}")
        elif not link.is_witness:
            problems.append(f"{module}.{name}: {backed_by} is not a test")
    return problems, owed


def test_every_backing_witness_resolves():
    """A ``backed_by`` names a test that exists, or says why none does."""
    problems, owed = _check_backing(_registry_entries(), REPO_ROOT)
    assert not problems, (
        "SUITE INVARIANT 7 — a registry entry's backed_by must be a node id "
        "that resolves to a test, 'owed: <reason>' or 'undrivable: <reason>':\n  "
        + "\n  ".join(problems)
    )
    assert owed <= OWED_BACKING_WITNESSES, (
        f"SUITE INVARIANT 7 — {owed} entries are owed a backing witness, "
        f"committed maximum is {OWED_BACKING_WITNESSES}; write the witness "
        "rather than raising the constant"
    )
    assert owed == OWED_BACKING_WITNESSES, (
        f"SUITE INVARIANT 7 — OWED_BACKING_WITNESSES is {OWED_BACKING_WITNESSES} "
        f"but only {owed} entries are owed; lower the constant in this change "
        "(it only ever decreases)"
    )


def test_backing_witness_check_classifies_what_it_is_shown():
    """The backing check fails a missing symbol, counts owed, skips undrivable."""
    from types import SimpleNamespace as NS

    def entry(backed_by):
        return NS(backed_by=backed_by)

    problems, owed = _check_backing([
        ("m", "missing", entry("pm_mvp::tests.test_suite_invariants::no_such_test")),
        ("m", "prod", entry("pm_mvp::tools.check_catalog_coverage::resolve_node_id")),
        ("m", "owed", entry("owed: nobody drives it")),
        ("m", "bare_owed", entry("owed:")),
        ("m", "free", entry("undrivable: the clock")),
        ("m", "real", entry("pm_mvp::tests.test_suite_invariants::"
                            "test_every_backing_witness_resolves")),
    ], REPO_ROOT)
    assert owed == 2
    assert [p.split(":")[0] for p in problems] == ["m.missing", "m.prod", "m.bare_owed"], problems


def test_the_ast_harvest_and_the_runtime_harvest_agree():
    """The walk's source-level harvest of the package is the package as imported.

    ``tools/witness_walk.py`` reads the entries by AST, without importing the
    suite; the invariants here import the package. Both must see the same
    names with the same kind and four fields, or a disclosure could name an
    entry the suite does not have, or miss one it does.
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "witness_walk", REPO_ROOT / "tools" / "witness_walk.py")
    walk = importlib.util.module_from_spec(spec)
    # Registered before execution: the module's dataclasses resolve their
    # postponed annotations through ``sys.modules[__name__]``.
    sys.modules["witness_walk"] = walk
    spec.loader.exec_module(walk)

    harvested = walk.harvest_registry(REPO_ROOT)
    assert harvested.problems == []
    by_ast = {
        name: (entry.kind, entry.rel, entry.boundary, entry.preserves,
               entry.not_proven, entry.backed_by)
        for name, entry in harvested.entries.items()
    }
    by_runtime = {
        name: (obj.entry_kind, f"tests/fixtures/fakes/{module}.py",
               obj.boundary, obj.preserves, obj.not_proven, obj.backed_by)
        for module, name, obj in _registry_entries()
    }
    assert by_ast == by_runtime
