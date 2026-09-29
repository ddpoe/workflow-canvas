"""Conda Dockerfile generator: pip-layer guard for pip-less envs.

An R-only conda-forge env (no python/pip in the env) has nothing to pip
install; the generated Dockerfile must omit the ``COPY pip-freeze.txt`` +
``RUN pip install`` layers or the image build fails at the pip RUN. A real
freeze keeps the layers verbatim.
"""
from __future__ import annotations

from axiom_annotations import workflow

from wfc.environments.dockerfiles import conda as conda_gen
from wfc.environments.introspect import PIP_MISSING_SENTINEL


@workflow(
    purpose="Conda Dockerfile generation omits the pip COPY/RUN layers when "
            "the captured freeze is empty or the absent-pip sentinel, so "
            "pip-less R conda envs build; a real freeze keeps the layers"
)
def test_conda_generate_pip_layer_guard():
    """Pip-less conda envs (R-only) generate a buildable Dockerfile."""
    for freeze in ("", "   \n", PIP_MISSING_SENTINEL):
        dockerfile = conda_gen.generate(
            env_name="r-env",
            pip_freeze_content=freeze,
        )
        assert "pip install" not in dockerfile, f"freeze={freeze!r}"
        assert "pip-freeze.txt" not in dockerfile, f"freeze={freeze!r}"
        # The conda layers and the permissions stage are untouched. The
        # chmod must run as root (the base ships /opt/conda root-owned;
        # chmod requires ownership) and hand back the base's default
        # non-root user afterwards.
        assert "micromamba install -y -n base -f /opt/explicit-list.txt" in dockerfile
        assert (
            "USER root\n\nRUN chmod -R a+rX /opt/conda\n\nUSER $MAMBA_USER"
            in dockerfile
        ), f"freeze={freeze!r}"

    # Real freeze: pip layers present, verbatim form unchanged.
    dockerfile = conda_gen.generate(
        env_name="py-env",
        pip_freeze_content="requests==2.32.0\n",
    )
    assert "COPY pip-freeze.txt /opt/" in dockerfile
    assert (
        "RUN --mount=type=cache,target=/root/.cache/pip "
        "pip install --no-deps -r /opt/pip-freeze.txt"
    ) in dockerfile
