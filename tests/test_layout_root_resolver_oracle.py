"""Layout unit: the root-resolver agreement oracle.

One function in this repository resolves a project root —
``wfc.persistence.project_root``: an env override validated against
``.wfc/wf-canvas.toml``, an upward walk from the cwd, and a ``RuntimeError``
when neither succeeds.

This oracle states the requirement: on one tree, from one cwd, with no
environment override in play, every entry point that resolves a root
derives the same answer as the canonical one — the same path where it
resolves, a refusal where it raises. A
returned cwd, or a walk past the tree to some ancestor, is disagreement.

Requirement: ``docs/system/layout.json``, section ``testing.requirements``
("Agreement oracle: one root resolver").
"""
from __future__ import annotations

from pathlib import Path

import pytest
from axiom_annotations import workflow

from tests.fixtures.conftest import write_env_record
from wfc.persistence import project_root, reset_engine

#: The env the S1 shape registers, so the entry points that need one
#: (dev-loop, the envs CLI) reach past root resolution on a real project.
ENV_NAME = "oracle-env"


# =============================================================================
# The three tree shapes
# =============================================================================

def _s1_marker(root: Path) -> None:
    """A real project: ``.wfc/`` holding the config file, plus one env record."""
    (root / ".wfc").mkdir(parents=True)
    (root / ".wfc" / "wf-canvas.toml").write_text('[project]\nname = "oracle"\n')
    write_env_record(root, ENV_NAME, image="ghcr.io/oracle/env", digest="a" * 64)


def _s2_bare_state_dir(root: Path) -> None:
    """A bare ``.wfc/`` with no config file — a half-initialised directory.

    The canonical resolver requires the config file, so every entry point
    must refuse this shape rather than accept the bare directory.
    """
    (root / ".wfc").mkdir(parents=True)


def _s3_no_marker(root: Path) -> None:
    """No ``.wfc`` anywhere: not a project at all."""


SHAPES = {
    "s1-marker": _s1_marker,
    "s2-bare-state-dir": _s2_bare_state_dir,
    "s3-no-marker": _s3_no_marker,
}

#: Shape -> whether the canonical resolver raises on it.
CANONICAL_RAISES = {
    "s1-marker": False,
    "s2-bare-state-dir": True,
    "s3-no-marker": True,
}


# =============================================================================
# The entry points, each reduced to (kind, value)
# =============================================================================

def _canvas(root: Path, capsys) -> tuple:
    """The canvas server's one accessor: a path, or the resolver's error."""
    from wfc.canvas.state import _server_project_root
    try:
        return ("path", Path(_server_project_root()).resolve())
    except RuntimeError:
        return ("raise",)


def _dev_loop(root: Path, capsys) -> tuple:
    """dev-loop's shared resolution: the root it will bind at /work, or its error."""
    from wfc.environments.dev_loop import _DevLoopError, _resolve_env_and_runtime
    try:
        _ref, resolved, _cache = _resolve_env_and_runtime(ENV_NAME)
    except _DevLoopError as exc:
        assert "No wfc project found" in exc.message, exc.message
        return ("raise",)
    return ("path", Path(resolved).resolve())


def _envs_cli(root: Path, capsys) -> tuple:
    """``wfc list-envs``: exit 0 listing the env recorded under ``root``, or exit 1.

    A CLI command exposes no path, so the root it resolved is read off its
    side effect: the env record exists only under the shape's root, and the
    listing names it only if that is the manifest the command read.
    """
    from wfc.environments import verbs
    rc = verbs.list_envs()
    captured = capsys.readouterr()
    if rc != 0:
        assert "No wfc project found" in captured.err, captured.err
        return ("raise",)
    assert ENV_NAME in captured.out, (
        f"list-envs exited 0 but listed a manifest other than {root}'s: "
        f"{captured.out!r}"
    )
    return ("path", root.resolve())


ENTRY_POINTS = {
    "canvas-accessor": _canvas,
    "dev-loop": _dev_loop,
    "envs-cli": _envs_cli,
}


def _canonical_outcome() -> tuple:
    """Reduce the canonical resolver to the same ``(kind, value)`` shape."""
    try:
        return ("path", project_root().resolve())
    except RuntimeError:
        return ("raise",)


@pytest.fixture
def canonical(request, tmp_path, monkeypatch) -> tuple:
    """Build one shape, pin the cwd inside it, and return the reference answer.

    The canonical resolver's own outcome is verified *here*, in setup, so a
    developer whose machine carries a marker above pytest's temp directory
    sees a polluted-fixture ERROR rather than a cell mis-attributed to an
    entry point.

    Yields:
        The canonical resolver's ``(kind, value)`` outcome on this shape.
    """
    shape_name = request.getfixturevalue("shape_name")

    root = tmp_path / "proj"
    nested_cwd = root / "a" / "b"
    nested_cwd.mkdir(parents=True)
    SHAPES[shape_name](root)

    monkeypatch.delenv("WFC_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(nested_cwd)

    reset_engine()
    observed = _canonical_outcome()
    expected = ("raise",) if CANONICAL_RAISES[shape_name] else ("path", root.resolve())
    assert observed == expected, (
        f"shape {shape_name} is polluted, not divergent: wfc.persistence."
        f"project_root returned {observed!r} where the shape declares "
        f"{expected!r}. Some ancestor of {tmp_path} carries a "
        f".wfc/wf-canvas.toml marker, so this cell would measure the "
        f"developer's filesystem rather than the shape."
    )

    yield observed
    reset_engine()


# =============================================================================
# The matrix
# =============================================================================

CELLS = [
    pytest.param(entry, shape, id=f"{entry}-{shape}")
    for entry in ENTRY_POINTS
    for shape in SHAPES
]


@pytest.mark.parametrize("entry_name,shape_name", CELLS)
@workflow(purpose="Every entry point that resolves a project root "
                  "derives the same answer as wfc.persistence.project_root on "
                  "the same tree, from the same cwd, with no environment "
                  "override in play",
          inputs="One entry point and one tree shape",
          outputs="Agreement, or a named divergence")
def test_root_resolvers_agree(entry_name, shape_name, canonical, tmp_path, capsys):
    """One entry point, on one tree shape, against the canonical answer.

    Agreement means the same resolved ``Path`` where the canonical resolver
    returns one and a refusal where it raises. A returned cwd, or a walk
    past the tree to an ancestor, is disagreement.
    """
    # The canonical call in the fixture populated the per-process cache;
    # without this the entry point would be handed that answer instead of
    # resolving for itself.
    reset_engine()

    observed = ENTRY_POINTS[entry_name](tmp_path / "proj", capsys)

    assert observed == canonical, (
        f"{entry_name} disagrees with wfc.persistence.project_root on "
        f"{shape_name}: canonical {canonical!r}, entry point {observed!r}"
    )
