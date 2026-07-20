"""No-wfc contract tests for the user-env Dockerfile generators (ADR-019 G.2).

These are annotation-Tier-1, unmarked pure-function tests. They lock the
*negative* invariant that the request calls the "no-wfc" guarantee: a generated
user-env image must be a single upstream base + the user's declared deps, with
**no wfc in the image** (per ADR-020 — Tier 2 env-var + file contract is the
canonical method interface; pm-client/wfc is opt-in, never pre-installed).

What is asserted here (negatives + the non-root-readable chmod):
  - Exactly one `FROM`, and it is the upstream base — never `local/wfc-base`
    or any two-image shim base, and never the `:latest` floating-everything tag.
  - No `pip install wfc` / `pip install workflow-canvas`.
  - No `COPY wfc/` / `COPY pyproject.toml` (the project source is never copied
    into a user-env image).
  - For pixi/conda, the recipe ends with `RUN chmod -R a+rX <env_dir>` at the
    *backend-native* env prefix (pixi `/{name}/envs/default`, conda
    `/opt/conda` — micromamba installs into `-n base`, whose prefix IS
    /opt/conda; there is no /opt/conda/envs/{name} tree in the built image) —
    the non-root-readable target that pairs with the runtime `--user` fix
    (ADR-019 #9).

Deliberately NOT re-asserted here (already covered by
`tests/test_dockerfile_generation.py`): pixi/conda base digest pinning and the
`pip --no-deps`-after-`pixi install` ordering. This module only owns the no-wfc
negatives + the per-backend chmod target.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wfc.dockerfiles import generate_for_backend


VALID_FREEZE = "numpy==1.26.4\npandas==2.2.1\n"


# Per-backend: the minimal kwargs that drive generate_for_backend, plus the
# backend-native env_dir the chmod must target. Parameterized so the single
# set of no-wfc assertions runs against both image-building backends
# without near-duplicate test bodies.
_BACKEND_CASES = [
    pytest.param(
        "pixi",
        {
            "env_name": "demo",
            "pixi_lock_path": Path("/proj/pixi.lock"),
            "pip_freeze_content": VALID_FREEZE,
        },
        "/demo/envs/default",
        id="pixi",
    ),
    pytest.param(
        "conda",
        {
            "env_name": "demo",
            "explicit_list_path": Path("/proj/explicit-list.txt"),
            "pip_freeze_content": VALID_FREEZE,
        },
        # micromamba's base env prefix — installs go to `-n base`, so the
        # real env tree (and the recorded interpreter's home) is /opt/conda.
        "/opt/conda",
        id="conda",
    ),
]


@pytest.mark.parametrize("backend, kwargs, expected_env_dir", _BACKEND_CASES)
def test_user_env_dockerfile_is_wfc_free_single_upstream(
    backend, kwargs, expected_env_dir
):
    """A generated user-env Dockerfile installs no wfc and builds on a single
    upstream base. For pixi/conda it ends with a non-root-readable chmod of
    the backend-native env dir."""
    dockerfile = generate_for_backend(backend, **kwargs)
    assert dockerfile is not None, f"{backend} should produce a Dockerfile"
    lower = dockerfile.lower()

    from_lines = [
        ln for ln in dockerfile.splitlines() if ln.strip().startswith("FROM")
    ]

    # Single upstream FROM — no multi-stage / two-image shim build.
    assert len(from_lines) == 1, (
        f"{backend}: expected exactly one FROM (single-image, no wfc-base "
        f"shim stage); got {from_lines}"
    )
    from_line = from_lines[0]

    # The base is the upstream image, never a locally-built wfc-base shim,
    # and never the float-everything `:latest` tag.
    assert "local/wfc-base" not in from_line, (
        f"{backend}: FROM must be the upstream base, not a wfc-base shim "
        f"image; got {from_line}"
    )
    assert not from_line.rstrip().endswith(":latest"), (
        f"{backend}: FROM must not pin `:latest`; got {from_line}"
    )

    # No wfc / workflow-canvas ever installed into a user-env image.
    assert "pip install wfc" not in lower
    assert "install workflow-canvas" not in lower
    assert "pip install -e" not in lower

    # The project source tree is never copied into a user-env image.
    assert "copy wfc/" not in lower
    assert "copy wfc " not in lower
    assert "copy pyproject.toml" not in lower

    # The backend-native chmod target (non-root-readable, pairs with --user).
    # pixi/conda chmod a dir their pixi/micromamba step creates.
    assert f"RUN chmod -R a+rX {expected_env_dir}" in dockerfile, (
        f"{backend}: expected `RUN chmod -R a+rX {expected_env_dir}` "
        f"(non-root-readable env dir, pairs with --user); got:\n{dockerfile}"
    )
    # And nowhere does the (rejected) uniform /opt/user-env prefix appear —
    # main parameterizes the env dir per backend by {env_name}.
    assert "/opt/user-env" not in dockerfile


def test_byo_backend_has_no_dockerfile():
    """BYO uses the upstream image as-is: generate_for_backend returns None,
    so there is no user-env image content to assert wfc-freeness against."""
    result = generate_for_backend(
        "byo",
        env_name="demo",
        pip_freeze_content=VALID_FREEZE,
        image="docker.io/library/python:3.12-slim",
    )
    assert result is None
