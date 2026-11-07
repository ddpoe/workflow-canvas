"""Contracts unit: resolving an output's ``options`` block.

``resolve_options`` turns an output's declared ``options`` and the
upstream's param sets into the ordered answer the canvas's picker shows:
the fixed names as declared, one built group per ``from_params`` entry,
and the patterns as written. Catalog: ``option-resolution``.
"""

import pytest
from axiom_annotations import workflow

from wfc.contracts import resolve_options


OPTIONS = {
    "fixed": ["label", "area", "DAPI_nuc_mean"],
    "from_params": [
        {"params": ["marker"], "pattern": "{}_nuc_mean"},
        {"params": ["markers", "stats"], "pattern": "{}_{}"},
        {"params": ["missing"], "pattern": "{}_never"},
    ],
    "patterns": ["*_mean", "*_std", "*_mean"],
}


@workflow(purpose="An output's options resolve in declaration order over "
                  "every param set: the fixed names as declared, then one "
                  "built group per from_params entry with its parameters and "
                  "the names its pattern expands to (each list value an item, "
                  "several parameters their cartesian product), first seen "
                  "across the sets; a name appears once, where it first "
                  "occurs; an entry with a parameter unset adds nothing; "
                  "patterns are kept as written and never expanded",
          inputs="An options block with fixed names, a one- and a "
                 "two-parameter from_params entry, an entry whose parameter "
                 "no set holds, and patterns; two param sets",
          outputs="fixed, built [{params, names}], patterns and all, ordered")
def test_options_resolve_in_order_over_every_param_set():
    """Fixed, then built per entry, then patterns; each name once."""
    base = {"marker": "DAPI", "markers": ["p27", "CycD1"], "stats": ["mean"]}
    variant = {"marker": "p27", "markers": ["p27"], "stats": ["mean", "std"]}

    answer = resolve_options(OPTIONS, [base, variant])

    assert answer["fixed"] == ["label", "area", "DAPI_nuc_mean"]
    assert answer["built"] == [
        # DAPI_nuc_mean is already fixed, so only the variant's name is built.
        {"params": ["marker"], "names": ["p27_nuc_mean"]},
        {"params": ["markers", "stats"],
         "names": ["p27_mean", "CycD1_mean", "p27_std"]},
    ]
    assert answer["patterns"] == ["*_mean", "*_std"]
    assert answer["all"] == ["label", "area", "DAPI_nuc_mean", "p27_nuc_mean",
                             "p27_mean", "CycD1_mean", "p27_std"]


def test_a_name_two_entries_build_is_kept_under_the_first():
    """The first entry that builds a name keeps it; a later one drops it."""
    options = {"from_params": [{"params": ["a"], "pattern": "{}"},
                               {"params": ["b"], "pattern": "{}"}]}

    answer = resolve_options(options, [{"a": "x", "b": ["x", "y"]}])

    assert answer["built"] == [{"params": ["a"], "names": ["x"]},
                               {"params": ["b"], "names": ["y"]}]


def test_a_single_param_set_may_be_passed_as_a_mapping():
    """One mapping reads as one param set."""
    options = {"from_params": [{"params": ["marker"], "pattern": "{}_mean"}]}

    assert resolve_options(options, {"marker": "DAPI"})["all"] == ["DAPI_mean"]


@pytest.mark.parametrize("options, params", [
    pytest.param({}, {}, id="empty-block"),
    pytest.param(None, {}, id="no-block"),
    pytest.param({"from_params": [{"params": ["marker"], "pattern": "{}_mean"}]},
                 {}, id="parameter-absent"),
    pytest.param({"from_params": [{"params": ["marker"], "pattern": "{}_mean"}]},
                 {"marker": None}, id="parameter-null"),
])
def test_nothing_to_resolve_answers_empty_groups(options, params):
    """No block, or an entry whose parameter is unset, builds nothing."""
    assert resolve_options(options, [params]) == {
        "fixed": [], "built": [], "patterns": [], "all": []}
