"""The backend table drives every per-backend switch.

Annotation Tier 1, unmarked pure-function tests. Each backend row must be
complete for what it claims (an image-building backend has a generator that
starts from one of its pinned bases, a default interpreter and a staged
lock; a backend that builds nothing has none of these), and the switch
points that name backends (the CLI's ``--backend`` choices, the typed-spec
prefixes, the default interpreter) must agree with it.
"""

from __future__ import annotations

import argparse

import pytest

from wfc.environments.backends import BACKEND_NAMES, BACKENDS, backend_row


def _register_env_backend_choices() -> list[str]:
    """The ``--backend`` choices of ``wfc register-env``'s parser."""
    from wfc.cli import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions
               if isinstance(a, argparse._SubParsersAction))
    register_env = sub.choices["register-env"]
    backend = next(a for a in register_env._actions if a.dest == "backend")
    return list(backend.choices)


def test_cli_backend_choices_equal_the_table():
    assert _register_env_backend_choices() == list(BACKEND_NAMES)


@pytest.mark.parametrize("name", BACKEND_NAMES)
def test_every_backend_row_is_complete_for_what_it_builds(name):
    row = BACKENDS[name]
    assert row.default_python("demo"), f"{name}: no default interpreter"
    # A payload carrying what a row's generator reads beyond the env name
    # (uv's Python minor, from the project's .python-version).
    source = {"uv_python": "3.12"}
    extra = row.generator_args(source) if row.generator_args else {}
    dockerfile = row.generate(env_name="demo", pip_freeze_content="", **extra)
    if not row.builds_image:
        assert dockerfile is None
        assert row.pinned_bases == ()
        assert row.staged_lock is None
        return
    from_lines = [ln.split(None, 1)[1] for ln in dockerfile.splitlines()
                  if ln.startswith("FROM ")]
    assert from_lines and from_lines[0] in row.pinned_bases, (
        f"{name}: generator's FROM {from_lines} is not a pinned base")
    assert from_lines[0] == row.default_base(source), (
        f"{name}: the recorded base is not the one the generator builds on")
    assert all("@sha256:" in base for base in row.pinned_bases)
    assert row.staged_lock is not None, f"{name}: no staged lock"
    assert row.parse_lock is not None, f"{name}: no package parser"


@pytest.mark.parametrize("name", BACKEND_NAMES)
def test_switch_points_read_the_table(name):
    from wfc.environments.interpreter import default_python_for_backend
    from wfc.environments.verbs import _typed_spec_backend

    row = BACKENDS[name]
    assert default_python_for_backend(name, "demo") == row.default_python("demo")
    expected = name if row.live_spec else None
    assert _typed_spec_backend(f"{name}:demo") == expected


def test_unknown_backend_is_refused_naming_every_backend():
    from wfc.environments.dockerfiles import generate_for_backend

    with pytest.raises(ValueError) as exc:
        generate_for_backend("nix", env_name="demo")
    for name in BACKEND_NAMES:
        assert repr(name) in str(exc.value)
    with pytest.raises(ValueError, match="Unknown backend 'nix'"):
        backend_row("nix")
