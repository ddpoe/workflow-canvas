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
        run_id: The upstream run's id; ``None`` when the run part is a
            draft string.
        draft: The run part when it names a draft step
            (``draft:<module>.<method>/<node>@<sample>``), else ``None``.
    """

    input_slot: str
    source_slot: str | None
    run_id: int | None
    draft: str | None = None


def _split_draft_run_part(text: str) -> tuple[list[str], str] | None:
    """Split an entry whose run part is a draft string, or ``None`` when it is not.

    The run part is a draft string only when it parses as a full one
    (``draft:<module>.<method>/<node>@<sample>``, Execution's draft parser).
    A slot may be named ``draft``, so ``data:draft:412`` is input ``data``,
    output ``draft``, run 412. The two-slot reading is tried first, then the
    one-slot reading, then the whole entry (which names no slot and is
    refused for that).

    Args:
        text: The entry as typed or spelled.

    Returns:
        ``(slot parts, draft string)``: the parts before the draft string
        (input, or input and output) and the run part.
    """
    from .draft import DraftRefError, parse_draft_ref

    parts = text.split(":")
    for cut in (2, 1, 0):
        if cut >= len(parts):
            continue
        run_part = ":".join(parts[cut:])
        try:
            parse_draft_ref(run_part)
        except DraftRefError:
            continue
        return parts[:cut], run_part
    return None


def _refuse_a_short_draft_string(parts: list[str]) -> None:
    """Raise the draft parser's refusal for a run part that starts a draft string.

    Called for an entry that is neither a draft entry nor a run entry, at a
    step that may read a draft step: a run part starting ``draft:`` that
    misses a part is refused showing the full form.

    Args:
        parts: The entry split on ``:``.

    Raises:
        DraftRefError: A run part starts ``draft:`` and is not a full draft
            string.
    """
    from .draft import DRAFT_REF_PREFIX, parse_draft_ref

    for cut in (1, 2, 0):
        run_part = ":".join(parts[cut:])
        if cut < len(parts) and run_part.startswith(DRAFT_REF_PREFIX):
            parse_draft_ref(run_part)


def spell_parent_entry(
    input_slot: str, source_slot: str | None, run_id: int | str | None,
    draft: str | None = None,
) -> str:
    """Spell one parent entry: the inverse of :func:`parse_parent_entries`.

    Args:
        input_slot: The step's input slot the run feeds.
        source_slot: The upstream run's output slot, or ``None`` when the
            link names none.
        run_id: The upstream run's id (or a draft string as the run part).
        draft: A draft string as the run part, which wins over *run_id*
            (a parsed entry's own field, so ``spell(*entry)`` round-trips).

    Returns:
        ``input:output:run`` when an output is named, ``input:run`` otherwise.
    """
    if draft is not None:
        run_id = draft
    if source_slot:
        return f"{input_slot}:{source_slot}:{run_id}"
    return f"{input_slot}:{run_id}"


def parse_parent_entries(
    entries: Iterable,
    *,
    step: str,
    allow_drafts: bool = False,
) -> list[ParentEntry]:
    """Parse a step's parent entries and apply the wiring rule to the whole list.

    The run part is a run id, or (for a step that runs as a draft step, and
    so records nothing) a draft string: a run part that Execution's draft
    module parses in full (every part is required). Any other run part is a
    run id, so a slot named ``draft`` reads as a slot.

    Args:
        entries: Entries as ``"input:output:run"`` or ``"input:run"`` strings.
        step: The step's name, for error messages.
        allow_drafts: Whether a draft string may be the run part. A recorded
            run never reads a draft step's outputs, so only the draft step
            passes True.

    Returns:
        The parsed entries, in order.

    Raises:
        ValueError: An entry is not one of the two forms -- a bare run id
            among them -- or its run id is not an integer, or its draft
            string is refused (or not allowed here), or two entries wire
            different outputs of one run into the same input slot.
    """
    parsed: list[ParentEntry] = []
    for entry in entries:
        text = str(entry).strip()
        drafted = _split_draft_run_part(text)
        if drafted is not None:
            parsed.append(_draft_entry(text, *drafted, step=step,
                                       allow_drafts=allow_drafts))
            continue
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
            if allow_drafts:
                _refuse_a_short_draft_string(text.split(":"))
            raise ValueError(
                f"Step '{step}': parent entry '{text}' is not one of "
                f"input:output:run or input:run (with an integer run id). "
                f"Every entry names the input slot it feeds; write "
                f"'<input slot>:{text}' if '{text}' is a bare run id."
            )
        parsed.append(ParentEntry(input_slot, source_slot or None, run_id))
    _check_wiring(parsed, step=step)
    return parsed


def _draft_entry(text: str, slots: list[str], run_part: str, *, step: str,
                 allow_drafts: bool) -> ParentEntry:
    """Parse one entry whose run part is a draft string.

    Args:
        text: The whole entry, for messages.
        slots: The parts before ``draft:``: the input, or input and output.
        run_part: The draft string.
        step: The step's name, for messages.
        allow_drafts: Whether a draft string may feed this step.

    Returns:
        The entry, its ``draft`` the draft string as written.

    Raises:
        ValueError: Not allowed here, the slot parts are not one of the two
            forms, or the draft string misses a part.
    """
    from .draft import parse_draft_ref

    if not allow_drafts:
        raise ValueError(
            f"Step '{step}': parent entry '{text}' names a draft step "
            f"({run_part}). A recorded run cannot read a draft step's outputs, "
            f"which are not kept; register the draft and run again.")
    if len(slots) not in (1, 2) or not slots[0]:
        raise ValueError(
            f"Step '{step}': parent entry '{text}' is not one of "
            f"input:output:run or input:run. Every entry names the input slot "
            f"it feeds: '<input slot>:{run_part}'.")
    ref = parse_draft_ref(run_part)
    source = slots[1] if len(slots) == 2 and slots[1] else None
    return ParentEntry(slots[0], source, None, ref.text)


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
    seen: dict[tuple[str, int | str | None], str | None] = {}
    for entry in entries:
        key = (entry.input_slot, entry.run_id if entry.draft is None else entry.draft)
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
