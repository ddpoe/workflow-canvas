"""Every generated recipe opens its env for reading inside the install RUN.

A step's container runs as the host user (``--user <uid>:<gid>``), so every
file of the env must be world-readable. A ``RUN`` after the install that
touches every file of the env copies the whole env into a new layer: a
separate ``chmod -R`` step took eight to nine minutes on a small conda env
and changed nothing. So the chmod ends the install ``RUN``, over files that
``RUN`` just wrote, and no later ``RUN`` walks the env tree. The conda
recipes install as root, which lets micromamba write the package cache
mount, and hand back the micromamba base's ``$MAMBA_USER`` at the end, so the
image's runtime user is unchanged.

Pure string tests over the generators' output (Tier 1). The real build,
its step times, its size and a non-root run are in
``tests/integration/test_conda_recipe_permissions_build.py``.
"""

from __future__ import annotations

import pytest

from wfc.environments.dockerfiles import conda, pixi, uv

PIP_SET = "requests==2.32.0\n"

#: Per recipe: how to render it, the env tree it must open for reading and
#: the text that names its install command.
_RECIPES = [
    pytest.param(lambda pip: conda.generate("demo", pip), conda.CONDA_ENV_DIR,
                 "micromamba install -y -n base -f /opt/explicit-list.txt",
                 "conda", id="conda"),
    pytest.param(lambda pip: conda.generate_solve("demo", pip),
                 conda.CONDA_ENV_DIR,
                 f"micromamba install -y -n base -f /opt/{conda.SOLVE_SPEC_FILENAME}",
                 "conda", id="conda-solve"),
    pytest.param(lambda pip: pixi.generate("demo", pip), pixi.env_dir_path("demo"),
                 "pixi install --locked --environment demo", "pixi", id="pixi"),
    pytest.param(lambda pip: uv.generate("demo", pip, python_minor="3.12"),
                 uv.VENV_DIR, "uv pip install --python", "uv", id="uv"),
]


def _instructions(dockerfile: str) -> list[tuple[str, str]]:
    """Split a rendered recipe into ``(keyword, rest)`` pairs, comments dropped."""
    out = []
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        keyword, _, rest = stripped.partition(" ")
        out.append((keyword.upper(), rest))
    return out


def _commands(run_rest: str) -> list[str]:
    """The ``&&``-joined commands of one ``RUN``, its ``--mount`` flags dropped."""
    words = run_rest.split(" ")
    while words and words[0].startswith("--"):
        words.pop(0)
    return [part.strip() for part in " ".join(words).split("&&")]


def _changes_permissions(command: str) -> bool:
    return "chmod" in command or "chown" in command


def _install_index(steps, install_text) -> int:
    matches = [i for i, (kw, rest) in enumerate(steps)
               if kw == "RUN" and install_text in rest]
    assert len(matches) == 1, steps
    return matches[0]


@pytest.mark.parametrize("pip", [PIP_SET, ""], ids=["pip-set", "no-pip"])
@pytest.mark.parametrize("render, env_dir, install_text, family", _RECIPES)
def test_no_recipe_has_a_layer_that_only_changes_permissions(
        render, env_dir, install_text, family, pip):
    """No ``RUN`` consists only of permission changes: that layer is the one
    that copied the whole env into the image a second time."""
    for keyword, rest in _instructions(render(pip)):
        if keyword == "RUN":
            assert not all(_changes_permissions(c) for c in _commands(rest)), rest


@pytest.mark.parametrize("pip", [PIP_SET, ""], ids=["pip-set", "no-pip"])
@pytest.mark.parametrize("render, env_dir, install_text, family", _RECIPES)
def test_the_install_run_ends_by_opening_the_env_for_reading(
        render, env_dir, install_text, family, pip):
    """The install ``RUN``'s last command gives every user read access (and
    search access on directories) over the whole env tree, and no later
    ``RUN`` changes permissions at all."""
    steps = _instructions(render(pip))
    install = _install_index(steps, install_text)

    last = _commands(steps[install][1])[-1]
    assert "chmod" in last and "a+rX" in last, last
    assert f" {env_dir}" in last, last

    for keyword, rest in steps[install + 1:]:
        if keyword == "RUN":
            assert not any(_changes_permissions(c) for c in _commands(rest)), rest


@pytest.mark.parametrize("pip", [PIP_SET, ""], ids=["pip-set", "no-pip"])
@pytest.mark.parametrize("render, env_dir, install_text, family", _RECIPES)
def test_installs_run_as_root_and_the_runtime_user_is_the_bases(
        render, env_dir, install_text, family, pip):
    """The conda recipes switch to root before the micromamba install and
    end as ``$MAMBA_USER``, the micromamba base's own user, with no other
    user in between; pixi and uv build as their bases' root and name no
    user."""
    steps = _instructions(render(pip))
    install = _install_index(steps, install_text)
    users = [(i, rest) for i, (kw, rest) in enumerate(steps) if kw == "USER"]

    if family != "conda":
        assert users == [], users
        return
    assert [rest for _, rest in users] == ["root", "$MAMBA_USER"], users
    root_at, back_at = users[0][0], users[1][0]
    assert root_at < install < back_at
    # Every RUN of the recipe runs as root: the pip set, and the solve
    # recipe's export, included.
    runs = [i for i, (kw, _) in enumerate(steps) if kw == "RUN"]
    assert all(root_at < i < back_at for i in runs), steps
    assert back_at == len(steps) - 1, "the user switch back is the recipe's last line"


def test_the_conda_chmod_skips_the_package_cache_mount():
    """The conda package cache is mounted inside the env tree during the
    install ``RUN``; the chmod leaves it alone, so it neither walks a cache
    that other builds share nor changes modes in it."""
    for text in (conda.generate("demo", PIP_SET), conda.generate_solve("demo", PIP_SET)):
        steps = _instructions(text)
        install = next(rest for kw, rest in steps
                       if kw == "RUN" and "micromamba install" in rest)
        assert f"--mount=type=cache,target={conda.CONDA_PKGS_DIR}" in install
        last = _commands(install)[-1]
        assert f"-path {conda.CONDA_PKGS_DIR} -prune" in last, last


def test_every_image_building_backend_has_a_recipe_case():
    """Each backend that builds an image is held to these rules: a new
    backend without a case here fails."""
    from wfc.environments.backends import BACKENDS

    building = {name for name, row in BACKENDS.items() if row.builds_image}
    assert {case.values[3] for case in _RECIPES} == building
