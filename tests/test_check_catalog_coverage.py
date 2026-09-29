"""Behavior tests for the catalog-coverage gate (tools/check_catalog_coverage.py).

Two groups.  The first covers the gate's base contract: which sections are
its subject, which cases count, and the ``MAX_UNPROVEN_CASES`` ratchet.  The
second covers the two teeth that decide what a *witness* is — a link must
resolve against the source tree, and it must be a test.

Both drive the gate over synthetic trees in ``tmp_path``, following the
``tests/test_check_layering.py`` precedent.  Note that a synthetic tree now
needs real source files as well as documents: a link is proof only if it
resolves, so a case is proven only when the file and symbol it cites exist.

**Deliberately no real-repo arm for the resolution tooth.**  The real
repository's links are checked by running the gate itself; a test pinning a
particular offender in the live tree would be a hardcoded-site oracle.

Two boundaries the resolution tests draw on purpose, because they read as
inconsistent otherwise:

* a link to a module with **no file** loses its path, so it is both unresolved
  *and* no longer a witness;
* a link to a **deleted symbol in a surviving test module** keeps its path, so
  the resolution tooth carries the defect alone and the case still counts as
  witnessed.  The witness question is *"is there a test behind this case?"* and
  is answered by the path; the resolution question is *"does this citation
  point at something real?"* and is answered by the symbol.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from axiom_annotations import workflow

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "check_catalog_coverage", REPO_ROOT / "tools" / "check_catalog_coverage.py"
)
check_catalog_coverage = importlib.util.module_from_spec(_spec)
sys.modules["check_catalog_coverage"] = check_catalog_coverage
_spec.loader.exec_module(check_catalog_coverage)


def _write(root: Path, rel: str, source: str) -> None:
    """Write one source file into a synthetic tree.

    Args:
        root: Repository root to write under.
        rel: Path relative to that root.
        source: File contents.
    """
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _write_doc(root: Path, name: str, sections: list) -> None:
    """Write one synthetic DocJSON system document.

    Args:
        root: Repository root to write under.
        name: Document path relative to ``docs/system``, without the ``.json``
            suffix.  May name a subdirectory (``cli/catalog``) to write a unit
            document split across a directory.
        sections: The document's top-level sections.
    """
    path = root / "docs" / "system" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"id": name, "title": name, "sections": sections}),
        encoding="utf-8",
    )


def _case(case_id: str, links: list | None) -> dict:
    """One catalog case section.

    Args:
        case_id: The case's section id.
        links: Its witness links, or ``None`` for the absent-key shape.

    Returns:
        The section object.
    """
    # A witnessed synthetic case reaches nothing, so its generated bullet is
    # ``none``; an unwitnessed one derives ``no witness`` and is reported stale
    # beside its unproven line, which is the live tree's behaviour too.
    section = {"id": case_id, "heading": case_id,
               "content": "- Given: ...\n- Test: t.\n- Stub disclosure: none"}
    if links is not None:
        section["links"] = links
    return section


def _catalog(*cases: dict) -> dict:
    """A ``catalog`` section holding ``cases``."""
    return {"id": "catalog", "sections": list(cases)}


def _links(*node_ids: str) -> list:
    """The links list for ``node_ids``."""
    return [{"node_id": node_id} for node_id in node_ids]


def _a_test_module(root: Path) -> None:
    """Write the module the original tests' placeholder links point at."""
    _write(root, "tests/t.py", "def test_a():\n    pass\n\n\ndef test_b():\n    pass\n")


def _unresolved(report) -> list[tuple[str, str]]:
    """``(case id, node id)`` for every link that does not resolve."""
    return [(case.qualified_id, link.node_id) for case, link in report.unresolved_links]


# -----------------------------------------------------------------------------
# The subject, the count, and the ratchet
# -----------------------------------------------------------------------------

@workflow(
    purpose="Every catalog case in the repository's real docs/system documents "
            "carries a test witness and the ratchet is satisfied: fail-closed, "
            "but the committed tree's coverage is legal (Tier 2).",
)
def test_current_tree_passes():
    """Every catalog case in a document with no migration section carries a witness.

    A document still carrying a migration section may hold to-build cases;
    they are reported, not counted.  When this fails the remedy is a link
    or a proof, never a higher constant.

    Scope note: this arm asserts *coverage*, not resolution.  It deliberately
    does not assert ``report.unresolved_links == []``: naming a specific
    offender here would be a hardcoded-site oracle.  Resolution is witnessed
    against synthetic trees below.
    """
    report = check_catalog_coverage.check_tree(REPO_ROOT)

    assert [c.qualified_id for c in report.unproven] == [], (
        "every catalog case must carry a witness link; add the link rather "
        "than raising MAX_UNPROVEN_CASES"
    )
    assert report.ratchet_errors == []
    assert report.stale_disclosures == [], (
        "every case's Stub disclosure bullet is generated; rerun the write mode "
        "rather than editing one"
    )
    assert len(report.proven) > 0


@workflow(
    purpose="A catalog case whose links list is absent or empty is reported "
            "by id and fails the gate (Tier 2).",
)
def test_unwitnessed_case_is_named_and_fails(tmp_path, monkeypatch, capsys):
    """Both the empty-list and absent-key shapes count as unproven."""
    _a_test_module(tmp_path)
    _write_doc(tmp_path, "widget", [
        _catalog(
            _case("proven-case", _links("pm_mvp::tests.t::test_a")),
            _case("empty-links-case", []),
            _case("no-links-key-case", None),
        ),
    ])

    report = check_catalog_coverage.check_tree(tmp_path)
    assert [c.qualified_id for c in report.unproven] == [
        "widget::catalog.empty-links-case",
        "widget::catalog.no-links-key-case",
    ]

    # The gate as a caller runs it: non-zero exit, offending ids in the output.
    monkeypatch.setattr(check_catalog_coverage, "REPO_ROOT", tmp_path)
    exit_code = check_catalog_coverage.main([])
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "widget::catalog.empty-links-case" in out
    assert "widget::catalog.no-links-key-case" in out
    assert "widget::catalog.proven-case" not in out


@workflow(
    purpose="The committed maximum ratchets in one direction: too many "
            "unproven cases fails, and slack in the constant fails too, so "
            "proving a case and lowering the number happen together (Tier 2).",
)
def test_ratchet_teeth(tmp_path):
    """Both teeth, and the passing state in between."""
    _a_test_module(tmp_path)
    _write_doc(tmp_path, "widget", [
        _catalog(
            _case("proven-case", _links("pm_mvp::tests.t::test_a")),
            _case("unproven-case", []),
        ),
    ])

    over_max = check_catalog_coverage.check_tree(tmp_path, max_unproven=0)
    assert any("committed maximum is 0" in err for err in over_max.ratchet_errors)

    allowed = check_catalog_coverage.check_tree(tmp_path, max_unproven=1)
    assert allowed.ratchet_errors == []

    # The case is proven; leaving the constant at 1 is slack the ratchet
    # refuses, so the same change that adds the witness lowers the number.
    _write_doc(tmp_path, "widget", [
        _catalog(
            _case("proven-case", _links("pm_mvp::tests.t::test_a")),
            _case("unproven-case", _links("pm_mvp::tests.t::test_b")),
        ),
    ])
    slack = check_catalog_coverage.check_tree(tmp_path, max_unproven=1)
    assert any("lower the constant to 0" in err for err in slack.ratchet_errors)

    lowered = check_catalog_coverage.check_tree(tmp_path, max_unproven=0)
    assert lowered.ratchet_errors == []
    assert lowered.unproven == []


@workflow(
    purpose="A document still carrying a migration section may hold "
            "unwitnessed catalog cases: they are reported as to-build and not "
            "counted; deleting the section makes the same cases count (Tier 2).",
)
def test_migration_section_defers_unwitnessed_cases(tmp_path, monkeypatch, capsys):
    """The document's own state decides, with no constant and no list."""
    _a_test_module(tmp_path)
    # An unwitnessed case's honest generated bullet, so the walk agrees with it.
    to_build = _case("to-build-case", [])
    to_build["content"] = "- Given: ...\n- Test: t.\n- Stub disclosure: no witness"
    catalog = _catalog(
        _case("proven-case", _links("pm_mvp::tests.t::test_a")),
        to_build,
    )
    migration = {"id": "migration", "heading": "Migration (self-deleting)",
                 "content": "what moves in"}

    _write_doc(tmp_path, "widget", [catalog, migration])
    deferred = check_catalog_coverage.check_tree(tmp_path)
    assert deferred.unproven == []
    assert [c.qualified_id for c in deferred.to_build] == ["widget::catalog.to-build-case"]
    assert deferred.ratchet_errors == []

    # ``main`` enforces the case census, whose committed count is a fact about
    # the real repository. A test that substitutes the tree substitutes it too.
    monkeypatch.setattr(check_catalog_coverage, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_catalog_coverage, "EXPECTED_CASES", 2)
    monkeypatch.setattr(check_catalog_coverage, "MAX_WIDE_CASES", 0)
    assert check_catalog_coverage.main([]) == 0
    assert "TO-BUILD: widget::catalog.to-build-case" in capsys.readouterr().out

    # With the migration section deleted, the same case counts.
    _write_doc(tmp_path, "widget", [catalog])
    strict = check_catalog_coverage.check_tree(tmp_path)
    assert [c.qualified_id for c in strict.unproven] == ["widget::catalog.to-build-case"]
    assert strict.to_build == []
    assert check_catalog_coverage.main([]) == 1


def test_only_catalog_cases_are_the_subject(tmp_path):
    """Sections outside ``catalog`` never count, however they are linked.

    The gate's subject is catalog case sections.  ``testing.requirements``
    entries are per-row prose and the ``catalog`` container itself carries
    no links by design; counting either would make the number meaningless.
    """
    _a_test_module(tmp_path)
    _write_doc(tmp_path, "widget", [
        {"id": "scope", "links": []},
        _catalog(_case("proven-case", _links("pm_mvp::tests.t::test_a"))),
        {"id": "testing", "sections": [
            {"id": "requirements", "heading": "Requirements", "links": []},
        ]},
    ])

    report = check_catalog_coverage.check_tree(tmp_path)

    assert [c.qualified_id for c in report.cases] == ["widget::catalog.proven-case"]
    assert report.unproven == []
    assert report.ratchet_errors == []


# -----------------------------------------------------------------------------
# Tooth (a) -- a link that does not resolve proves nothing
# -----------------------------------------------------------------------------

@workflow(purpose="A catalog link whose module has no file in the source tree is "
                  "reported unresolved naming the module path, and — having no "
                  "path at all — stops counting as the case's witness (Tier 2).")
def test_a_link_to_a_module_with_no_file_is_unresolved(tmp_path):
    _write(tmp_path, "tests/test_live.py", "def test_live():\n    pass\n")
    _write_doc(tmp_path, "storage", [
        _catalog(
            _case("kept", _links("pm_mvp::tests.test_live::test_live")),
            _case("moved", _links("pm_mvp::tests.test_deleted_module::test_gone")),
        ),
    ])

    report = check_catalog_coverage.check_tree(tmp_path)

    assert _unresolved(report) == [
        ("storage::catalog.moved", "pm_mvp::tests.test_deleted_module::test_gone")
    ]
    assert (
        "no source file for module path 'tests.test_deleted_module'"
        in report.unresolved_links[0][1].problem
    )
    assert [c.qualified_id for c in report.unproven] == ["storage::catalog.moved"]


@workflow(purpose="Resolution reaches the symbol, not just the file: a citation "
                  "to a function deleted from a module that still exists is "
                  "unresolved, while its sibling in the same file is not (Tier 2).")
def test_a_deleted_symbol_in_a_surviving_module_is_unresolved(tmp_path):
    # The shape a refactor leaves behind, and the one the index cannot see:
    # the deleted function survives there as a ghost node.
    _write(
        tmp_path,
        "tests/test_samples.py",
        "def test_a_sample_row_with_no_content_hash_is_refused():\n    pass\n",
    )
    _write_doc(tmp_path, "storage", [
        _catalog(
            _case(
                "sample-restore",
                _links(
                    "pm_mvp::tests.test_samples::"
                    "test_a_sample_row_with_no_content_hash_is_refused",
                    "pm_mvp::tests.test_samples::test_restore_sample_null_hash_errors",
                ),
            ),
        ),
    ])

    report = check_catalog_coverage.check_tree(tmp_path)

    assert _unresolved(report) == [
        (
            "storage::catalog.sample-restore",
            "pm_mvp::tests.test_samples::test_restore_sample_null_hash_errors",
        )
    ]
    assert (
        report.unresolved_links[0][1].problem
        == "'test_restore_sample_null_hash_errors' is not defined in tests/test_samples.py"
    )
    # The surviving sibling still points at a real test, so the case keeps a
    # witness and the resolution tooth carries the defect on its own.
    assert report.unproven == []


@workflow(purpose="A Class.method node id resolves when both halves exist, and "
                  "fails when either the member or the class is missing — the "
                  "nested form is neither a false positive nor a false alarm (Tier 2).")
def test_a_class_method_link_checks_both_the_class_and_the_member(tmp_path):
    _write(
        tmp_path,
        "tests/test_health.py",
        "class SampleHealth:\n"
        "    reachable = True\n"
        "\n"
        "    def classify(self):\n"
        "        return self.reachable\n",
    )
    _write_doc(tmp_path, "registration", [
        _catalog(
            _case("method", _links("pm_mvp::tests.test_health::SampleHealth.classify")),
            _case("attribute", _links("pm_mvp::tests.test_health::SampleHealth.reachable")),
            _case("gone-member", _links("pm_mvp::tests.test_health::SampleHealth.preflight")),
            _case("gone-class", _links("pm_mvp::tests.test_health::Missing.classify")),
        ),
    ])

    report = check_catalog_coverage.check_tree(tmp_path)

    assert _unresolved(report) == [
        (
            "registration::catalog.gone-member",
            "pm_mvp::tests.test_health::SampleHealth.preflight",
        ),
        (
            "registration::catalog.gone-class",
            "pm_mvp::tests.test_health::Missing.classify",
        ),
    ]


# -----------------------------------------------------------------------------
# Tooth (b) -- only a test counts as a witness
# -----------------------------------------------------------------------------

@workflow(purpose="A case linked only to the production code it describes is "
                  "unproven: production links resolve, but only a test can "
                  "falsify the claim a case makes (Tier 2).")
def test_a_case_witnessed_only_by_production_code_is_unproven(tmp_path):
    _write(
        tmp_path,
        "wfc/storage/resolve.py",
        "def has_malformed_output_records():\n    return False\n",
    )
    _write(tmp_path, "tests/test_resolve.py", "def test_malformed_rows():\n    pass\n")
    _write_doc(tmp_path, "storage", [
        _catalog(
            _case(
                "self-proving",
                _links("pm_mvp::wfc.storage.resolve::has_malformed_output_records"),
            ),
            _case(
                "witnessed",
                _links(
                    "pm_mvp::wfc.storage.resolve::has_malformed_output_records",
                    "pm_mvp::tests.test_resolve::test_malformed_rows",
                ),
            ),
        ),
    ])

    report = check_catalog_coverage.check_tree(tmp_path)

    # The production link is perfectly resolvable — it is simply not proof.
    assert report.unresolved_links == []
    assert [c.qualified_id for c in report.unproven] == ["storage::catalog.self-proving"]
    assert {
        c.qualified_id: (len(c.witnesses), len(c.links)) for c in report.proven
    } == {"storage::catalog.witnessed": (1, 2)}


@workflow(purpose="A case witnessed entirely by wfc-client/tests/** counts as "
                  "proven, though its node ids carry no tests prefix — the "
                  "witness predicate reads the path, not the namespace (Tier 2).")
def test_a_wfc_client_test_module_is_a_witness(tmp_path):
    # Seven cases in client-export are witnessed only from here.  A node-id
    # prefix rule reads `pm_mvp::wfc-client.tests.*` as production and calls
    # all seven unproven.
    _write(
        tmp_path,
        "wfc-client/tests/test_export.py",
        "def test_export_round_trip():\n    pass\n",
    )
    _write_doc(tmp_path, "client-export", [
        _catalog(
            _case(
                "bundle",
                _links("pm_mvp::wfc-client.tests.test_export::test_export_round_trip"),
            ),
        ),
    ])

    report = check_catalog_coverage.check_tree(tmp_path)

    assert report.unresolved_links == []
    assert report.unproven == []
    (link,) = report.cases[0].links
    assert link.rel_path == "wfc-client/tests/test_export.py"
    assert link.is_witness


@workflow(purpose="A case witnessed entirely by a canvas Vitest module counts "
                  "as proven, and its dotted filename resolves — the trailing "
                  "dots name compile.variables.test.ts, not two directories (Tier 2).")
def test_a_canvas_vitest_module_is_a_witness(tmp_path):
    # ~30 cases across canvas-ui and lineage are witnessed only from here, by
    # module-level ids with no `tests` segment anywhere.
    _write(
        tmp_path,
        "wfc/canvas/static/src/lib/builder/__tests__/compile.variables.test.ts",
        "import { describe, it } from 'vitest'\n",
    )
    _write_doc(tmp_path, "canvas-ui", [
        _catalog(
            _case(
                "variable-compile",
                _links(
                    "pm_mvp::wfc.canvas.static.src.lib.builder."
                    "__tests__.compile.variables.test"
                ),
            ),
        ),
    ])

    report = check_catalog_coverage.check_tree(tmp_path)

    assert report.unresolved_links == []
    assert report.unproven == []
    (link,) = report.cases[0].links
    assert (
        link.rel_path
        == "wfc/canvas/static/src/lib/builder/__tests__/compile.variables.test.ts"
    )
    assert link.is_witness


@workflow(purpose="The gate exits 1 on a tree carrying an unresolved link and 0 "
                  "on a clean one, printing the offending citation so the "
                  "report is the repair worklist rather than a bare count (Tier 2).")
def test_main_exits_1_on_an_unresolved_link_and_0_on_a_clean_tree(
    tmp_path, monkeypatch, capsys
):
    red, green = tmp_path / "red", tmp_path / "green"
    for root in (red, green):
        _write(root, "tests/test_live.py", "def test_live():\n    pass\n")
    _write_doc(red, "storage", [
        _catalog(
            _case(
                "sample-restore",
                _links(
                    "pm_mvp::tests.test_live::test_live",
                    "pm_mvp::tests.test_live::test_gone",
                ),
            ),
        ),
    ])
    green_case = _case("sample-restore", _links("pm_mvp::tests.test_live::test_live"))
    green_case["content"] = "- Given: ...\n- Test: t.\n- Stub disclosure: none"
    _write_doc(green, "storage", [_catalog(green_case)])

    # Both trees hold one case; the census constant follows the substituted tree.
    monkeypatch.setattr(check_catalog_coverage, "EXPECTED_CASES", 1)
    monkeypatch.setattr(check_catalog_coverage, "MAX_WIDE_CASES", 0)
    monkeypatch.setattr(check_catalog_coverage, "REPO_ROOT", red)
    assert check_catalog_coverage.main([]) == 1
    out = capsys.readouterr().out
    assert "UNRESOLVED: storage::catalog.sample-restore" in out
    assert "pm_mvp::tests.test_live::test_gone" in out
    assert "1 unresolved link(s)" in out

    monkeypatch.setattr(check_catalog_coverage, "REPO_ROOT", green)
    assert check_catalog_coverage.main([]) == 0
    assert "0 unproven, 0 over-wide, 0 to-build, of 1 case(s)" in capsys.readouterr().out


# -----------------------------------------------------------------------------
# Scan depth and the case census
# -----------------------------------------------------------------------------

@workflow(
    purpose="A unit document split across a directory is scanned exactly like "
            "a top-level one, and two units' same-named files stay distinct, so "
            "moving a unit into a directory cannot hide its cases (Tier 2).",
)
def test_a_split_unit_is_scanned_like_a_top_level_one(tmp_path):
    """Depth does not decide whether a case is seen; only its content does.

    The gate scanned one directory level until the Tier 2 split needed
    ``docs/system/<unit>/catalog.json``.  A single-level scan reports a moved
    unit's cases as absent rather than as an error — every other tooth stays
    satisfied — so this is what makes the split safe to perform at all.

    The two nested documents deliberately share a filename and a case id.  A
    doc name taken from the file stem would collapse them onto one qualified
    id, which is how a real split of four units into four ``catalog.json``
    files would lose cases without the count changing.
    """
    _a_test_module(tmp_path)
    _write_doc(tmp_path, "layout", [
        _catalog(_case("flat", _links("pm_mvp::tests.t::test_a"))),
    ])
    _write_doc(tmp_path, "cli/catalog", [
        _catalog(_case("nested", _links("pm_mvp::tests.t::test_a"))),
    ])
    _write_doc(tmp_path, "canvas-api/catalog", [
        _catalog(_case("nested", _links("pm_mvp::tests.t::test_b"))),
    ])

    report = check_catalog_coverage.check_tree(tmp_path)

    assert sorted(c.qualified_id for c in report.cases) == [
        "canvas-api/catalog::catalog.nested",
        "cli/catalog::catalog.nested",
        "layout::catalog.flat",
    ]
    assert report.unproven == []
    assert report.ratchet_errors == []


@workflow(
    purpose="The case census fails in both directions, so a case cannot "
            "disappear — the failure a directory move introduces — or appear "
            "without the committed count moving in the same change (Tier 2).",
)
def test_the_case_census_fails_in_both_directions(tmp_path):
    """The ratchet asks whether the cases found are proven; the census asks
    whether they were found.

    A document that stops being scanned takes its cases with it and satisfies
    every other tooth on the way out: zero unproven, zero unresolved, exit 0.
    The count is the only thing that can notice.
    """
    _a_test_module(tmp_path)
    _write_doc(tmp_path, "widget", [
        _catalog(
            _case("one", _links("pm_mvp::tests.t::test_a")),
            _case("two", _links("pm_mvp::tests.t::test_b")),
        ),
    ])

    assert check_catalog_coverage.check_tree(
        tmp_path, expected_cases=2
    ).ratchet_errors == []

    missing = check_catalog_coverage.check_tree(tmp_path, expected_cases=3)
    assert len(missing.ratchet_errors) == 1
    assert "found 2 catalog case(s), EXPECTED_CASES is 3" in missing.ratchet_errors[0]
    assert "no longer being scanned" in missing.ratchet_errors[0]

    extra = check_catalog_coverage.check_tree(tmp_path, expected_cases=1)
    assert len(extra.ratchet_errors) == 1
    assert "a case was added" in extra.ratchet_errors[0]

    # Left unset the census does not apply: the committed count is a fact about
    # the repository, and a synthetic tree has none.
    assert check_catalog_coverage.check_tree(tmp_path).ratchet_errors == []


@workflow(
    purpose="A unit's own catalog.json is read as the catalog itself: its "
            "top-level sections are families, the leaves beneath them are the "
            "cases, and a family is not counted as a case (Tier 2).",
)
def test_a_unit_catalog_document_groups_cases_under_families(tmp_path):
    """The shape the graph viewer's two-level nesting requires.

    A flat unit document nests ``catalog`` -> case, so grouping those cases by
    family would need a third level.  A split unit promotes the catalog to its
    own document instead: the document *is* the catalog, its sections are
    families, and the cases are the leaves below them.

    Both shapes reach the same count — a case is a leaf either way — which is
    what lets the census verify that a split moved cases rather than losing
    them.  Five sections are written here and three are cases; a rule that
    counted a family would report five.
    """
    _a_test_module(tmp_path)
    _write_doc(tmp_path, "cli/catalog", [
        {"id": "root-resolution", "heading": "Root resolution", "sections": [
            _case("upward-walk", _links("pm_mvp::tests.t::test_a")),
            _case("env-override", _links("pm_mvp::tests.t::test_b")),
        ]},
        {"id": "registration", "heading": "Registration", "sections": [
            _case("copies-source", _links("pm_mvp::tests.t::test_a")),
        ]},
    ])

    report = check_catalog_coverage.check_tree(tmp_path, expected_cases=3)

    assert sorted(c.qualified_id for c in report.cases) == [
        "cli/catalog::registration.copies-source",
        "cli/catalog::root-resolution.env-override",
        "cli/catalog::root-resolution.upward-walk",
    ]
    assert report.unproven == []
    assert report.ratchet_errors == []


@workflow(purpose="A case accruing a fourth witness is reported as over-wide, "
                  "and the maximum ratchets in both directions -- a case that "
                  "keeps needing proofs has stopped being one claim (Tier 2).")
def test_over_wide_cases_ratchet(tmp_path):
    """The brake on case width, which was prose-only until now.

    The witness rule has always said a case needing a fourth link is two
    cases.  Nothing counted, so nine drifted past it in the real tree.  Like
    the unproven maximum this ratchets both ways, so the debt cannot grow and
    cannot be quietly re-inflated after a split lowers it.
    """
    _write(tmp_path, "tests/t.py", "".join(
        f"def test_{n}():\n    pass\n\n\n" for n in "abcde"))
    _write_doc(tmp_path, "execution", [
        _catalog(
            _case("narrow", _links("pm_mvp::tests.t::test_a")),
            _case("wide", _links(*(f"pm_mvp::tests.t::test_{n}" for n in "abcd"))),
        ),
    ])

    # Four witnesses is one past the bound, and only that case is named.
    assert [c.qualified_id for c in check_catalog_coverage.check_tree(tmp_path).wide] \
        == ["execution::catalog.wide"]
    # Omitted, the tooth is silent -- a synthetic tree is not the repository.
    assert check_catalog_coverage.check_tree(tmp_path).ratchet_errors == []

    over = check_catalog_coverage.check_tree(tmp_path, max_wide=0)
    assert any("carry more than 3 witnesses" in e for e in over.ratchet_errors)

    assert check_catalog_coverage.check_tree(tmp_path, max_wide=1).ratchet_errors == []

    slack = check_catalog_coverage.check_tree(tmp_path, max_wide=4)
    assert any("lower the constant to 1" in e for e in slack.ratchet_errors)


@workflow(purpose="A link to production code alongside real witnesses is "
                  "refused at a committed maximum of zero: a case's links are "
                  "its proofs, and there is no count to burn down (Tier 2).")
def test_production_links_on_a_case_are_refused(tmp_path):
    """Why this one does not ratchet like the other two.

    An unproven case may be legitimate debt and an over-wide case is a split
    waiting to happen, so both carry a maximum that decreases.  A case citing
    the code it describes is never legitimate -- it inflates the apparent
    proof count with links that prove nothing -- so the maximum is zero and
    stays there.
    """
    _write(tmp_path, "wfc/cli.py", "def build_parser():\n    return None\n")
    _write(tmp_path, "tests/t.py", "def test_a():\n    pass\n")
    _write_doc(tmp_path, "cli", [
        _catalog(
            _case("clean", _links("pm_mvp::tests.t::test_a")),
            _case("self-citing", _links("pm_mvp::tests.t::test_a",
                                        "pm_mvp::wfc.cli::build_parser")),
        ),
    ])

    report = check_catalog_coverage.check_tree(tmp_path)

    # It resolves and it is proven -- the objection is the link, not the case.
    assert report.unresolved_links == []
    assert report.unproven == []
    assert [c.qualified_id for c in report.self_citing] == ["cli::catalog.self-citing"]
    assert any("resolve to production code" in e for e in report.ratchet_errors)


@workflow(purpose="A top-level `intro` section is the catalog's own prose, not "
                  "a case; a nested one is still a case, so a family cannot "
                  "hide a case by naming it intro (Tier 2).")
def test_a_top_level_intro_is_prose_and_a_nested_one_is_not(tmp_path):
    """The one named exemption in the leaf rule, and why it is safe.

    A split unit's `catalog.json` *is* the catalog, so it has no container
    section to hold an intro, and a leaf section is otherwise a case by
    definition -- the intro would count as one and could never be proven.
    The exemption is top-level only, and at the top level the census is the
    guard: renaming a case to `intro` drops the total and fails the gate.
    """
    _write(tmp_path, "tests/t.py", "def test_a():\n    pass\n")
    _write_doc(tmp_path, "graph/catalog", [
        {"id": "intro", "heading": "About", "content": "How these cases are built."},
        {"id": "parse", "heading": "Parse", "content": "What parsing means.",
         "sections": [
             _case("node-kinds", _links("pm_mvp::tests.t::test_a")),
             _case("intro", _links("pm_mvp::tests.t::test_a")),
         ]},
    ])

    report = check_catalog_coverage.check_tree(tmp_path)

    assert sorted(c.qualified_id for c in report.cases) == [
        "graph/catalog::parse.intro",
        "graph/catalog::parse.node-kinds",
    ]
    # The prose carries no witness and is not reported as unproven, because it
    # is not a case at all.
    assert report.unproven == []


# -----------------------------------------------------------------------------
# The generated Stub disclosure bullet: compare against zero, and the write mode
# -----------------------------------------------------------------------------

def _disclosure_tree(root: Path) -> None:
    """One witness that reaches nothing, one stale bullet and one missing."""
    _a_test_module(root)
    stale = _case("stale", _links("pm_mvp::tests.t::test_a"))
    stale["content"] = "- Given: g.\n- Test: t.\n- Stub disclosure: hand-written prose."
    missing = _case("missing", _links("pm_mvp::tests.t::test_b"))
    missing["content"] = "- Given: g.\n- Test: t.\n- Falsifier: f."
    _write_doc(root, "storage", [_catalog(stale, missing)])


@workflow(
    purpose="A stale or missing Stub disclosure bullet fails the gate against "
            "zero, naming the row and the rerun advice; the write mode rewrites "
            "exactly those bullets; the gate then passes and a second write is "
            "byte-identical (Tier 2).",
)
def test_stale_or_missing_disclosures_fail_until_the_write_mode_runs(tmp_path):
    _disclosure_tree(tmp_path)
    advice = "rerun `python tools/check_catalog_coverage.py --write-disclosures`"

    report = check_catalog_coverage.check_tree(tmp_path)
    assert [s.split(":")[0] for s in report.stale_disclosures] == ["storage", "storage"]
    assert report.stale_disclosures[0].startswith("storage::catalog.stale: Stub disclosure differs")
    assert report.stale_disclosures[1].startswith("storage::catalog.missing: no Stub disclosure bullet")
    assert all(s.endswith(advice) for s in report.stale_disclosures)
    assert report.ratchet_errors == []

    changed = check_catalog_coverage.write_disclosures(tmp_path)
    assert changed == [
        ("storage::catalog.stale", "- Stub disclosure: hand-written prose.", "none"),
        ("storage::catalog.missing", None, "none"),
    ]
    written = (tmp_path / "docs" / "system" / "storage.json").read_bytes()
    doc = json.loads(written)
    contents = [s["content"] for s in doc["sections"][0]["sections"]]
    assert contents == [
        "- Given: g.\n- Test: t.\n- Stub disclosure: none",
        "- Given: g.\n- Test: t.\n- Stub disclosure: none\n- Falsifier: f.",
    ]
    assert check_catalog_coverage.check_tree(tmp_path).stale_disclosures == []

    assert check_catalog_coverage.write_disclosures(tmp_path) == []
    assert (tmp_path / "docs" / "system" / "storage.json").read_bytes() == written


@workflow(
    purpose="The write mode round-trips a document with the repository's "
            "formatting -- two-space indentation, ensure_ascii off, CRLF line "
            "endings -- so only the bullet lines differ and every other line is "
            "byte-identical (Tier 2).",
)
def test_write_mode_preserves_the_documents_formatting(tmp_path):
    _a_test_module(tmp_path)
    case = _case("dash", _links("pm_mvp::tests.t::test_a"))
    case["content"] = "- Given: an em dash — kept.\n- Test: t."
    payload = {"id": "storage", "title": "storage — unit", "sections": [_catalog(case)]}
    path = tmp_path / "docs" / "system" / "storage.json"
    path.parent.mkdir(parents=True)
    before = json.dumps(payload, indent=2, ensure_ascii=False).replace("\n", "\r\n")
    path.write_bytes(before.encode("utf-8"))

    check_catalog_coverage.write_disclosures(tmp_path)

    after = path.read_bytes().decode("utf-8")
    assert "\r\n" in after and "\n" not in after.replace("\r\n", "")
    assert "—" in after  # not escaped to \u2014
    old_lines, new_lines = before.split("\r\n"), after.split("\r\n")
    assert len(old_lines) == len(new_lines)
    differing = [(o, n) for o, n in zip(old_lines, new_lines) if o != n]
    assert len(differing) == 1
    assert differing[0][1].rstrip(",") == (
        '          "content": "- Given: an em dash — kept.\\n- Test: t.\\n- Stub disclosure: none"'
    )
