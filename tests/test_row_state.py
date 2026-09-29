"""Behavior tests for the row-state report (tools/row_state.py).

The report's claim is that a row's coverage is derived from what it links to,
never from prose, so it cannot drift from a status sentence nobody updated.
These drive it over synthetic trees in ``tmp_path``, following the
``tests/test_check_catalog_coverage.py`` precedent.

The report is not a gate: it always exits 0, and no test here asserts a
failure code.  What it must get right is the classification and the warnings,
because a planning instrument that miscounts is worse than none.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from axiom_annotations import workflow

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "row_state", REPO_ROOT / "tools" / "row_state.py"
)
row_state = importlib.util.module_from_spec(_spec)
sys.modules["row_state"] = row_state
_spec.loader.exec_module(row_state)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _doc(root: Path, rel: str, sections: list, tags: list | None = None) -> None:
    """Write one DocJSON document at ``rel`` under the tree root."""
    payload: dict = {"title": rel, "sections": sections}
    if tags is not None:
        payload["tags"] = tags
    _write(root, rel, json.dumps(payload))


def _sec(sid: str, links: list | None = None, tags: list | None = None,
         sections: list | None = None) -> dict:
    out: dict = {"id": sid, "heading": sid, "content": "- Given: ..."}
    if links is not None:
        out["links"] = [{"node_id": n} for n in links]
    if tags is not None:
        out["tags"] = tags
    if sections is not None:
        out["sections"] = sections
    return out


def _states(rows) -> dict[str, str]:
    return {r.row_id: r.coverage for r in rows}


def _warnings(rows) -> list[str]:
    return [w for r in rows for w in r.warnings]


def _run(tmp_path):
    return row_state.classify(row_state.collect(tmp_path), tmp_path)


@workflow(
    purpose="A catalog case's coverage is read from its proofs -- a witness "
            "makes it proven, a pev-request diagnosed, no link unknown -- so "
            "coverage cannot drift from prose (Tier 2).",
)
def test_a_case_reads_its_links_as_proofs(tmp_path):
    """The claim the whole report rests on, on the kind that links proofs.

    A finding link is deliberately not coverage: it records what is wrong, not
    that anything proves or fixes it, so a case carrying only a finding is
    still unknown.
    """
    _write(tmp_path, "tests/t.py", "def test_a():\n    pass\n")
    _doc(tmp_path, "docs/pev-requests/2026-01-01-fix.json", [_sec("scope")],
         tags=["pev-request", "queued"])
    _doc(tmp_path, "docs/system/cli/catalog.json", [
        {"id": "surface", "heading": "Surface", "sections": [
            _sec("witnessed", ["pm::tests.t::test_a"]),
            _sec("diagnosed", ["pm::docs.pev-requests.2026-01-01-fix"]),
            _sec("only-a-finding", ["pm::docs.finding-queue::storage.d-20"]),
            _sec("bare"),
        ]},
    ])

    assert _states(_run(tmp_path)) == {
        "surface.witnessed": "proven",
        "surface.diagnosed": "diagnosed",
        "surface.only-a-finding": "unknown",
        "surface.bare": "unknown",
    }


@workflow(
    purpose="An expectation reads the same structural signal with the opposite "
            "polarity: it links findings and requests, so no link is `assumed` "
            "rather than unknown, and a finding is `open` (Tier 2).",
)
def test_an_expectation_reads_its_links_as_problems(tmp_path):
    """The polarity split, which is the thing most likely to be misread.

    A case links proofs, so an absent link means nobody proved it.  An
    expectation links findings and requests, so an absent link means nobody
    filed anything.  Reading both with one rule is what made a broad promise
    report as proven off a case that covered a fraction of it.
    """
    _doc(tmp_path, "docs/pev-requests/2026-01-01-fix.json", [_sec("scope")],
         tags=["pev-request", "queued"])
    _doc(tmp_path, "docs/system/cli/expectations.json", [
        {"id": "starting", "heading": "Starting", "sections": [
            _sec("bare"),
            _sec("filed", ["pm::docs.finding-queue::storage.d-20"],
                 tags=["violated"]),
            _sec("planned", ["pm::docs.finding-queue::storage.d-20",
                             "pm::docs.pev-requests.2026-01-01-fix"],
                 tags=["violated"]),
        ]},
    ])

    assert _states(_run(tmp_path)) == {
        "starting.bare": "assumed",
        "starting.filed": "open",
        "starting.planned": "diagnosed",
    }


@workflow(
    purpose="An expectation carries exactly one of investigate / confirmed / "
            "violated, and a blank one is reported: absence of a tag must not "
            "be readable as an affirmation (Tier 2).",
)
def test_a_blank_expectation_tag_is_an_error_not_a_default(tmp_path):
    """Why the tag vocabulary opened to a third value.

    Coverage is structural and says only what is filed, so it cannot tell a
    sentence written today from one nothing has contradicted in a year.  If
    blank meant "affirmed", forgetting to tag a row would affirm it -- the
    same silent green the witness edge was removed for.  So `confirmed` is
    said out loud, and blank is an error.
    """
    _doc(tmp_path, "docs/system/cli/expectations.json", [
        {"id": "starting", "heading": "Starting", "sections": [
            _sec("blank"),
            _sec("affirmed", tags=["confirmed"]),
            _sec("not-yet-looked", tags=["investigate"]),
        ]},
    ])

    rows = _run(tmp_path)

    # All three are `assumed`: coverage answers what is filed, never who looked.
    assert set(_states(rows).values()) == {"assumed"}
    assert any("UNTAGGED EXPECTATION" in w for w in _warnings(rows))
    assert not [r for r in rows if r.row_id == "starting.affirmed" and r.warnings]
    assert sorted(r.row_id for r in rows if r.needs_attention) == [
        "starting.blank",
        "starting.not-yet-looked",
    ]


@workflow(
    purpose="A violated expectation names what violates it -- a finding, or a "
            "request for a promise nothing delivers yet -- and one linking "
            "neither is reported (Tier 2).",
)
def test_a_violated_expectation_names_its_violation(tmp_path):
    """A request alone is a violation when the promised behavior does not exist.

    A request's new promise is written at filing, before any code delivers it,
    and there is no defect to file against behavior that is absent; the
    request itself is what the row is waiting on.  A `violated` tag with no
    link at all names nothing, and is the row nobody can act on.
    """
    _doc(tmp_path, "docs/pev-requests/2026-01-01-fix.json", [_sec("scope")],
         tags=["pev-request", "queued"])
    _doc(tmp_path, "docs/system/cli/expectations.json", [
        {"id": "starting", "heading": "Starting", "sections": [
            _sec("unbuilt", ["pm::docs.pev-requests.2026-01-01-fix"],
                 tags=["violated"]),
            _sec("defect", ["pm::docs.finding-queue::storage.d-20"],
                 tags=["violated"]),
            _sec("nameless", tags=["violated"]),
        ]},
    ])

    rows = {r.row_id: r for r in _run(tmp_path)}

    assert rows["starting.unbuilt"].warnings == []
    assert rows["starting.defect"].warnings == []
    assert rows["starting.nameless"].warnings == [
        "violated with no finding or request link"
    ]


@workflow(
    purpose="An expectation linking anything but a pev-request or the finding "
            "queue is reported, and the link never counts as coverage -- a "
            "promise is broader than any one case can prove (Tier 2).",
)
def test_an_expectation_may_only_link_a_request_or_a_finding(tmp_path):
    """Why the witness edge was removed rather than bounded.

    A case link on a broad sentence reads as proof while proving a fraction of
    it, and the row then shows green until a finding happens to surface.  The
    row stays `assumed` here: the illegal link buys it nothing.
    """
    _write(tmp_path, "tests/t.py", "def test_a():\n    pass\n")
    _doc(tmp_path, "docs/system/layout.json", [
        {"id": "catalog", "sections": [_sec("root-walk", ["pm::tests.t::test_a"])]},
    ])
    _doc(tmp_path, "docs/system/cli/expectations.json", [
        {"id": "starting", "heading": "Starting", "sections": [
            _sec("cites-a-case", ["pm::docs.system.layout::catalog.root-walk"]),
            _sec("cites-a-test", ["pm::tests.t::test_a"]),
        ]},
    ])

    rows = _run(tmp_path)
    warnings = _warnings(rows)

    assert any("ILLEGAL LINK" in w and "catalog.root-walk" in w for w in warnings)
    assert any("ILLEGAL LINK" in w and "tests.t::test_a" in w for w in warnings)
    assert _states(rows) == {
        "catalog.root-walk": "proven",
        "starting.cites-a-case": "assumed",
        "starting.cites-a-test": "assumed",
    }


@workflow(
    purpose="A citation that no longer resolves is reported -- a dead catalog "
            "case and a missing request -- because the index reports neither: "
            "a ghost node reads clean and a purged node reads as absent (Tier 2).",
)
def test_dead_citations_and_missing_requests_are_reported(tmp_path):
    """The reason this reads files rather than the axiom-graph index.

    Both shapes here are the ones an index-backed resolver gets wrong, so the
    report has to answer them from the tree itself.
    """
    _doc(tmp_path, "docs/system/cli/catalog.json", [
        {"id": "surface", "heading": "Surface", "sections": [
            _sec("cites-a-ghost", ["pm::tests.gone::test_removed"]),
        ]},
    ])
    _doc(tmp_path, "docs/system/cli/expectations.json", [
        {"id": "starting", "heading": "Starting", "sections": [
            _sec("cites-no-request", ["pm::docs.pev-requests.2026-01-01-absent"]),
        ]},
    ])

    rows = _run(tmp_path)
    warnings = _warnings(rows)

    assert any("DEAD CITATION" in w and "tests.gone" in w for w in warnings)
    assert any("does not exist" in w and "2026-01-01-absent" in w for w in warnings)
    # A dead citation is not a witness: the case stays unknown rather than
    # reading as proven off a link that resolves to nothing.
    assert _states(rows)["surface.cites-a-ghost"] == "unknown"


@workflow(
    purpose="A row still linked to a request that has landed is reported as a "
            "stale diagnosis, which is what stops the diagnosed set becoming a "
            "graveyard instead of a queue (Tier 2).",
)
def test_a_landed_request_is_a_stale_diagnosis(tmp_path):
    """The obligation the doc topology assigns to the agent, made detectable.

    "The agent replaces the link at the end of the cycle" is a habit until
    something notices when it did not happen.  An open request is silent; a
    completed one is not.
    """
    _doc(tmp_path, "docs/pev-requests/2026-01-01-open.json", [_sec("scope")],
         tags=["pev-request", "queued"])
    _doc(tmp_path, "docs/pev-requests/2026-01-02-landed.json", [_sec("scope")],
         tags=["pev-request", "completed"])
    _doc(tmp_path, "docs/system/cli/expectations.json", [
        {"id": "starting", "heading": "Starting", "sections": [
            _sec("still-open", ["pm::docs.pev-requests.2026-01-01-open"]),
            _sec("landed", ["pm::docs.pev-requests.2026-01-02-landed"]),
        ]},
    ])

    warnings = _warnings(_run(tmp_path))

    assert [w for w in warnings if "STALE DIAGNOSIS" in w and "2026-01-02-landed" in w]
    assert not [w for w in warnings if "2026-01-01-open" in w]


@workflow(
    purpose="The default view is the work queue, and it reads each kind by its "
            "own rule: an unproven case, an open or unconfirmed expectation, "
            "or anything tagged investigate (Tier 2).",
)
def test_the_default_view_is_the_work_queue(tmp_path):
    """What a plan buys, and what it does not.

    A `diagnosed` row is out whichever kind it is -- its request says what
    happens next -- and so is a proven case and a confirmed expectation.  What
    is in is work nobody has picked up: a case with no proof, an expectation
    with a finding and no request, and a promise nobody has affirmed.
    """
    _write(tmp_path, "tests/t.py", "def test_a():\n    pass\n")
    _doc(tmp_path, "docs/pev-requests/2026-01-01-fix.json", [_sec("scope")],
         tags=["pev-request", "queued"])
    _doc(tmp_path, "docs/system/cli/catalog.json", [
        {"id": "surface", "heading": "Surface", "sections": [
            _sec("proven", ["pm::tests.t::test_a"]),
            _sec("unproven"),
        ]},
    ])
    _doc(tmp_path, "docs/system/cli/expectations.json", [
        {"id": "starting", "heading": "Starting", "sections": [
            _sec("planned", ["pm::docs.finding-queue::storage.d-20",
                             "pm::docs.pev-requests.2026-01-01-fix"],
                 tags=["violated"]),
            _sec("open-no-plan", ["pm::docs.finding-queue::storage.d-20"],
                 tags=["violated"]),
            _sec("affirmed", tags=["confirmed"]),
            _sec("needs-a-look", tags=["investigate"]),
            _sec("never-affirmed"),
        ]},
    ])

    rows = _run(tmp_path)

    assert sorted(r.row_id for r in rows if r.needs_attention) == [
        "starting.needs-a-look",
        "starting.never-affirmed",
        "starting.open-no-plan",
        "surface.unproven",
    ]


@workflow(
    purpose="A live pev-request that no row links is reported, landed ones and "
            "ones tagged no-rows are not -- the inverse of the stale-diagnosis "
            "warning, and the other way the queue rots (Tier 2).",
)
def test_a_live_request_with_no_row_is_reported(tmp_path):
    """A request nothing links is invisible from the rows by construction.

    The stale-diagnosis warning catches a row still pointing at a request that
    landed.  This catches the opposite: a fix specified, filed, and then
    quietly not missed by anybody, because no promise is waiting on it.
    """
    _doc(tmp_path, "docs/pev-requests/2026-01-01-linked.json", [_sec("scope")],
         tags=["pev-request", "queued"])
    _doc(tmp_path, "docs/pev-requests/2026-01-02-orphaned.json", [_sec("scope")],
         tags=["pev-request", "not-started"])
    _doc(tmp_path, "docs/pev-requests/2026-01-03-landed.json", [_sec("scope")],
         tags=["pev-request", "completed"])
    _doc(tmp_path, "docs/pev-requests/2026-01-04-owns-no-row.json", [_sec("scope")],
         tags=["pev-request", "queued", "no-rows"])
    _doc(tmp_path, "docs/system/cli/expectations.json", [
        {"id": "starting", "heading": "Starting", "sections": [
            _sec("planned", ["pm::docs.pev-requests.2026-01-01-linked"],
                 tags=["violated"]),
        ]},
    ])

    rows = _run(tmp_path)
    reported = [stem for stem, _ in row_state.unlinked_requests(tmp_path, rows)]

    assert reported == ["2026-01-02-orphaned"]


@workflow(
    purpose="The subject is catalog cases and expectation rows under "
            "docs/system only: a family container, a non-catalog section and a "
            "request document's own tags contribute no rows (Tier 2).",
)
def test_the_subject_is_cases_and_expectations_under_docs_system(tmp_path):
    """Scoping is what keeps the shared vocabulary from colliding.

    `investigate` is also a pev-request status, so a report that swept tags
    globally would count request documents as rows.  Restricting the subject
    structurally -- rather than filtering afterwards -- is what makes that
    impossible rather than merely unlikely.
    """
    _write(tmp_path, "tests/t.py", "def test_a():\n    pass\n")
    _doc(tmp_path, "docs/pev-requests/2026-01-01-fix.json", [_sec("scope")],
         tags=["pev-request", "investigate"])
    _doc(tmp_path, "docs/system/cli.json", [
        _sec("scope", tags=["investigate"]),
        {"id": "catalog", "sections": [_sec("one", ["pm::tests.t::test_a"])]},
        _sec("decisions", tags=["violated"]),
    ])
    _doc(tmp_path, "docs/system/canvas-api/catalog.json", [
        {"id": "submit", "heading": "Submit", "sections": [
            _sec("accepted", ["pm::tests.t::test_a"]),
        ]},
    ])

    rows = _run(tmp_path)

    assert sorted(r.qualified_id for r in rows) == [
        "docs.system.canvas-api.catalog::submit.accepted",
        "docs.system.cli::catalog.one",
    ]
    # The family container is not a row, and neither is scope or decisions.
    assert all(r.row_id != "submit" for r in rows)


def test_a_case_reads_its_tier_from_the_given_bullet(tmp_path):
    """The Given bullet's leading word is the tier; a Given without one is untiered.

    Nothing gates on it: an expectation has no Given and carries no tier.
    """
    by_route = _sec("by-route")
    by_route["content"] = "- Given (route): a registered method.\n- Test: t."
    by_value = _sec("by-value")
    by_value["content"] = "- Given (pure): a literal document.\n- Test: t."
    plain = _sec("plain")                      # ``- Given: ...``
    odd = _sec("odd")
    odd["content"] = "- Given (other): ...\n- Test: t."
    later = _sec("later")
    later["content"] = "- Test: t.\n- Given (route): the tier is read wherever the bullet sits."
    _doc(tmp_path, "docs/system/cli/catalog.json", [
        {"id": "surface", "heading": "Surface",
         "sections": [by_route, by_value, plain, odd, later]},
    ])
    _doc(tmp_path, "docs/system/cli/expectations.json", [
        {"id": "start", "heading": "Start", "sections": [
            _sec("promise", ["pm::docs.finding-queue::cli.d-1"], tags=["violated"]),
        ]},
    ])

    rows = _run(tmp_path)
    assert {r.row_id: r.tier for r in rows if r.kind == "case"} == {
        "surface.by-route": "route",
        "surface.by-value": "pure",
        "surface.plain": "untiered",
        "surface.odd": "untiered",
        "surface.later": "route",
    }
    assert [r.tier for r in rows if r.kind == "expectation"] == [""]
    assert not _warnings(rows), "the tier is reported, never warned about"


def test_the_tier_is_carried_in_the_report_and_the_row_listing(tmp_path, capsys):
    """``--summary`` counts cases per tier and ``--all`` prints each case's tier."""
    tiered = _sec("tiered")
    tiered["content"] = "- Given (pure): ...\n- Test: t."
    _doc(tmp_path, "docs/system/cli/catalog.json", [
        {"id": "surface", "heading": "Surface", "sections": [tiered, _sec("bare")]},
    ])
    rows = _run(tmp_path)

    row_state.render_summary(rows)
    summary = capsys.readouterr().out
    header = next(line for line in summary.splitlines() if line.startswith("unit"))
    total = next(line for line in summary.splitlines() if line.startswith("TOTAL"))
    assert header.split()[-3:] == ["route", "pure", "untiered"]
    assert total.split()[-3:] == ["0", "1", "1"]

    row_state.render_rows(rows, "all rows")
    listing = capsys.readouterr().out
    assert "unknown    pure       surface.tiered" in listing
    assert "unknown    untiered   surface.bare" in listing
