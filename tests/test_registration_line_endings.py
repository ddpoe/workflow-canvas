"""Registration and line endings: a CRLF shell script is refused; registry bytes
are never converted.

A ``.sh`` with CRLF line endings is refused before any write, and the
message carries the fix. With ``core.autocrlf=true`` the registered version
extracts byte for byte, so the copy a run executes fingerprints as the
version. Real ``git`` in a project ``wfc init`` produced; every version is
made by production ``register_method``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

REPO = Path(__file__).resolve().parent.parent
FIXTURE_METHOD = REPO / "tests" / "fixtures" / "methods" / "transform"
MODULE, METHOD = "mod", "transform"


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True).stdout


def _source(tmp_path: Path) -> Path:
    """The fixture method, copied outside the project, with LF bytes.

    Its ``method.yaml`` names the main script, so the helper scripts the
    tests add beside it register.
    """
    dest = tmp_path / "elsewhere" / METHOD
    shutil.copytree(FIXTURE_METHOD, dest)
    for path in dest.iterdir():
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    yaml_path = dest / "method.yaml"
    yaml_path.write_bytes(yaml_path.read_bytes() + f"script: {METHOD}.py\n".encode())
    return dest


def _register(source_dir: Path):
    from wfc.registration import register_method, register_module
    register_module(name=MODULE, contracts=[])
    return register_method(method_dir=source_dir, module_name=MODULE,
                           assume_yes=True)


@workflow(purpose="A method whose .sh helper has CRLF line endings is "
                  "refused before any write, naming the script and the fix "
                  "(convert it to LF); HEAD, the snapshot, the version list and "
                  "the method rows are as they were")
def test_crlf_shell_script_is_refused_before_any_write(tmp_project, tmp_path):
    from sqlmodel import select

    from wfc import layout
    from wfc.persistence import Method, get_session

    口 = Step(step_num=1, name="A method with a CRLF shell helper",
             purpose="The script is LF; prep.sh is a helper with CRLF endings")
    source = _source(tmp_path)
    (source / "prep.sh").write_bytes(b"#!/bin/sh\r\necho ready\r\n")
    head = _git(tmp_project, "rev-parse", "HEAD")

    口 = Step(step_num=2, name="Register it",
             purpose="Refused with the file named and the fix")
    with pytest.raises(ValueError) as refused:
        _register(source)
    message = str(refused.value)
    assert "prep.sh" in message
    assert "CRLF" in message and "LF" in message.replace("CRLF", "")

    口 = Step(step_num=3, name="Nothing was written",
             purpose="No commit, no snapshot, no version list, no method row")
    assert _git(tmp_project, "rev-parse", "HEAD") == head
    assert not layout.method_dir(tmp_project, MODULE, METHOD).exists()
    assert not layout.method_versions_path(tmp_project, MODULE, METHOD).exists()
    with get_session() as session:
        assert session.exec(select(Method).where(Method.name == METHOD)).first() is None

    口 = Step(step_num=4, name="Converted to LF, it registers",
             purpose="The same method with prep.sh in LF registers")
    (source / "prep.sh").write_bytes(b"#!/bin/sh\necho ready\n")
    assert _register(source).method_id is not None


@workflow(purpose="Decision 3: with core.autocrlf=true, a method with an LF "
                  "script and a CRLF helper registers, the registry blobs hold "
                  "the snapshot's bytes unconverted, and the version extracted "
                  "as a run extracts it is byte for byte the source and "
                  "fingerprints as the version")
def test_autocrlf_registration_extracts_byte_for_byte(tmp_project, tmp_path):
    from wfc import layout
    from wfc.registration.method_version import newest_entry, read_version_list
    from wfc.registration.registered_code import extract_version_copy

    口 = Step(step_num=1, name="A user whose git converts line endings",
             purpose="core.autocrlf=true in the project's repository")
    _git(tmp_project, "config", "core.autocrlf", "true")
    source = _source(tmp_path)
    helper = b"X = 1\r\nY = 2\r\n"
    (source / "util.py").write_bytes(helper)
    script = (source / f"{METHOD}.py").read_bytes()
    assert b"\r\n" not in script

    口 = Step(step_num=2, name="Register the method",
             purpose="Production register_method")
    _register(source)

    口 = Step(step_num=3, name="The registry blobs are the snapshot's bytes",
             purpose="git stored the CRLF helper and the LF script unconverted")
    rel = layout.method_dir(tmp_project, MODULE, METHOD) \
        .relative_to(tmp_project).as_posix()
    assert _git(tmp_project, "cat-file", "-p", f"HEAD:{rel}/util.py") == helper
    assert _git(tmp_project, "cat-file", "-p", f"HEAD:{rel}/{METHOD}.py") == script

    口 = Step(step_num=4, name="Extract the version as a run does",
             purpose="The verified copy is byte for byte the source")
    entry = newest_entry(read_version_list(tmp_project, MODULE, METHOD))
    copy = extract_version_copy(MODULE, METHOD, entry, tmp_project)
    assert (copy / "util.py").read_bytes() == helper
    assert (copy / f"{METHOD}.py").read_bytes() == script
