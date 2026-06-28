"""Layer A — cache-key sensitivity matrix (US-4).

The provenance promise is that a run's cache key reacts to *everything that
matters* (code, params, inputs, env) and to *nothing that doesn't* (the order
in which param keys or upstream inputs happen to be enumerated). This single
parametrized test drives the real fingerprint functions in ``wfc/version.py``
end-to-end and proves both halves of that promise.

Each parametrized case is a behavior spec, not a regression snapshot: a
failing assertion means a fingerprint function stopped reacting (or started
over-reacting) to one of its load-bearing inputs.

Axes proven LOAD-BEARING (changing the axis busts the cache key):
  - code    -- edit a .py in the registered method source dir
  - params  -- change a param *value*
  - inputs  -- change an upstream ``Run.cache_key`` (NOT ``RunOutput.content_hash``;
               the input fingerprint chains on cache_key, so deferred archiving /
               NULL content_hash never perturbs it — see edge cases in the pitch)
  - env     -- change the env content blob hashed by ``store_env_content``

Axes proven IRRELEVANT (reordering does NOT bust the cache key):
  - param-key order        -- json.dumps(params, sort_keys=True)
  - upstream-input order   -- sorted() inside build_input_fingerprint (load-bearing)

No Docker. Uses the in-process ``tmp_project`` fixture (git repo + DB).
"""

from pathlib import Path

import pytest

from wfc.database import get_session
from wfc.models import Method, Module, Run
from wfc.version import (
    build_cache_key,
    build_code_fingerprint,
    build_input_fingerprint,
    store_env_content,
)


# =============================================================================
# Helpers
# =============================================================================

def _setup_dvc_config(project_root: Path) -> None:
    """Write a [dvc] config and init the local DVC cache (for store_env_content)."""
    remote_dir = project_root / "dvc_remote"
    remote_dir.mkdir(parents=True, exist_ok=True)
    config_path = project_root / ".wfc" / "wf-canvas.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = (project_root / ".wfc" / "wfc.db").as_posix()
    config_path.write_text(
        f'[database]\nurl = "sqlite:///{db_path}"\n\n'
        f'[project]\nname = "test"\n\n'
        f'[dvc]\nremote_type = "local"\n'
        f'remote_path = "{remote_dir.as_posix()}"\nauto_init = true\n'
    )
    from wfc.provenance import init_dvc
    init_dvc(project_root, {"url": str(remote_dir)})


def _make_method_source(project_dir: Path, name: str = "m_sens") -> Path:
    """Create a minimal method source dir with one .py file for fingerprinting."""
    method_dir = Path(project_dir) / "methods" / name
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / f"{name}.py").write_text("def main():\n    return 1\n")
    return method_dir


def _seed_upstream_run(cache_key: str) -> int:
    """Insert a Module/Method/Run chain with a given Run.cache_key; return run id.

    The input fingerprint chains on ``Run.cache_key``, so a unique cache_key per
    upstream run is what makes the input axis observable.
    """
    with get_session() as session:
        module = Module(name=f"mod_{cache_key[:6]}", description="x")
        session.add(module)
        session.commit()
        session.refresh(module)
        method = Method(
            name=f"meth_{cache_key[:6]}",
            module_id=module.id,
            script_path="methods/x/x.py",
            env="container:demo",
        )
        session.add(method)
        session.commit()
        session.refresh(method)
        run = Run(method_id=method.id, status="completed", cache_key=cache_key)
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


# A fixed baseline for the three components we hold constant while varying one axis.
_CODE_FP = "a" * 64
_INPUT_FP = "b" * 64
_ENV_FP = "c" * 32
_PARAMS = {"threshold": 0.5, "normalize": True}


# =============================================================================
# US-4: cache-key sensitivity matrix
# =============================================================================

@pytest.mark.parametrize(
    "axis",
    ["code", "params", "inputs", "env"],
)
def test_cache_key_reacts_to_load_bearing_axis(tmp_project, axis):
    """Changing any of code/params/inputs/env busts the cache key.

    Each axis is exercised through its *real* fingerprint function so the test
    fails if any of build_code_fingerprint / build_input_fingerprint /
    store_env_content / build_cache_key stops folding its component into the key.
    """
    if axis == "code":
        method_dir = _make_method_source(tmp_project)
        fp_before = build_code_fingerprint(method_dir)
        # Edit a .py in the method source dir -> code identity must change.
        (method_dir / "m_sens.py").write_text("def main():\n    return 2\n")
        fp_after = build_code_fingerprint(method_dir)
        assert fp_before != fp_after, "editing method source did not change code fingerprint"
        key_before = build_cache_key(fp_before, _PARAMS, _INPUT_FP, _ENV_FP)
        key_after = build_cache_key(fp_after, _PARAMS, _INPUT_FP, _ENV_FP)
        assert key_before != key_after

    elif axis == "params":
        key_before = build_cache_key(_CODE_FP, {"threshold": 0.5}, _INPUT_FP, _ENV_FP)
        key_after = build_cache_key(_CODE_FP, {"threshold": 0.9}, _INPUT_FP, _ENV_FP)
        assert key_before != key_after, "changing a param value did not change the key"

    elif axis == "inputs":
        # Vary an upstream Run.cache_key (NOT content_hash): the input fingerprint
        # chains on cache_key, so deferred archiving / NULL content_hash is
        # irrelevant here. Changing the upstream cache_key MUST bust the key.
        up_a = _seed_upstream_run("0" * 64)
        up_b = _seed_upstream_run("1" * 64)
        fp_before = build_input_fingerprint([up_a], sample_ids=[])
        fp_after = build_input_fingerprint([up_b], sample_ids=[])
        assert fp_before != fp_after, "distinct upstream cache_keys produced the same input fingerprint"
        key_before = build_cache_key(_CODE_FP, _PARAMS, fp_before, _ENV_FP)
        key_after = build_cache_key(_CODE_FP, _PARAMS, fp_after, _ENV_FP)
        assert key_before != key_after

    elif axis == "env":
        _setup_dvc_config(tmp_project)
        fp_before = store_env_content("packages=numpy==1.0\n", tmp_project)
        fp_after = store_env_content("packages=numpy==2.0\n", tmp_project)
        assert fp_before != fp_after, "distinct env content produced the same env fingerprint"
        key_before = build_cache_key(_CODE_FP, _PARAMS, _INPUT_FP, fp_before)
        key_after = build_cache_key(_CODE_FP, _PARAMS, _INPUT_FP, fp_after)
        assert key_before != key_after


@pytest.mark.parametrize(
    "axis",
    ["param_key_order", "upstream_input_order"],
)
def test_cache_key_ignores_irrelevant_ordering(tmp_project, axis):
    """Reordering param keys or upstream inputs does NOT change the cache key.

    These prove the sorted-determinism guarantees: json.dumps(sort_keys=True)
    in build_cache_key and the load-bearing sorted() in build_input_fingerprint.
    Removing either would silently break cache stability across DB/enumeration
    orderings — this test is the guard.
    """
    if axis == "param_key_order":
        # Same params, keys supplied in a different insertion order.
        key_1 = build_cache_key(_CODE_FP, {"a": 1, "b": 2}, _INPUT_FP, _ENV_FP)
        key_2 = build_cache_key(_CODE_FP, {"b": 2, "a": 1}, _INPUT_FP, _ENV_FP)
        assert key_1 == key_2, "param-key order changed the cache key (sort_keys regression)"

    elif axis == "upstream_input_order":
        up_a = _seed_upstream_run("2" * 64)
        up_b = _seed_upstream_run("3" * 64)
        fp_ab = build_input_fingerprint([up_a, up_b], sample_ids=[])
        fp_ba = build_input_fingerprint([up_b, up_a], sample_ids=[])
        assert fp_ab == fp_ba, "upstream-input order changed the input fingerprint (sorted() regression)"
        # And the full key is stable too.
        assert (
            build_cache_key(_CODE_FP, _PARAMS, fp_ab, _ENV_FP)
            == build_cache_key(_CODE_FP, _PARAMS, fp_ba, _ENV_FP)
        )
