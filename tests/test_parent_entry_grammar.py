"""Tier 2: the parent-entry grammar — one module owns encode and decode.

``wfc.execution.parents`` spells an entry and parses it back. The two are
inverses, the wire format has exactly two forms (``input:output:run`` and
``input:run``), and a slot name that would break the grammar is refused
where slot names are declared (``parse_method_yaml``), not silently
re-parsed into a different wiring.
"""
from __future__ import annotations

import itertools

import pytest
from axiom_annotations import workflow

from wfc.execution.parents import (
    ParentEntry,
    parse_parent_entries,
    spell_parent_entry,
)


INPUT_SLOTS = ["data", "sources", "ref_panel", "in-1"]
SOURCE_SLOTS = [None, "merged", "qc", "out-2"]
RUN_IDS = [1, 412, 99999]


@workflow(purpose="Every (input slot, optional source slot, run id) triple survives "
                  "a spell/parse round trip through the parent-entry grammar")
def test_spelling_and_parsing_are_inverses():
    # One entry per parse: the wiring rule is a property of a whole list, and
    # the product deliberately contains lists that break it.
    for input_slot, source_slot, run_id in itertools.product(
        INPUT_SLOTS, SOURCE_SLOTS, RUN_IDS
    ):
        spelled = spell_parent_entry(input_slot, source_slot, run_id)
        parsed = parse_parent_entries([spelled], step="n1")

        assert parsed == [ParentEntry(input_slot, source_slot, run_id)]
        # ...and the other direction: re-spelling is the same text.
        assert spell_parent_entry(*parsed[0]) == spelled


@workflow(purpose="A bare run id is not a parent entry: the parser refuses it "
                  "naming the entry and the step, so no caller supplies a "
                  "default input slot of its own")
def test_bare_run_id_is_refused():
    with pytest.raises(ValueError) as exc:
        parse_parent_entries(["412"], step="merge_counts")

    message = str(exc.value)
    assert "412" in message
    assert "merge_counts" in message


@pytest.mark.parametrize("entry", ["data:merged:extra:412", "data:notanint", ":412"])
def test_entries_outside_the_two_forms_are_refused(entry):
    """Tier 1: four fields, a non-integer run id, and an empty input slot are
    each outside the grammar."""
    with pytest.raises(ValueError):
        parse_parent_entries([entry], step="n1")


@workflow(purpose="A slot name carrying a colon is refused where slot names are "
                  "declared, so the grammar never has to guess which colon is "
                  "the delimiter")
def test_a_colon_in_a_slot_name_is_refused_at_declaration(tmp_path):
    from wfc.contracts.declarations import parse_method_yaml

    # Spelled with a colon-carrying slot, the entry reads as a different
    # wiring than the one intended — which is exactly what the declaration
    # rule prevents from ever being registered.
    assert spell_parent_entry("a:b", None, 412) == "a:b:412"
    assert parse_parent_entries(["a:b:412"], step="n1") == [
        ParentEntry("a", "b", 412)
    ]

    method_dir = tmp_path / "m"
    method_dir.mkdir()
    yaml_path = method_dir / "method.yaml"
    yaml_path.write_text(
        "env: grammar-env\n"
        "inputs:\n"
        "  'a:b': a file\n"
        "outputs:\n"
        "  out:\n"
        "    type: .csv\n"
    )
    with pytest.raises(ValueError) as exc:
        parse_method_yaml(method_dir)
    assert "a:b" in str(exc.value)
    assert str(yaml_path) in str(exc.value)


def test_a_list_of_entries_after_one_flag_is_an_argparse_error(capsys):
    """Two entries after one ``--parent-run-id`` stop at argument parsing.

    The flag takes one entry per use, so the second entry is an unrecognised
    argument: argparse exits 2 naming it, and no entry reaches the parser.
    """
    from wfc.cli import build_parser

    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([
            "check_cache", "--method", "align", "--module", "reads_mod",
            "--sample", "s1",
            "--parent-run-id", "reads:bam:412", "reads:bam:413",
        ])

    assert exit_info.value.code == 2
    assert "reads:bam:413" in capsys.readouterr().err
