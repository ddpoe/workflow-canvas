"""Multi-language method registration (R / bash) — discovery, ambiguity, script: key.

Covers the registration path widened from `.py`-only to the recognized
extension set (`.py`/`.R`/`.r`/`.sh`):

  - a `<name>.sh` script is discovered by the extension probe, registered
    WITHOUT any Python AST scan, snapshotted, and fingerprintable
  - a dir with two candidate scripts and no `script:` key errors before any
    DB write, listing the candidates and pointing at `script:`
  - the optional method.yaml `script:` key selects the named file explicitly
  - an explicit `script:` value with an unrecognized extension is rejected
  - an explicit `script:` value resolving OUTSIDE the method dir is rejected
    (the snapshot/fingerprint could never cover it; `helpers:` is the fix)
  - re-registration purges ghost scripts an earlier registration left in
    the module-qualified snapshot (both permissive and strict modes)
  - the register-method CLI prints a clean ERROR line (no traceback) for
    script-discovery misses
  - `wfc register-env` rejects passing both --interpreter and --python

No Docker required: the env manifest entry uses a fake digest-pinned ref
(check_method_env validates ref shape only).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlmodel import select

from axiom_annotations import workflow

from wfc import layout
from wfc.persistence import get_session, reset_engine
from wfc.init import init_project
from wfc.persistence import Method, TrackedFunction
from wfc.registration import register_method, register_module
from wfc.identity import build_code_fingerprint

from tests.fixtures.conftest import project_archive_dir, write_env_record

FAKE_DIGEST = "a" * 64
ENV_NAME = "test-env"


def _projection_of(method_dir):
    """The contract projection the claim phase reads from a method directory."""
    from pathlib import Path as _Path

    from wfc.contracts import parse_method_yaml, render_contract_projection

    return render_contract_projection(parse_method_yaml(_Path(method_dir)))


def _method_yaml(extra: str = "") -> str:
    return (
        "inputs:\n"
        "  trigger:\n"
        "    type: .txt\n"
        "    required: false\n"
        "outputs:\n"
        "  output:\n"
        "    type: .txt\n"
        "    required: true\n"
        "params: {}\n"
        f"env: {ENV_NAME}\n"
        + extra
    )


@pytest.fixture
def multilang_project(tmp_path, monkeypatch):
    """Tmp wfc project with a git repo, fake container env, and one module."""
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init"], cwd=proj, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "wfc@wfc"],
        cwd=proj, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "wfc"],
        cwd=proj, check=True, capture_output=True,
    )

    monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{proj / '.wfc' / 'wfc.db'}")

    # Explicit out-of-tree archive: the default resolves to
    # ~/.wfc/archives/<project> and init_dvc pre-creates it.
    init_project(proj, archive=str(project_archive_dir(proj)), assume_yes=True)
    write_env_record(proj, ENV_NAME, digest=FAKE_DIGEST)
    reset_engine()
    register_module(name="multilang", contracts=[])
    monkeypatch.chdir(proj)
    yield proj
    reset_engine()


def _get_method(name: str) -> Method | None:
    with get_session() as session:
        return session.exec(select(Method).where(Method.name == name)).first()


@workflow(
    purpose="A bash method registers through the extension probe: the .sh "
            "script is discovered without an AST scan, its real path is "
            "recorded, the snapshot copies it to the module-qualified "
            "snapshot, and the "
            "code fingerprint covers the registered copy"
)
def test_bash_method_registers_without_ast_scan(multilang_project):
    """`<name>.sh` discovered; AST scan skipped; snapshot + fingerprint."""
    proj = multilang_project
    src = proj / "src" / "greet"
    src.mkdir(parents=True)
    # LF bytes on every platform: a shell script with CRLF is refused.
    (src / "greet.sh").write_text(
        'printf "%s" "hello" > "$WFC_RUN_DIR/output.txt"\n', newline="\n"
    )
    (src / "method.yaml").write_text(_method_yaml())

    method_id = register_method(method_dir=src, module_name="multilang", assume_yes=True).method_id

    method = _get_method("greet")
    assert method is not None and method.id == method_id
    assert method.script_path == "greet.sh"

    # No Python AST scan -> no tracked-function metadata.
    with get_session() as session:
        tfs = session.exec(
            select(TrackedFunction).where(TrackedFunction.method_id == method_id)
        ).all()
    assert tfs == []

    # Snapshot copied the script + contract into the module-qualified
    # snapshot (the exact set the fingerprint hashes), and the registered
    # copy fingerprints.
    registered = layout.method_dir(proj, "multilang", "greet")
    assert (registered / "greet.sh").exists()
    assert (registered / "method.yaml").exists()
    assert len(build_code_fingerprint(registered, _projection_of(registered))) == 64

    # Ghost purge (permissive mode): a recognized script an earlier
    # registration copied, since removed from the source, is dropped on
    # re-registration, so the fingerprint covers exactly the registered set.
    # With a second script in the folder, method.yaml names the main one.
    (src / "method.yaml").write_text(_method_yaml("script: greet.sh\n"))
    (src / "ghost.sh").write_text("echo stale\n", newline="\n")
    register_method(method_dir=src, module_name="multilang", assume_yes=True)
    ghost = registered / "ghost.sh"
    assert ghost.exists()
    fp_with_ghost = build_code_fingerprint(registered, _projection_of(registered))
    (src / "ghost.sh").unlink()
    register_method(method_dir=src, module_name="multilang", assume_yes=True)
    assert not ghost.exists(), "stale snapshot script survived re-registration"
    assert (registered / "greet.sh").exists()
    assert (registered / "method.yaml").exists()
    assert build_code_fingerprint(registered, _projection_of(registered)) != fp_with_ghost


@workflow(
    purpose="A method dir with two candidate scripts and no explicit "
            "script: key fails registration before any DB write, listing the "
            "candidates and pointing at the method.yaml script: key; setting "
            "script: then selects the named file"
)
def test_ambiguous_scripts_error_then_script_key_selects(multilang_project):
    """ambiguity errors pre-DB; `script:` disambiguates."""
    proj = multilang_project
    src = proj / "src" / "dual"
    src.mkdir(parents=True)
    (src / "dual.py").write_text("def main():\n    pass\n")
    (src / "dual.R").write_text('x <- Sys.getenv("WFC_RUN_DIR")\n')
    (src / "method.yaml").write_text(_method_yaml())

    with pytest.raises(ValueError) as excinfo:
        register_method(method_dir=src, module_name="multilang", assume_yes=True)
    msg = str(excinfo.value)
    assert "dual.py" in msg and "dual.R" in msg
    assert "script:" in msg
    # Error fired before any DB write.
    assert _get_method("dual") is None

    # Explicit script: selects the R file.
    (src / "method.yaml").write_text(_method_yaml("script: dual.R\n"))
    method_id = register_method(method_dir=src, module_name="multilang", assume_yes=True).method_id
    method = _get_method("dual")
    assert method is not None and method.id == method_id
    assert method.script_path == "dual.R"


def test_script_key_unrecognized_extension_rejected(multilang_project):
    """edge: explicit `script:` with an unrecognized extension errors."""
    proj = multilang_project
    src = proj / "src" / "oddball"
    src.mkdir(parents=True)
    (src / "oddball.txt").write_text("not a script")
    (src / "method.yaml").write_text(_method_yaml("script: oddball.txt\n"))

    with pytest.raises(ValueError, match="unrecognized extension"):
        register_method(method_dir=src, module_name="multilang", assume_yes=True)
    assert _get_method("oddball") is None


def test_script_outside_method_dir_rejected(multilang_project):
    """An explicit script resolving outside the method dir is rejected
    pre-DB (snapshot/fingerprint could never cover it); the error points at
    `helpers:`. Same containment rule for the `script:` key and --script."""
    proj = multilang_project
    shared = proj / "shared"
    shared.mkdir()
    (shared / "main.R").write_text("m <- 1\n")

    src = proj / "src" / "escapist"
    src.mkdir(parents=True)
    (src / "method.yaml").write_text(_method_yaml("script: ../../shared/main.R\n"))

    with pytest.raises(ValueError, match="outside the method directory") as excinfo:
        register_method(method_dir=src, module_name="multilang", assume_yes=True)
    msg = str(excinfo.value)
    assert "helpers:" in msg
    assert _get_method("escapist") is None

    # Same rejection through the CLI --script parameter (identical path).
    with pytest.raises(ValueError, match="outside the method directory"):
        register_method(
            method_dir=src, module_name="multilang",
            script_name="../../shared/main.R",
            assume_yes=True,
        )
    assert _get_method("escapist") is None


@pytest.mark.parametrize(
    "family, files, yaml_extra, expect_msg",
    [
        # Probe miss: no script: key and no discoverable script in the dir.
        ("empty", {}, "", "No method script found"),
        # Explicit script: names a file that does not exist (FileNotFoundError
        # at the library level — must surface as a clean CLI error, not a
        # traceback). Message content is not pinned here; the contract is.
        ("ghost", {}, "script: ghost.R\n", None),
        # Two recognized candidates and no script: key to disambiguate.
        (
            "dual",
            {"dual.py": "def main():\n    pass\n",
             "dual.R": 'x <- Sys.getenv("WFC_RUN_DIR")\n'},
            "",
            "dual.R",
        ),
    ],
)
def test_register_method_cli_reports_discovery_errors_cleanly(
    multilang_project, capsys, family, files, yaml_extra, expect_msg
):
    """The register-method CLI turns every script-discovery failure family
    (probe miss, explicit script: naming a missing file, two ambiguous
    candidates) into a clean 'ERROR:' line + exit 1, not a traceback."""
    from wfc.cli import cli_main

    proj = multilang_project
    src = proj / "src" / family
    src.mkdir(parents=True)
    for fname, content in files.items():
        (src / fname).write_text(content)
    (src / "method.yaml").write_text(_method_yaml(yaml_extra))

    rc = cli_main(["register-method", "--yes", str(src), "--module", "multilang"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR:")
    if expect_msg is not None:
        assert expect_msg in err


@workflow(
    purpose="Strict helpers mode (opt-in via method.yaml `helpers:`): a "
            "declared out-of-dir helper is snapshotted under the reserved "
            "subdir and participates in the code fingerprint (editing it "
            "changes the digest on re-registration); an undeclared "
            "recognized script in the method dir fails registration loudly; "
            "a project-escaping helper path is rejected"
)
def test_strict_helpers_mode(multilang_project):
    """helpers present -> strict snapshot/fingerprint coverage."""
    proj = multilang_project

    # Out-of-dir shared helper.
    shared = proj / "shared"
    shared.mkdir()
    helper = shared / "util.R"
    helper.write_text("u <- 1\n")

    src = proj / "src" / "strict"
    src.mkdir(parents=True)
    (src / "strict.R").write_text("x <- 1\n")
    (src / "method.yaml").write_text(
        _method_yaml("script: strict.R\nhelpers:\n  - ../../shared/util.R\n")
    )

    register_method(method_dir=src, module_name="multilang", assume_yes=True)
    registered = layout.method_dir(proj, "multilang", "strict")
    snapshot_helper = registered / "_wfc_helpers" / "shared" / "util.R"
    assert (registered / "strict.R").exists()
    assert snapshot_helper.exists(), "out-of-dir helper missing from snapshot"

    fp1 = build_code_fingerprint(registered, _projection_of(registered))

    # Editing the declared helper changes the fingerprint (cache key axis)
    # after re-registration refreshes the snapshot.
    helper.write_text("u <- 2\n")
    register_method(method_dir=src, module_name="multilang", assume_yes=True)
    fp2 = build_code_fingerprint(registered, _projection_of(registered))
    assert fp2 != fp1, "edited helper did not change the code fingerprint"

    # Undeclared recognized script in the method dir -> loud strict error.
    (src / "sneaky.R").write_text("s <- 1\n")
    with pytest.raises(ValueError, match="helpers:") as excinfo:
        register_method(method_dir=src, module_name="multilang", assume_yes=True)
    assert "sneaky.R" in str(excinfo.value)
    (src / "sneaky.R").unlink()

    # Project-escaping helper path -> rejected before any DB write.
    esc = proj / "src" / "escape"
    esc.mkdir(parents=True)
    (esc / "escape.R").write_text("e <- 1\n")
    (esc / "method.yaml").write_text(
        _method_yaml("helpers:\n  - ../../../outside.R\n")
    )
    with pytest.raises(ValueError, match="Move the helper into the method's directory"):
        register_method(method_dir=esc, module_name="multilang", assume_yes=True)
    assert _get_method("escape") is None


@workflow(
    purpose="Strict helpers from a source outside the project: helpers in "
            "the method's own directory register, sit in the snapshot and "
            "enter the code fingerprint; a helper removed from method.yaml "
            "and the source leaves the snapshot on re-registration; a helper "
            "outside the method's directory is refused with the move-it "
            "advice and leaves no row and no snapshot"
)
def test_strict_helpers_from_a_source_outside_the_project(multilang_project):
    """In-directory strict helpers register from any directory."""
    proj = multilang_project
    elsewhere = proj.parent / "elsewhere"

    src = elsewhere / "outer"
    (src / "lib").mkdir(parents=True)
    (src / "outer.R").write_text("o <- 1\n")
    (src / "lib" / "util.R").write_text("u <- 1\n")
    (src / "lib" / "extra.R").write_text("e <- 1\n")
    (src / "method.yaml").write_text(
        _method_yaml("script: outer.R\nhelpers:\n  - lib/util.R\n  - lib/extra.R\n")
    )

    register_method(method_dir=src, module_name="multilang", assume_yes=True)
    registered = layout.method_dir(proj, "multilang", "outer")
    assert (registered / "outer.R").exists()
    assert (registered / "lib" / "util.R").exists()
    assert (registered / "lib" / "extra.R").exists()
    fp1 = build_code_fingerprint(registered, _projection_of(registered))

    # The helper is part of the fingerprint: editing it and re-registering
    # changes the digest.
    (src / "lib" / "util.R").write_text("u <- 2\n")
    register_method(method_dir=src, module_name="multilang", assume_yes=True)
    fp2 = build_code_fingerprint(registered, _projection_of(registered))
    assert fp2 != fp1, "an in-directory helper is not in the code fingerprint"

    # A helper dropped from method.yaml and the source is purged from the
    # snapshot on re-registration (the snapshot holds exactly the declared
    # set).
    (src / "lib" / "extra.R").unlink()
    (src / "method.yaml").write_text(_method_yaml("script: outer.R\nhelpers:\n  - lib/util.R\n"))
    register_method(method_dir=src, module_name="multilang", assume_yes=True)
    assert not (registered / "lib" / "extra.R").exists(), (
        "a removed helper survived re-registration in the snapshot")
    assert (registered / "lib" / "util.R").exists()

    # A helper outside the method's directory, from a source outside the
    # project: refused, naming the fix, before anything is written.
    (elsewhere / "shared.R").write_text("s <- 1\n")
    far = elsewhere / "far"
    far.mkdir()
    (far / "far.R").write_text("f <- 1\n")
    (far / "method.yaml").write_text(_method_yaml("helpers:\n  - ../shared.R\n"))
    with pytest.raises(ValueError) as refused:
        register_method(method_dir=far, module_name="multilang", assume_yes=True)
    message = str(refused.value)
    assert "Move the helper into the method's directory" in message
    assert "inside the project" in message
    assert _get_method("far") is None
    assert not layout.method_dir(proj, "multilang", "far").exists()


def test_register_env_rejects_both_interpreter_and_python(
    multilang_project, capsys, monkeypatch,
):
    """--interpreter and --python are the same recorded field; both given
    is ambiguous and rejected before any staging or docker work, even on a
    host with no Docker."""
    import argparse

    from tests.fixtures.fakes import stub_readiness_probes
    from wfc.cli import _cli_register_env

    stub_readiness_probes(monkeypatch, docker="fail")

    args = argparse.Namespace(
        name="dupe", spec=None, from_path=None, backend="byo",
        image="docker://example.com/img:1", base_image=None,
        dry_run=False, force=False,
        interpreter_path="Rscript", python_path="python",
    )
    rc = _cli_register_env(args)
    assert rc == 1
    assert "only one of --interpreter/--python" in capsys.readouterr().err
