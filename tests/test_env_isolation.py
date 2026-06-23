"""
Unit & integration tests: Shared named environment system.

Story: Methods reference shared environments by typed prefix in method.yaml
(``env: pixi:image-io``, ``env: pixi:agnostic_image_analysis:default``,
``env: conda:cell_pose``).
The python executable is resolved at registration and Snakefile generation time.

Three env types:
  - ``pixi:<name>``              — standalone pixi project (default env)
  - ``pixi:<project>:<env>``     — pixi project with explicit env name
  - ``conda:<name>``             — conda environment by name
  - ``inherit``                  — inherit wfc's Python (default when omitted)

Test coverage:
  - read_config() parses [pixi] root and optional [conda] root
  - parse_method_yaml() extracts env key
  - resolve_python_for_env() dispatches on prefix (pixi 2-part/3-part, conda)
  - _resolve_env() validates named env via config + prefix dispatch
  - generate_snakefile() emits ENV_PYTHON_PATHS with resolved python paths
  - Error cases: zero matches, multiple matches, bare names rejected

These are Tier 2 tests: @workflow(purpose=...) with Step() markers.
"""

from pathlib import Path
import sys

import pytest

from dflow.core.decorators import workflow, Step

from wfc.init import init_project, read_config
from wfc.contracts import parse_method_yaml
from wfc.register import _resolve_env, resolve_python_for_env
from wfc.snakemake_gen import StepDef, PipelineDef, generate_snakefile


# =============================================================================
# Unit: Config parsing — [pixi] section
# =============================================================================

@workflow(
    purpose="A config with [pixi] root = '.pixi' resolves to project_root/.pixi")
def test_read_config_pixi_root_default(tmp_path):
    """[pixi] root = '.pixi' -> resolved to project_root/.pixi."""
    _ = Step(step_num=1, name="Create config with pixi section",
             purpose="Write a wf-canvas.toml with a relative pixi root")
    wfc_dir = tmp_path / ".wfc"
    wfc_dir.mkdir()
    (wfc_dir / "wf-canvas.toml").write_text(
        '[database]\nurl = "sqlite:///test.db"\n\n'
        '[project]\nname = "test"\n\n'
        '[pixi]\nroot = ".pixi"\n'
    )

    _ = Step(step_num=2, name="Verify pixi_root resolution",
             purpose="Confirm pixi_root resolves to project_root/.pixi")
    config = read_config(tmp_path)
    assert "pixi_root" in config
    expected = (tmp_path / ".pixi").resolve()
    assert Path(config["pixi_root"]) == expected


@workflow(
    purpose="A config with an absolute pixi root uses that path as-is")
def test_read_config_pixi_root_absolute(tmp_path):
    """[pixi] root = '/some/abs/path' -> that absolute path."""
    _ = Step(step_num=1, name="Create config with absolute pixi root",
             purpose="Write a wf-canvas.toml with an absolute pixi root path")
    wfc_dir = tmp_path / ".wfc"
    wfc_dir.mkdir()
    abs_path = str(tmp_path / "shared_envs")
    toml_path = abs_path.replace("\\", "/")
    (wfc_dir / "wf-canvas.toml").write_text(
        '[database]\nurl = "sqlite:///test.db"\n\n'
        '[project]\nname = "test"\n\n'
        f'[pixi]\nroot = "{toml_path}"\n'
    )

    _ = Step(step_num=2, name="Verify absolute pixi root",
             purpose="Confirm pixi_root matches the absolute path")
    config = read_config(tmp_path)
    assert Path(config["pixi_root"]) == Path(abs_path).resolve()


@workflow(
    purpose="A config with no [pixi] section defaults to .pixi")
def test_read_config_pixi_root_missing_section(tmp_path):
    """No [pixi] section -> pixi_root defaults to project_root/.pixi."""
    _ = Step(step_num=1, name="Create config without pixi section",
             purpose="Write a wf-canvas.toml with no pixi section")
    wfc_dir = tmp_path / ".wfc"
    wfc_dir.mkdir()
    (wfc_dir / "wf-canvas.toml").write_text(
        '[database]\nurl = "sqlite:///test.db"\n\n'
        '[project]\nname = "test"\n'
    )

    _ = Step(step_num=2, name="Verify default pixi root",
             purpose="Confirm pixi_root defaults to .pixi under project root")
    config = read_config(tmp_path)
    expected = (tmp_path / ".pixi").resolve()
    assert Path(config["pixi_root"]) == expected


# =============================================================================
# Regression: init_project writes UTF-8 config that read_config can parse
# =============================================================================

@workflow(
    purpose="Config written by init_project is valid UTF-8 that tomllib can parse "
            "and includes pixi_root")
def test_init_project_config_roundtrip_utf8(tmp_path):
    """init_project writes UTF-8 config -> read_config parses it successfully."""
    _ = Step(step_num=1, name="Scaffold project via init_project",
             purpose="Create a project whose wf-canvas.toml is written by init_project")
    init_project(tmp_path, init_git=True)

    _ = Step(step_num=2, name="Verify read_config parses the generated config",
             purpose="Confirm the config file is valid UTF-8 by reading it with tomllib")
    config = read_config(tmp_path)
    assert config["database_url"] is not None
    assert config["project_name"] == tmp_path.name
    assert "pixi_root" in config

    _ = Step(step_num=3, name="Verify config file is UTF-8 encoded",
             purpose="Read the raw bytes and confirm UTF-8 decoding succeeds")
    config_path = tmp_path / ".wfc" / "wf-canvas.toml"
    raw_bytes = config_path.read_bytes()
    raw_bytes.decode("utf-8")


# =============================================================================
# Unit: Contract parsing — env key
# =============================================================================

@workflow(
    purpose="parse_method_yaml extracts the env key from method.yaml")
def test_parse_method_yaml_env_key(tmp_path):
    """method.yaml with env: pixi:image-io -> contract dict includes env."""
    _ = Step(step_num=1, name="Create method.yaml with env key",
             purpose="Write a method.yaml that declares a named shared environment")
    method_dir = tmp_path / "my_method"
    method_dir.mkdir()
    (method_dir / "method.yaml").write_text(
        "env: pixi:image-io\n"
        "inputs:\n  data:\n    type: csv\n"
        "outputs:\n  result:\n    type: csv\n"
    )

    _ = Step(step_num=2, name="Verify env key in parsed contract",
             purpose="Confirm parse_method_yaml includes the env field")
    contract = parse_method_yaml(method_dir)
    assert contract is not None
    assert contract["env"] == "pixi:image-io"


@workflow(
    purpose="parse_method_yaml defaults env to 'inherit' when not specified")
def test_parse_method_yaml_env_default_inherit(tmp_path):
    """method.yaml without env key -> env defaults to 'inherit'."""
    _ = Step(step_num=1, name="Create method.yaml without env key",
             purpose="Write a method.yaml that does not declare an env")
    method_dir = tmp_path / "my_method"
    method_dir.mkdir()
    (method_dir / "method.yaml").write_text(
        "inputs:\n  data:\n    type: csv\n"
        "outputs:\n  result:\n    type: csv\n"
    )

    _ = Step(step_num=2, name="Verify env defaults to inherit",
             purpose="Confirm parse_method_yaml defaults env to 'inherit'")
    contract = parse_method_yaml(method_dir)
    assert contract is not None
    assert contract["env"] == "inherit"


# =============================================================================
# Unit: Glob-based env resolution
# =============================================================================

def _make_fake_env(pixi_root, env_name, hash_suffix="abc123"):
    """Helper: create a fake pixi env directory with a python executable."""
    env_dir = pixi_root / f"{env_name}-{hash_suffix}" / "envs" / "default"
    if sys.platform == "win32":
        python = env_dir / "Scripts" / "python.exe"
    else:
        python = env_dir / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("fake python")
    return python


@workflow(
    purpose="resolve_python_for_env finds a single matching pixi env and returns the python path")
def test_resolve_python_single_match(tmp_path):
    """One matching env dir -> returns python path."""
    _ = Step(step_num=1, name="Create fake pixi env",
             purpose="Set up a single env directory with python executable")
    pixi_root = tmp_path / ".pixi"
    expected_python = _make_fake_env(pixi_root, "image-io")

    _ = Step(step_num=2, name="Resolve python",
             purpose="Confirm resolve_python_for_env returns the correct path")
    result = resolve_python_for_env("pixi:image-io", pixi_root=pixi_root)
    assert result == expected_python.resolve()


@workflow(
    purpose="resolve_python_for_env raises ValueError when no pixi env matches")
def test_resolve_python_zero_matches(tmp_path):
    """No matching env dir -> ValueError."""
    _ = Step(step_num=1, name="Create empty pixi root",
             purpose="Set up pixi root with no matching env directories")
    pixi_root = tmp_path / ".pixi"
    pixi_root.mkdir()

    _ = Step(step_num=2, name="Attempt resolution",
             purpose="Confirm ValueError is raised on zero matches")
    with pytest.raises(ValueError, match="No pixi environment found"):
        resolve_python_for_env("pixi:image-io", pixi_root=pixi_root)


@workflow(
    purpose="resolve_python_for_env raises ValueError when multiple pixi envs match")
def test_resolve_python_multiple_matches(tmp_path):
    """Multiple matching env dirs -> ValueError listing matches."""
    _ = Step(step_num=1, name="Create two matching env dirs",
             purpose="Set up pixi root with two env directories for the same name")
    pixi_root = tmp_path / ".pixi"
    _make_fake_env(pixi_root, "image-io", "hash1")
    _make_fake_env(pixi_root, "image-io", "hash2")

    _ = Step(step_num=2, name="Attempt resolution",
             purpose="Confirm ValueError is raised listing both matches")
    with pytest.raises(ValueError, match="Multiple pixi environments"):
        resolve_python_for_env("pixi:image-io", pixi_root=pixi_root)


@workflow(
    purpose="resolve_python_for_env raises ValueError when env dir exists but python is missing")
def test_resolve_python_missing_executable(tmp_path):
    """Env dir exists but no python binary -> ValueError."""
    _ = Step(step_num=1, name="Create env dir without python",
             purpose="Set up pixi env directory structure without the python binary")
    pixi_root = tmp_path / ".pixi"
    env_dir = pixi_root / "image-io-abc123" / "envs" / "default"
    env_dir.mkdir(parents=True)
    # Do NOT create python executable

    _ = Step(step_num=2, name="Attempt resolution",
             purpose="Confirm ValueError mentioning missing python")
    with pytest.raises(ValueError, match="Python executable not found"):
        resolve_python_for_env("pixi:image-io", pixi_root=pixi_root)


@workflow(
    purpose="resolve_python_for_env rejects bare env names without a typed prefix")
def test_resolve_python_bare_name_rejected(tmp_path):
    """Bare name (no prefix) -> ValueError with guidance."""
    pixi_root = tmp_path / ".pixi"
    _make_fake_env(pixi_root, "image-io")

    with pytest.raises(ValueError, match="Unknown env spec"):
        resolve_python_for_env("image-io", pixi_root=pixi_root)


@workflow(
    purpose="resolve_python_for_env resolves pixi:<project>:<env> to {project}-*/envs/{env}")
def test_resolve_python_pixi_project_env(tmp_path):
    """pixi:<project>:<env> searches {project}-*/envs/{env} in pixi root."""
    _ = Step(step_num=1, name="Create fake pixi project env",
             purpose="Set up an env dir matching {project}-*/envs/{env} pattern")
    pixi_root = tmp_path / ".pixi"
    env_dir = pixi_root / "agnostic_image_analysis-abc123" / "envs" / "default"
    if sys.platform == "win32":
        python = env_dir / "Scripts" / "python.exe"
    else:
        python = env_dir / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("fake python")

    _ = Step(step_num=2, name="Resolve project env",
             purpose="Confirm pixi:agnostic_image_analysis:default resolves correctly")
    result = resolve_python_for_env(
        "pixi:agnostic_image_analysis:default", pixi_root=pixi_root)
    assert result == python.resolve()


@workflow(
    purpose="pixi:<project>:<env> works with non-default env names like 'gpu'")
def test_resolve_python_pixi_project_env_nondefault(tmp_path):
    """pixi:myproject:gpu resolves {pixi_root}/myproject-*/envs/gpu."""
    _ = Step(step_num=1, name="Create fake pixi project with gpu env",
             purpose="Set up a project env dir with a non-default env name")
    pixi_root = tmp_path / ".pixi"
    env_dir = pixi_root / "myproject-abc123" / "envs" / "gpu"
    if sys.platform == "win32":
        python = env_dir / "Scripts" / "python.exe"
    else:
        python = env_dir / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("fake python")

    _ = Step(step_num=2, name="Resolve project:env",
             purpose="Confirm pixi:myproject:gpu resolves correctly")
    result = resolve_python_for_env("pixi:myproject:gpu", pixi_root=pixi_root)
    assert result == python.resolve()


@workflow(
    purpose="resolve_python_for_env resolves conda: envs by name")
def test_resolve_python_conda(tmp_path):
    """conda:<name> finds python in {conda_root}/envs/<name>."""
    _ = Step(step_num=1, name="Create fake conda env",
             purpose="Set up a conda env directory with python executable")
    conda_root = tmp_path / "anaconda3"
    env_dir = conda_root / "envs" / "cell_pose"
    if sys.platform == "win32":
        python = env_dir / "python.exe"
    else:
        python = env_dir / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("fake python")

    _ = Step(step_num=2, name="Resolve conda env",
             purpose="Confirm conda:cell_pose resolves to the conda env python")
    result = resolve_python_for_env("conda:cell_pose", conda_root=conda_root)
    assert result == python.resolve()


@workflow(
    purpose="resolve_python_for_env raises when conda env does not exist")
def test_resolve_python_conda_missing(tmp_path):
    """conda:<name> with no matching env -> ValueError."""
    conda_root = tmp_path / "anaconda3"
    conda_root.mkdir()

    with pytest.raises(ValueError, match="No conda environment"):
        resolve_python_for_env("conda:cell_pose", conda_root=conda_root)


@workflow(
    purpose="_resolve_env reads pixi_root from config and validates via glob")
def test_resolve_env_via_config(tmp_path):
    """_resolve_env reads config, finds env via glob, returns env spec."""
    _ = Step(step_num=1, name="Set up project with pixi config and fake env",
             purpose="Create wf-canvas.toml with pixi root and a matching env")
    wfc_dir = tmp_path / ".wfc"
    wfc_dir.mkdir()
    pixi_root = tmp_path / ".pixi"
    _make_fake_env(pixi_root, "image-io")
    (wfc_dir / "wf-canvas.toml").write_text(
        '[database]\nurl = "sqlite:///test.db"\n\n'
        '[project]\nname = "test"\n\n'
        '[pixi]\nroot = ".pixi"\n',
        encoding="utf-8",
    )

    _ = Step(step_num=2, name="Resolve env",
             purpose="Confirm _resolve_env returns the full prefixed env spec")
    result = _resolve_env("pixi:image-io", tmp_path)
    assert result == "pixi:image-io"


@workflow(
    purpose="_resolve_env raises when no matching env found in pixi root")
def test_resolve_env_no_match(tmp_path):
    """No matching env in pixi root -> ValueError."""
    _ = Step(step_num=1, name="Set up project with empty pixi root",
             purpose="Create wf-canvas.toml with pixi root but no matching envs")
    wfc_dir = tmp_path / ".wfc"
    wfc_dir.mkdir()
    pixi_root = tmp_path / ".pixi"
    pixi_root.mkdir()
    (wfc_dir / "wf-canvas.toml").write_text(
        '[database]\nurl = "sqlite:///test.db"\n\n'
        '[project]\nname = "test"\n\n'
        '[pixi]\nroot = ".pixi"\n',
        encoding="utf-8",
    )

    _ = Step(step_num=2, name="Attempt resolution",
             purpose="Confirm ValueError is raised")
    with pytest.raises(ValueError, match="No pixi environment found"):
        _resolve_env("pixi:image-io", tmp_path)


# =============================================================================
# Integration: Snakefile generation with named envs
# =============================================================================

@workflow(
    purpose="The generated Snakefile contains ENV_NAMES dict and ENV_PYTHON_PATHS "
            "with resolved python paths for named environments")
def test_snakefile_env_names_dict(wfc_root, tmp_path):
    """Named env steps produce ENV_PYTHON_PATHS with resolved python paths."""
    _ = Step(step_num=1, name="Set up env config",
             purpose="Create project config with pixi root and a fake env with python")
    project_dir = Path(wfc_root)
    wfc_dir = project_dir / ".wfc"
    wfc_dir.mkdir(exist_ok=True)
    pixi_root = project_dir / ".pixi"
    fake_python = _make_fake_env(pixi_root, "image-io")
    (wfc_dir / "wf-canvas.toml").write_text(
        '[database]\nurl = "sqlite:///test.db"\n\n'
        '[project]\nname = "test"\n\n'
        '[pixi]\nroot = ".pixi"\n'
    )

    _ = Step(step_num=2, name="Define pipeline with named env step",
             purpose="Create a pipeline with one named-env step and one inherit step")
    pipeline = PipelineDef(
        steps=[
            StepDef(
                method_name="preprocess",
                module_name="demo",
                script_path="methods/preprocess/preprocess.py",
                params={},
                env="pixi:image-io"),
            StepDef(
                method_name="filter_cells",
                module_name="demo",
                script_path="methods/filter_cells/filter_cells.py",
                params={},
                depends_on=["preprocess"],
                env="inherit"),
        ],
        samples=["Pa16c"])

    _ = Step(step_num=3, name="Generate Snakefile with project dir",
             purpose="Produce the Snakefile text, passing project_dir for env resolution")
    snakefile = generate_snakefile(pipeline, wfc_root, project_root=wfc_root)

    _ = Step(step_num=4, name="Verify ENV_NAMES and ENV_PYTHON_PATHS",
             purpose="Confirm the Snakefile has ENV_NAMES and resolved python paths")
    assert "ENV_NAMES" in snakefile
    assert "'preprocess': 'pixi:image-io'" in snakefile
    assert "'filter_cells': 'inherit'" in snakefile
    # The resolved python path should appear in ENV_PYTHON_PATHS
    assert "ENV_PYTHON_PATHS" in snakefile
    resolved_python = str(fake_python.resolve()).replace("\\", "/")
    assert resolved_python in snakefile or str(fake_python.resolve()) in snakefile


@workflow(
    purpose="An all-inherit pipeline produces the same Snakefile structure "
            "as before (backward compatibility)")
def test_snakefile_inherit_backward_compat(wfc_root):
    """All-inherit pipeline -> ENV_NAMES with all 'inherit', no pixi dispatch."""
    _ = Step(step_num=1, name="Define all-inherit pipeline",
             purpose="Create a pipeline where all steps inherit the caller's Python")
    pipeline = PipelineDef(
        steps=[
            StepDef(
                method_name="preprocess",
                module_name="demo",
                script_path="methods/preprocess/preprocess.py",
                params={},
                env="inherit"),
            StepDef(
                method_name="filter_cells",
                module_name="demo",
                script_path="methods/filter_cells/filter_cells.py",
                params={},
                depends_on=["preprocess"],
                env="inherit"),
        ],
        samples=["Pa16c"])

    _ = Step(step_num=2, name="Generate Snakefile",
             purpose="Produce the Snakefile text")
    snakefile = generate_snakefile(pipeline, wfc_root, project_root=wfc_root)

    _ = Step(step_num=3, name="Verify all-inherit output",
             purpose="Confirm all entries are 'inherit' and the run_method dispatches correctly")
    assert "ENV_NAMES" in snakefile
    assert "'preprocess': 'inherit'" in snakefile
    assert "'filter_cells': 'inherit'" in snakefile
    # run_method should still use sys.executable for inherit
    assert "sys.executable" in snakefile


# =============================================================================
# Regression: wfc._ensure_wfc_shim() — narrow PYTHONPATH for pixi subprocesses
# =============================================================================
#
# See wfc.cli._ensure_wfc_shim docstring and ADR-008 for background.  Before
# the shim, wfc run-step prepended the host venv's site-packages to the pixi
# subprocess PYTHONPATH, which loaded host numpy/pandas with a different
# CPython ABI than the pixi env's interpreter (classic symptom:
# "_multiarray_umath.cp312-win_amd64.pyd ... Python version is ... 3.10").
# These tests lock in the shim's structural invariants so a future cleanup
# can't accidentally re-widen the PYTHONPATH.


@workflow(purpose="Shim root exposes only the wfc package, not its siblings")
def test_ensure_wfc_shim_contains_only_wfc(wfc_root):
    import os
    from wfc.cli import _ensure_wfc_shim

    _ = Step(step_num=1, name="Build shim",
             purpose="Create (or reuse) the cached shim directory")
    shim_root = _ensure_wfc_shim()

    _ = Step(step_num=2, name="Verify sole entry is wfc/",
             purpose="Confirm no foreign site-packages siblings leaked in")
    entries = sorted(os.listdir(shim_root))
    assert entries == ["wfc"], (
        f"shim root must contain only wfc/, got: {entries} — "
        "any sibling here would be visible on the pixi env's sys.path"
    )

    _ = Step(step_num=3, name="Verify shim wfc/ has only __init__.py",
             purpose="Confirm the shim is a stub, not a copy of wfc's tree")
    wfc_entries = sorted(os.listdir(shim_root / "wfc"))
    assert wfc_entries == ["__init__.py"], (
        f"shim wfc/ must contain only __init__.py, got: {wfc_entries}"
    )


@workflow(purpose="Shim __init__.py rewrites __path__ to the real wfc dir")
def test_ensure_wfc_shim_rewrites_path(wfc_root):
    import wfc as _wfc_pkg
    from wfc.cli import _ensure_wfc_shim

    _ = Step(step_num=1, name="Resolve real wfc path",
             purpose="Locate the authoritative wfc package dir via its __file__")
    real_wfc_dir = Path(_wfc_pkg.__file__).parent.resolve()

    _ = Step(step_num=2, name="Read shim __init__.py",
             purpose="Inspect the auto-generated shim contents")
    shim_root = _ensure_wfc_shim()
    content = (shim_root / "wfc" / "__init__.py").read_text(encoding="utf-8")

    _ = Step(step_num=3, name="Assert __path__ points at real wfc dir",
             purpose="Confirm the __path__ rewrite is in place")
    assert "__path__" in content
    # The shim embeds the path via repr(str(real_wfc_dir)) so Windows
    # backslashes appear doubled — compare against the same form the
    # generator uses, not the raw path string.
    assert repr(str(real_wfc_dir)) in content


@workflow(purpose="Shim build is idempotent across repeated calls")
def test_ensure_wfc_shim_idempotent(wfc_root):
    from wfc.cli import _ensure_wfc_shim

    _ = Step(step_num=1, name="Call twice",
             purpose="Invoke the builder back-to-back to check caching")
    r1 = _ensure_wfc_shim()
    r2 = _ensure_wfc_shim()

    _ = Step(step_num=2, name="Assert same path and still exists",
             purpose="Two calls must return the same path and leave the shim intact")
    assert r1 == r2
    assert (r1 / "wfc" / "__init__.py").exists()


@workflow(purpose="Shim cache lives under project root, not %LOCALAPPDATA% (MS Store UWP safe)")
def test_ensure_wfc_shim_cache_under_project_root(wfc_root, monkeypatch):
    from wfc.cli import _ensure_wfc_shim
    from wfc.database import project_root

    _ = Step(step_num=1, name="Ensure no env override active",
             purpose="Test the default cache location, not WFC_SHIM_CACHE_DIR")
    monkeypatch.delenv("WFC_SHIM_CACHE_DIR", raising=False)

    _ = Step(step_num=2, name="Build shim",
             purpose="Invoke the builder with default cache location")
    shim_root = _ensure_wfc_shim()

    _ = Step(step_num=3, name="Assert shim lives under project root",
             purpose="MS Store Python virtualizes %LOCALAPPDATA% — platformdirs is unsafe")
    proot = project_root().resolve()
    assert proot in shim_root.resolve().parents, (
        f"shim cache must live under project_root() (got {shim_root}); "
        f"platformdirs under %LOCALAPPDATA% is virtualized by UWP on MS Store "
        f"Python, making the shim invisible to non-Store subprocesses"
    )


@workflow(purpose="WFC_SHIM_CACHE_DIR env var overrides the default cache location")
def test_ensure_wfc_shim_env_override(tmp_path, monkeypatch):
    from wfc.cli import _ensure_wfc_shim

    _ = Step(step_num=1, name="Set WFC_SHIM_CACHE_DIR to a tmp path",
             purpose="Exercise the CI / read-only-checkout escape hatch")
    override = tmp_path / "custom_shim_cache"
    monkeypatch.setenv("WFC_SHIM_CACHE_DIR", str(override))

    _ = Step(step_num=2, name="Build shim",
             purpose="Run the builder with the override in effect")
    shim_root = _ensure_wfc_shim()

    _ = Step(step_num=3, name="Assert shim sits inside the override",
             purpose="Confirm WFC_SHIM_CACHE_DIR took precedence over project root")
    assert override.resolve() in shim_root.resolve().parents
    assert (shim_root / "wfc" / "__init__.py").exists()


@workflow(purpose="Unwritable cache dir raises a clear RuntimeError naming WFC_SHIM_CACHE_DIR")
def test_ensure_wfc_shim_unwritable_cache(tmp_path, monkeypatch):
    from wfc.cli import _ensure_wfc_shim

    _ = Step(step_num=1, name="Point WFC_SHIM_CACHE_DIR at an unwritable path",
             purpose="Create a file and try to mkdir a subdirectory inside it")
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    monkeypatch.setenv("WFC_SHIM_CACHE_DIR", str(blocker / "subdir"))

    _ = Step(step_num=2, name="Attempt to build shim",
             purpose="Verify RuntimeError mentions WFC_SHIM_CACHE_DIR so users know the escape hatch")
    with pytest.raises(RuntimeError, match="WFC_SHIM_CACHE_DIR"):
        _ensure_wfc_shim()


@workflow(purpose="Subprocess with PYTHONPATH=shim can import wfc.method")
def test_ensure_wfc_shim_subprocess_import(wfc_root):
    import subprocess
    import os

    from wfc.cli import _ensure_wfc_shim

    _ = Step(step_num=1, name="Build shim",
             purpose="Create (or reuse) the cached shim directory")
    shim_root = _ensure_wfc_shim()

    _ = Step(step_num=2, name="Spawn subprocess with only the shim on PYTHONPATH",
             purpose="Verify the stub __path__ resolves wfc.method at runtime")
    # Subprocess inherits sys.executable (host venv) so pandas/stdlib come
    # from the host's own site-packages via the interpreter's default path.
    # The shim on PYTHONPATH only adds wfc; it must not shadow anything.
    env = {**os.environ, "PYTHONPATH": str(shim_root)}
    result = subprocess.run(
        [sys.executable, "-c",
         "from wfc.method import wfc_method; "
         "import wfc; "
         "print('WFC_PATH=', wfc.__path__); "
         "print('OK')"],
        env=env, capture_output=True, text=True,
    )

    _ = Step(step_num=3, name="Assert import succeeded and __path__ was rewritten",
             purpose="Check subprocess exit code, stdout marker, and __path__ value")
    assert result.returncode == 0, (
        f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "OK" in result.stdout
    # The shim rewrites wfc.__path__ to the real wfc dir, so the reported
    # __path__ must NOT point at the shim root.
    assert str(shim_root / "wfc") not in result.stdout, (
        "shim leaked into wfc.__path__; __path__ rewrite failed"
    )
