"""The terminal surface is frozen: `build_parser()` against a committed pin.

Covers:
  - Tier 2: every verb (and `cache`'s sub-verbs) with each option's
    option strings, destination, default, choices, ``nargs`` and
    required-ness, compared to ``tests/fixtures/cli_parser_surface.json``.

The pin holds the terminal surface still: the parser is what the terminal
sees, and code behind ``wfc/cli.py`` may change without changing what it
accepts. Help text is deliberately not pinned — only the machine-readable
surface.

Regenerate the pin ONLY for a deliberate surface change, with
``poetry run python tools/regen_cli_parser_pin.py``, and say so in the
commit message.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from axiom_annotations import workflow

PIN_PATH = Path(__file__).parent / "fixtures" / "cli_parser_surface.json"


def _literal(value):
    """Return a JSON-safe rendering of an argparse attribute value."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return repr(value)


def parser_surface(parser: argparse.ArgumentParser, path: str = "wfc") -> dict:
    """Walk a parser and its subparsers into a comparable option table.

    Args:
        parser: The parser to walk.
        path: Command path prefix for the parser's own entry.

    Returns:
        A mapping of command path (e.g. ``"wfc cache archive"``) to the
        list of its option records, each carrying option strings,
        destination, default, choices, ``nargs`` and required-ness.
        Positionals come first in **declaration order** (argparse consumes
        them in that order, so a swap is a real surface change), followed
        by the flagged options sorted by ``(dest, option strings)`` — those
        are order-insensitive on the command line, and sorting keeps the
        pin stable against a harmless re-ordering of ``add_argument`` calls.
    """
    entries: dict[str, list[dict]] = {}
    positionals: list[dict] = []
    flagged: list[dict] = []
    subparser_actions: list[argparse._SubParsersAction] = []

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            subparser_actions.append(action)
            continue
        record = {
            "option_strings": list(action.option_strings),
            "dest": action.dest,
            "default": _literal(action.default),
            "choices": (
                None if action.choices is None
                else [_literal(choice) for choice in action.choices]
            ),
            "nargs": _literal(action.nargs),
            "required": bool(action.required),
        }
        (flagged if action.option_strings else positionals).append(record)

    entries[path] = positionals + sorted(
        flagged, key=lambda o: (o["dest"], ",".join(o["option_strings"]))
    )
    for sub_action in subparser_actions:
        for name, subparser in sub_action.choices.items():
            entries.update(parser_surface(subparser, f"{path} {name}"))
    return entries


@workflow(
    purpose="The terminal surface is frozen: every verb's option strings, "
            "destinations, defaults, choices, nargs and required-ness match "
            "the committed pin, so a change to the parser's construction "
            "cannot change what it accepts or how it fills its namespace.",
)
def test_parser_surface_matches_pin():
    from wfc.cli import build_parser

    surface = parser_surface(build_parser())
    pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))

    assert sorted(surface) == sorted(pin), (
        "the set of verbs changed; regenerate the pin only on purpose"
    )
    for command in sorted(pin):
        assert surface[command] == pin[command], (
            f"the option surface of `{command}` changed"
        )
