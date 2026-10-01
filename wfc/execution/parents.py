"""Parent entries: the one owner of the wire format, both directions.

A parent entry says which of a step's inputs an upstream run feeds, and which
of that run's outputs feeds it. It has two forms and no others:

- ``input:output:run`` names the input slot, the upstream output slot and the
  run id (``data:merged:412``);
- ``input:run`` names the input slot and the run id, and no output.

:func:`spell_parent_entry` writes an entry and :func:`parse_parent_entries`
reads one; they live together because they are inverses, and a reader that
disagrees with the writer turns one wiring into another silently. The claim
phase spells entries from the pipeline's wiring, a user types them on
``--parent-run-id``, and every list, spelled or typed, comes back through the
parser.

An entry always names its input slot. A bare run id is refused: it would have
to be assigned a default input slot by whichever caller happened to parse it,
and the two callers held different defaults, so the same text meant two
wirings.

The parser applies the one wiring rule over the whole list: entries that share
an input slot and a run must name the same output. Entries that share an input
slot and name different runs are fan-in and allowed.

Slot names cannot contain ``:`` — the delimiter would be ambiguous. That rule
is enforced where slot names are declared, in
``wfc.contracts.declarations.parse_method_yaml``.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple


class ParentEntry(NamedTuple):
    """One parsed parent entry.

    Attributes:
        input_slot: The step's input slot the run feeds.
        source_slot: The upstream run's output slot, or None when the entry
            names none.
        run_id: The upstream run's id.
    """

    input_slot: str
    source_slot: str | None
    run_id: int


def spell_parent_entry(
    input_slot: str, source_slot: str | None, run_id: int | str
) -> str:
    """Spell one parent entry: the inverse of :func:`parse_parent_entries`.

    Args:
        input_slot: The step's input slot the run feeds.
        source_slot: The upstream run's output slot, or ``None`` when the
            link names none.
        run_id: The upstream run's id.

    Returns:
        ``input:output:run`` when an output is named, ``input:run`` otherwise.
    """
    if source_slot:
        return f"{input_slot}:{source_slot}:{run_id}"
    return f"{input_slot}:{run_id}"


def parse_parent_entries(
    entries: Iterable,
    *,
    step: str,
) -> list[ParentEntry]:
    """Parse a step's parent entries and apply the wiring rule to the whole list.

    Args:
        entries: Entries as ``"input:output:run"`` or ``"input:run"`` strings.
        step: The step's name, for error messages.

    Returns:
        The parsed entries, in order.

    Raises:
        ValueError: An entry is not one of the two forms -- a bare run id
            among them -- or its run id is not an integer, or two entries
            wire different outputs of one run into the same input slot.
    """
    parsed: list[ParentEntry] = []
    for entry in entries:
        text = str(entry).strip()
        parts = text.split(":")
        if len(parts) == 3:
            input_slot, source_slot, run_text = parts
        elif len(parts) == 2:
            (input_slot, run_text), source_slot = parts, None
        else:
            input_slot, source_slot, run_text = "", None, ""
        try:
            run_id = int(run_text)
        except ValueError:
            run_id = None
        if run_id is None or not input_slot:
            raise ValueError(
                f"Step '{step}': parent entry '{text}' is not one of "
                f"input:output:run or input:run (with an integer run id). "
                f"Every entry names the input slot it feeds; write "
                f"'<input slot>:{text}' if '{text}' is a bare run id."
            )
        parsed.append(ParentEntry(input_slot, source_slot or None, run_id))
    _check_wiring(parsed, step=step)
    return parsed


def _check_wiring(entries: list[ParentEntry], *, step: str) -> None:
    """Reject entries that wire two outputs of one run into one input slot.

    Entries sharing an input slot and a run must name the same output (an
    entry naming no output counts as naming a different one). Entries sharing
    an input slot with different runs are fan-in and pass.

    Args:
        entries: The step's parsed entries.
        step: The step's name, for the error message.

    Raises:
        ValueError: Naming the step, the input slot, the run and both outputs.
    """
    seen: dict[tuple[str, int], str | None] = {}
    for entry in entries:
        key = (entry.input_slot, entry.run_id)
        if key not in seen:
            seen[key] = entry.source_slot
            continue
        prior = seen[key]
        if prior != entry.source_slot:
            def _named(slot: str | None) -> str:
                return f"'{slot}'" if slot is not None else "(no output named)"
            raise ValueError(
                f"Step '{step}' input slot '{entry.input_slot}' is wired to two "
                f"different outputs of run {entry.run_id}: {_named(prior)} and "
                f"{_named(entry.source_slot)}. One input slot cannot receive two "
                f"outputs of the same run; remove one of the links."
            )
