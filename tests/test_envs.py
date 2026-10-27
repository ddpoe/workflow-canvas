"""
Subsystem Tests: .wfc/envs.json manifest

Covers:
  - validate_container_ref: shape check accepts digest-pinned refs and
    rejects floating tags / malformed scheme / missing host (Tier 1).
  - load_manifest: missing file returns empty manifest; unknown
    schema_version raises with a clear message (Tier 1).
  - list_envs / get / delete: read + remove against a sample manifest
    file written directly to .wfc/envs.json (Tier 2).
  - wfc.cli list-envs / show-env / delete-env happy paths via the in-process
    cli runner (Tier 3).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.fakes import stub_interactive_prompt

from axiom_annotations import workflow, Step


VALID_DIGEST = "a" * 64
VALID_REF = f"docker://ghcr.io/dante/image-io@sha256:{VALID_DIGEST}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_manifest(project_dir: Path, envs: dict, schema_version: int = 3) -> None:
    """Write a manifest dict to ``<project>/.wfc/envs.json``."""
    (project_dir / ".wfc").mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": schema_version, "envs": envs}
    (project_dir / ".wfc" / "envs.json").write_text(json.dumps(payload, indent=2))


def _sample_record(container: str = VALID_REF) -> dict:
    """Return a sample record-dict (the VALUE side of an envs[name] entry).

    The env name is the KEY of the ``envs`` dict, not a field inside the
    record — so this helper does NOT include a ``name`` key.
    """
    return {
        "backend": "pixi",
        "source": "pixi.toml",
        "container": container,
        "env_fingerprint": "deadbeef" * 8,
        "built_from_lock": "pixi.lock",
        "built_at": "2026-05-16T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# Tier 1: validate_container_ref
# ---------------------------------------------------------------------------

def test_validate_container_ref_accepts_digest_pin():
    from wfc.contracts import validate_container_ref
    validate_container_ref(VALID_REF)  # no raise


def test_validate_container_ref_accepts_local_namespace_ref():
    """The pixi/conda build path records locally-built images under the
    ``local/`` namespace as ``docker://local/<name>@sha256:<hex>`` (see
    wfc.environments.register). That form MUST satisfy the shape check — otherwise
    register-env writes a manifest ref that register-method then rejects.
    A bare ``<name>@sha256`` ref, with no scheme and no namespace, would fail it.
    """
    from wfc.contracts import validate_container_ref
    validate_container_ref(f"docker://local/cc-mapping@sha256:{VALID_DIGEST}")


@pytest.mark.parametrize("bad_ref", [
    "docker://ghcr.io/dante/image-io:latest",            # floating tag
    "docker://ghcr.io/dante/image-io:v1",                # floating tag
    "ghcr.io/dante/image-io@sha256:" + ("a" * 64),       # missing scheme
    "docker://@sha256:" + ("a" * 64),                    # missing host
    "docker://ghcr.io/dante/image-io@sha256:tooshort",   # bad digest length
    "",                                                   # empty
])
def test_validate_container_ref_rejects_non_digest_pin(bad_ref):
    from wfc.contracts import validate_container_ref
    with pytest.raises(ValueError, match="digest-pinned|non-empty"):
        validate_container_ref(bad_ref)


# ---------------------------------------------------------------------------
# Tier 1: load_manifest edge cases
# ---------------------------------------------------------------------------

def test_load_manifest_missing_file_returns_empty(tmp_path):
    (tmp_path / ".wfc").mkdir()
    from wfc.environments import load_manifest
    manifest = load_manifest(tmp_path)
    assert manifest == {"schema_version": 3, "envs": {}}


def test_load_manifest_unknown_schema_version_raises(tmp_path):
    _write_manifest(tmp_path, envs={}, schema_version=99)
    from wfc.environments import load_manifest
    with pytest.raises(ValueError, match="schema_version"):
        load_manifest(tmp_path)


# ---------------------------------------------------------------------------
# Tier 2: list_envs / get / delete
# ---------------------------------------------------------------------------

@workflow(purpose="list_envs returns (name, record) tuples sorted by name; "
                  "missing manifest yields empty list")
def test_list_envs_empty_and_populated(tmp_path):
    from wfc.environments import list_envs
    (tmp_path / ".wfc").mkdir()

    # No manifest file -> empty
    assert list_envs(tmp_path) == []

    # Populated -> 2 sorted (name, EnvRecord) tuples
    _write_manifest(tmp_path, envs={
        "zeta": _sample_record(),
        "alpha": _sample_record(),
    })
    records = list_envs(tmp_path)
    assert [name for name, _ in records] == ["alpha", "zeta"]
    assert records[0][1].container == VALID_REF
    assert records[0][1].backend == "pixi"


@workflow(purpose="get returns full record on hit, None on miss")
def test_get_hit_and_miss(tmp_path):
    from wfc.environments import get
    _write_manifest(tmp_path, envs={"image-io": _sample_record()})

    record = get("image-io", tmp_path)
    assert record is not None
    # The name is the dict key in .wfc/envs.json::envs, not a field on the
    # record itself. Callers know the name from the lookup args.
    assert record.env_fingerprint == "deadbeef" * 8
    assert record.backend == "pixi"

    assert get("nonexistent", tmp_path) is None


@workflow(purpose="delete removes the entry from the manifest "
                  "and survives across reload")
def test_delete_removes_entry(tmp_path):
    from wfc.environments import delete, get, list_envs
    _write_manifest(tmp_path, envs={
        "image-io": _sample_record(),
        "other": _sample_record(),
    })

    delete("image-io", tmp_path)
    assert get("image-io", tmp_path) is None
    assert [name for name, _ in list_envs(tmp_path)] == ["other"]

    # KeyError on second delete
    with pytest.raises(KeyError):
        delete("image-io", tmp_path)


def test_save_manifest_is_atomic(tmp_path):
    """save_manifest writes via tempfile + os.replace so a reader never
    sees a half-flushed file even if the process is interrupted."""
    from wfc.environments import save_manifest, load_manifest
    (tmp_path / ".wfc").mkdir()

    payload = {"schema_version": 3, "envs": {"image-io": _sample_record()}}
    save_manifest(tmp_path, payload)

    # No leftover temp files in .wfc/
    leftovers = [p.name for p in (tmp_path / ".wfc").iterdir()
                 if p.name.startswith(".envs.")]
    assert leftovers == []

    # Round-trip via load_manifest
    assert load_manifest(tmp_path)["envs"]["image-io"]["container"] == VALID_REF


@workflow(purpose="save_manifest while another handle holds .wfc/envs.json "
                  "open, released about 100 ms later (the Windows sharing "
                  "violation of a concurrent reader), succeeds: the record is "
                  "written and no temp file is left")
def test_save_manifest_waits_out_a_briefly_held_manifest(tmp_project):
    import threading

    from wfc.environments import load_manifest, save_manifest

    manifest = tmp_project / ".wfc" / "envs.json"
    save_manifest(tmp_project, {"schema_version": 3, "envs": {}})
    holder = open(manifest, "rb")  # noqa: SIM115 -- closed by the timer
    release = threading.Timer(0.1, holder.close)
    release.start()
    try:
        save_manifest(tmp_project, {"schema_version": 3,
                                    "envs": {"image-io": _sample_record()}})
    finally:
        release.join()
        holder.close()

    assert load_manifest(tmp_project)["envs"]["image-io"]["container"] == VALID_REF
    assert [p.name for p in manifest.parent.iterdir()
            if p.name.startswith(".envs.")] == []


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to replace "
                                             "a file another handle holds open")
def test_save_manifest_gives_up_on_a_manifest_held_throughout(tmp_project):
    """A handle held past every retry: PermissionError, the old bytes kept and
    the temp file removed."""
    from wfc.environments import save_manifest

    manifest = tmp_project / ".wfc" / "envs.json"
    save_manifest(tmp_project, {"schema_version": 3, "envs": {}})
    before = manifest.read_bytes()
    with open(manifest, "rb"):
        with pytest.raises(PermissionError):
            save_manifest(tmp_project, {"schema_version": 3,
                                        "envs": {"image-io": _sample_record()}})

    assert manifest.read_bytes() == before
    assert [p.name for p in manifest.parent.iterdir()
            if p.name.startswith(".envs.")] == []


# ---------------------------------------------------------------------------
# Tier 2: version_interpreter — the interpreter of one recorded image
# ---------------------------------------------------------------------------

@workflow(purpose="version_interpreter answers by (env, fingerprint) from "
                  "that image's captures: the --python override register "
                  "recorded, else the backend's default; an image with no "
                  "captures folder is refused with CaptureMissingError, "
                  "never answered with a bare 'python'")
@pytest.mark.parametrize("override,expected", [
    ("/usr/bin/python3", "/usr/bin/python3"),
    (None, "python"),
])
def test_version_interpreter_reads_the_images_own_capture(
    tmp_project, monkeypatch, override, expected,
):
    from tests.fixtures.fakes import stub_docker_image_content, stub_docker_image_inspect
    from wfc import environments as envs_mod
    from wfc.environments import CaptureMissingError, version_interpreter

    stub_docker_image_content(monkeypatch)
    stub_docker_image_inspect(monkeypatch, f"sha256:{VALID_DIGEST}")
    record = envs_mod.register(
        name="tools", backend="byo",
        source={"image": "docker://local/tools:latest"},
        project_dir=tmp_project, python_override=override,
    )

    assert version_interpreter("tools", record.env_fingerprint,
                               tmp_project) == expected
    with pytest.raises(CaptureMissingError):
        version_interpreter("tools", "f" * 32, tmp_project)


def test_env_record_python_field_round_trips_and_old_records_load():
    """Additive-field backcompat: a pre-field record loads with python=None;
    a new record round-trips the field through to_dict/from_dict."""
    from wfc.environments import EnvRecord

    old = EnvRecord.from_dict(_sample_record())
    assert old.python is None

    data = _sample_record()
    data["python"] = "/opt/conda/bin/python"
    new = EnvRecord.from_dict(data)
    assert new.python == "/opt/conda/bin/python"
    assert new.to_dict()["python"] == "/opt/conda/bin/python"


# ---------------------------------------------------------------------------
# Tier 3: CLI surface
# ---------------------------------------------------------------------------

@workflow(
    purpose="wfc list-envs prints a friendly empty-state line in a fresh "
            "project, then a 2-row table once envs are registered",
)
def test_cli_list_envs_empty_then_populated(cli, tmp_project):
    口 = Step(step_num=1, name="Empty state",
             purpose="Fresh project -> friendly message, exit 0")
    # tmp_project seeds a placeholder ``fixture-env`` so
    # container-only method registration validates Docker-free. This test
    # owns the manifest-empty-state assertion, so clear that seed first.
    envs_json = tmp_project / ".wfc" / "envs.json"
    if envs_json.exists():
        envs_json.unlink()
    result = cli("list-envs")
    assert result.returncode == 0, result.stderr
    assert "No container envs registered" in result.stdout

    口 = Step(step_num=2, name="Populate manifest",
             purpose="Write a 2-env manifest directly to .wfc/envs.json")
    _write_manifest(tmp_project, envs={
        "image-io": _sample_record(),
        "ml": _sample_record(),
    })

    口 = Step(step_num=3, name="Re-run list-envs",
             purpose="Verify header + 2 data rows are printed")
    result = cli("list-envs")
    assert result.returncode == 0, result.stderr
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    # 1 header + 2 rows
    assert len(lines) == 3
    assert "NAME" in lines[0]
    assert "image-io" in lines[1]
    assert "ml" in lines[2]


@workflow(
    purpose="wfc show-env prints the env name, then each record field as "
            "'key : value', and exits 0; for a name with no record it prints "
            "an error and exits 1",
)
def test_cli_show_env_prints_record_then_refuses_unknown_name(cli, tmp_project):
    record = _sample_record()
    _write_manifest(tmp_project, envs={"image-io": record})

    result = cli("show-env", "image-io")
    assert result.returncode == 0, result.stderr
    rows = [ln.partition(" : ") for ln in result.stdout.splitlines() if ln.strip()]
    fields = {key.strip(): value for key, _sep, value in rows}
    assert rows[0][0].strip() == "name"
    assert fields["name"] == "image-io"
    for key, value in record.items():
        assert fields[key] == value, key

    missing = cli("show-env", "no-such-env")
    assert missing.returncode == 1
    assert "ERROR" in missing.stderr
    assert "no-such-env" in missing.stderr


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under *root* by relative path, with its bytes."""
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _register_env(project: Path, name: str, monkeypatch, layer: str = "1"):
    """Register *name* as a pixi env through production, Docker faked."""
    from tests.fixtures.fakes import (
        stub_docker_build,
        stub_docker_image_content,
        stub_docker_image_inspect,
    )
    from wfc import environments as envs_mod

    stub_docker_build(monkeypatch, None)
    stub_docker_image_inspect(monkeypatch, "sha256:" + layer * 64)
    stub_docker_image_content(monkeypatch, {
        "layers": ["sha256:" + layer * 64], "env": ["PATH=/usr/bin"],
        "user": "", "workdir": "/"})
    return envs_mod.register(
        name=name, backend="pixi",
        source={"pixi_lock_content": "version: 6\nenvironments: {}\n",
                "pixi_toml_content": f'[project]\nname = "{name}"\n',
                "pip_freeze_content": "numpy==1.26.4\n"},
        project_dir=project)


@workflow(
    purpose="wfc delete-env refuses, --force or not, while an older (not "
            "the newest) version of a method records the env, naming that "
            "version; the record and its captures stay",
    inputs="method transform v1 on image-io and v2 on fixture-env, read "
           "from its committed version list",
    outputs="exit 1 naming data_transform/transform v1; image-io kept",
)
def test_cli_delete_env_refuses_while_an_older_version_records_it(
    cli, tmp_project, monkeypatch,
):
    from wfc.environments import get as env_get
    from wfc.registration import register_method, register_module, version_entry

    口 = Step(step_num=1, name="Register image-io, then transform v1 on it",
             purpose="The method's first version records image-io")
    register_module(name="data_transform",
                    contracts=[{"type": "output", "name": "output",
                                "value_type": "csv", "required": True}],
                    description="Test")
    record = _register_env(tmp_project, "image-io", monkeypatch)
    method_dir = tmp_project / "methods" / "transform"
    yaml_path = method_dir / "method.yaml"
    original_yaml = yaml_path.read_text()
    yaml_path.write_text(original_yaml.replace("env: fixture-env", "env: image-io"))
    register_method(method_dir=method_dir, module_name="data_transform",
                    assume_yes=True)

    口 = Step(step_num=2, name="Move transform to fixture-env (v2)",
             purpose="The newest version no longer records image-io; v1 still does")
    yaml_path.write_text(original_yaml)
    register_method(method_dir=method_dir, module_name="data_transform",
                    assume_yes=True)
    newest = version_entry("data_transform", "transform")
    assert (newest.label, newest.env_name) == (2, "fixture-env")

    口 = Step(step_num=3, name="delete-env image-io refuses",
             purpose="v1 still records it, so removing it would strand v1")
    result = cli("delete-env", "image-io", "--force")
    assert result.returncode == 1
    assert "data_transform/transform v1" in result.stderr
    assert "transform v2" not in result.stderr
    assert env_get("image-io", tmp_project) is not None
    assert (tmp_project / ".wfc" / "captures" / "image-io"
            / record.env_fingerprint).is_dir()


@workflow(
    purpose="wfc delete-env removes an env no version records with its "
            "captures folder in one commit; the tree is as clean as before",
    inputs="an env registered (and committed) through production",
    outputs="one commit holding envs.json and the folder's deletions",
)
def test_cli_delete_env_commits_the_record_and_captures_removal(
    cli, tmp_project, monkeypatch,
):
    from wfc.environments import get as env_get

    record = _register_env(tmp_project, "spare", monkeypatch)
    folder = f".wfc/captures/spare/{record.env_fingerprint}"
    files = {f"{folder}/{n}" for n in _tree_bytes(tmp_project / folder)}
    assert files
    # Every untracked file listed: git folds an all-untracked dir into one line.
    status_before = _git(tmp_project, "status", "--porcelain", "-uall")

    stub_interactive_prompt(monkeypatch, "y")
    result = cli("delete-env", "spare")

    assert result.returncode == 0, result.stderr
    assert env_get("spare", tmp_project) is None
    assert not (tmp_project / ".wfc" / "captures" / "spare").exists()
    committed = set(_git(tmp_project, "show", "--name-only", "--format=",
                         "HEAD").split())
    assert committed == {".wfc/envs.json", *files}
    assert _git(tmp_project, "status", "--porcelain", "-uall") == status_before


@workflow(
    purpose="wfc delete-env refuses when a method's version list cannot be "
            "read, naming the file: an unreadable record is no permission",
    inputs="a registered env and a version list that is not JSON",
    outputs="exit 1 naming the list; the env and its captures kept",
)
def test_cli_delete_env_refuses_on_an_unreadable_version_list(
    cli, tmp_project, monkeypatch,
):
    from wfc.environments import get as env_get

    _register_env(tmp_project, "spare", monkeypatch)
    broken = (tmp_project / ".wfc" / "registry" / "methods" / "data_transform"
              / "broken.versions.json")
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{not json", encoding="utf-8")

    result = cli("delete-env", "spare", "--force")

    assert result.returncode == 1
    assert "broken.versions.json" in result.stderr
    assert env_get("spare", tmp_project) is not None
    assert (tmp_project / ".wfc" / "captures" / "spare").is_dir()


def test_cli_delete_env_refuses_on_a_version_entry_whose_env_does_not_parse(
    cli, tmp_project, monkeypatch,
):
    """A version list that reads as JSON but records an env value that is not
    an env name is unreadable too: delete-env refuses naming the file rather
    than counting the entry as naming no env."""
    from wfc.environments import get as env_get

    _register_env(tmp_project, "spare", monkeypatch)
    # A hand-damaged list, like the unreadable one above: valid JSON whose
    # one entry records a value that is not an env name.
    path = (tmp_project / ".wfc" / "registry" / "methods" / "data_transform"
            / "odd.versions.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": 1, "versions": [{
        "label": 1, "code_fingerprint": "c" * 32,
        "env_name": "container:container:spare", "env_fingerprint": "d" * 32,
        "image_ref": "wfc-local/spare:1",
        "registered_at": "2026-01-01T00:00:00+00:00", "tree_hash": "e" * 40,
    }]}), encoding="utf-8")

    result = cli("delete-env", "spare", "--force")

    assert result.returncode == 1
    assert path.name in result.stderr
    assert "container:container:spare" in result.stderr
    assert env_get("spare", tmp_project) is not None
    assert (tmp_project / ".wfc" / "captures" / "spare").is_dir()


@workflow(
    purpose="wfc delete-env asks before removing an env no version records; "
            "answering N aborts and keeps the record and its captures",
    inputs="an env registered (and committed) through production; the answer n",
    outputs="exit 1 'Aborted.'; the record, the captures folder and HEAD kept",
)
def test_cli_delete_env_declined_keeps_the_record_and_captures(
    cli, tmp_project, monkeypatch,
):
    from wfc.environments import get as env_get

    record = _register_env(tmp_project, "spare", monkeypatch)
    manifest = tmp_project / ".wfc" / "envs.json"
    captures = tmp_project / ".wfc" / "captures" / "spare"
    before = (manifest.read_bytes(), _tree_bytes(captures),
              _git(tmp_project, "rev-parse", "HEAD"))

    stub_interactive_prompt(monkeypatch, "n")
    result = cli("delete-env", "spare")

    assert result.returncode == 1
    assert "Aborted." in result.stdout + result.stderr
    assert env_get("spare", tmp_project) is not None
    assert (captures / record.env_fingerprint).is_dir()
    assert (manifest.read_bytes(), _tree_bytes(captures),
            _git(tmp_project, "rev-parse", "HEAD")) == before


def test_cli_delete_env_refused_commit_leaves_the_tree_as_it_was(
    cli, tmp_project, monkeypatch,
):
    """A delete-env whose commit git refuses (a pre-commit hook) puts the
    record and the captures folder back and leaves nothing staged."""
    _register_env(tmp_project, "spare", monkeypatch)
    manifest = tmp_project / ".wfc" / "envs.json"
    captures = tmp_project / ".wfc" / "captures" / "spare"
    before = (manifest.read_bytes(), _tree_bytes(captures))
    hook = tmp_project / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
    os.chmod(hook, 0o755)

    result = cli("delete-env", "spare", "--force")

    assert result.returncode == 1
    assert "was not deleted" in result.stderr
    assert (manifest.read_bytes(), _tree_bytes(captures)) == before
    assert _git(tmp_project, "diff", "--cached", "--name-only").strip() == ""


@workflow(
    purpose="wfc delete-env whose record deletion fails to write envs.json "
            "refuses and puts the manifest and the captures folder back byte "
            "for byte, leaving no moved-aside folder under .wfc/captures/",
    inputs="an env registered (and committed) through production; the "
           "manifest's atomic landing (os.replace onto envs.json) writing the "
           "record deletion, then failing",
    outputs="exit 1 'was not deleted'; envs.json, the captures and HEAD as "
            "they were; no .delete-env-* folder",
)
def test_cli_delete_env_failed_record_deletion_restores_the_tree(
    cli, tmp_project, monkeypatch,
):
    from tests.fixtures.fakes import fake_os_replace

    _register_env(tmp_project, "spare", monkeypatch)
    manifest = tmp_project / ".wfc" / "envs.json"
    captures_root = tmp_project / ".wfc" / "captures"
    captures = captures_root / "spare"
    before = (manifest.read_bytes(), _tree_bytes(captures),
              _git(tmp_project, "rev-parse", "HEAD"))
    real_replace = os.replace

    def land_then_fail(src, dst, *args, **kwargs):
        # The record deletion lands on disk, then the write reports failure:
        # only the restore puts envs.json's bytes back.
        real_replace(src, dst, *args, **kwargs)
        if Path(dst).name == "envs.json":
            raise PermissionError(13, "Permission denied", str(dst))

    fake_os_replace(monkeypatch, land_then_fail)
    result = cli("delete-env", "spare", "--force")

    assert result.returncode == 1
    assert "was not deleted" in result.stderr
    assert (manifest.read_bytes(), _tree_bytes(captures),
            _git(tmp_project, "rev-parse", "HEAD")) == before
    assert not list(captures_root.glob(".delete-env-*"))
    assert _git(tmp_project, "diff", "--cached", "--name-only").strip() == ""


def test_delete_env_outside_git_removes_without_committing(tmp_path, monkeypatch):
    """Outside a git repository delete-env removes the record and the
    captures folder and commits nothing."""
    from tests.conftest import pin_project_root
    from wfc.environments import get as env_get
    from wfc.environments.verbs import delete_env

    pin_project_root(monkeypatch, tmp_path)
    _write_manifest(tmp_path, envs={"image-io": _sample_record()})
    folder = tmp_path / ".wfc" / "captures" / "image-io" / ("d" * 32)
    folder.mkdir(parents=True)
    (folder / "packages.lock").write_text("x\n", encoding="utf-8")

    assert delete_env("image-io", references=[], force=True) == 0
    assert env_get("image-io", tmp_path) is None
    assert not folder.parent.exists()
    assert not (tmp_path / ".git").exists()

