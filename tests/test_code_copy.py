"""Extracted code copies: a version's registered tree, extracted, verified and shared.

The extraction, a lost or damaged copy re-extracted (a lost tree refused
naming the version), and the Tier 1 concurrency rule of the extraction (two
extractors of one version leave one verified copy).
"""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.routes import register_test_method
from wfc import layout
from wfc.registration.method_version import read_version_list, snapshot_code_fingerprint
from wfc.registration.registered_code import (
    MalformedMethodRecordError,
    extract_version_copy,
)

MODULE, METHOD = "copies", "lookup"


@pytest.fixture
def registered(tmp_project, tmp_path_factory):
    """Register a method with a CRLF text helper and a binary helper; return its newest entry."""
    src = tmp_path_factory.mktemp("copy-sources") / METHOD
    src.mkdir()
    (src / f"{METHOD}.py").write_text("print('ok')\n", encoding="utf-8")
    (src / "method.yaml").write_text(
        "inputs:\n  data:\n    type: .csv\nenv: fixture-env\n"
        "helpers:\n  - table.tsv\n  - weights.bin\n", encoding="utf-8")
    (src / "table.tsv").write_bytes(b"a\tb\r\n1\t2\r\n")
    (src / "weights.bin").write_bytes(bytes(range(256)) + b"\x00\r\n\x00")
    register_test_method(tmp_project, module_name=MODULE, method_dir=src)
    entries = read_version_list(tmp_project, MODULE, METHOD)
    return tmp_project, entries[-1]


@workflow(purpose="A version's tree extracted by git archive fingerprints as the "
                  "snapshot registration recorded, CRLF text and binary helper "
                  "included, and the verified copy is stamped beside itself")
def test_the_extracted_copy_fingerprints_as_the_registered_version(registered):
    root, entry = registered

    copy = extract_version_copy(MODULE, METHOD, entry, root)

    assert copy == layout.code_copy_dir(root, entry.tree_hash)
    assert snapshot_code_fingerprint(copy) == entry.code_fingerprint
    snapshot = layout.method_dir(root, MODULE, METHOD)
    assert snapshot_code_fingerprint(snapshot) == entry.code_fingerprint
    assert (copy / "weights.bin").read_bytes() == (snapshot / "weights.bin").read_bytes()
    stamp = layout.code_copy_stamp(root, entry.tree_hash)
    assert stamp.read_text(encoding="utf-8").strip() == entry.code_fingerprint


@workflow(purpose="A run's code is re-extracted when its copy was "
                  "deleted or damaged, and a version whose tree git no longer "
                  "holds is refused naming the version")
def test_the_resolver_re_extracts_a_lost_copy_and_refuses_a_lost_tree(registered):
    import shutil

    from wfc.registration import resolve_method_code

    root, entry = registered

    口 = Step(step_num=1, name="Resolve, then delete the copy",
             purpose="The next resolve extracts it again")
    code = resolve_method_code(MODULE, METHOD, root)
    shutil.rmtree(code.code_dir)
    again = resolve_method_code(MODULE, METHOD, root)
    assert again.code_dir == code.code_dir
    assert snapshot_code_fingerprint(again.code_dir) == entry.code_fingerprint

    口 = Step(step_num=2, name="Damage the copy",
             purpose="A changed helper is replaced by the registered bytes")
    (again.code_dir / "table.tsv").write_bytes(b"tampered\n")
    repaired = resolve_method_code(MODULE, METHOD, root)
    assert (repaired.code_dir / "table.tsv").read_bytes().replace(b"\r\n", b"\n") == (
        b"a\tb\n1\t2\n")

    口 = Step(step_num=3, name="Lose the tree from git, and the copy",
             purpose="Refused naming v1; no copy is left behind")
    shutil.rmtree(repaired.code_dir)
    loose = root / ".git" / "objects" / entry.tree_hash[:2] / entry.tree_hash[2:]
    assert loose.is_file(), "the registration left its tree as a loose object"
    loose.chmod(0o644)
    loose.unlink()
    with pytest.raises(MalformedMethodRecordError, match=r"Version v1 .*git no longer holds"):
        resolve_method_code(MODULE, METHOD, root)
    assert not repaired.code_dir.exists()


def test_a_verified_copy_is_reused_and_a_damaged_one_replaced(registered):
    root, entry = registered
    copy = extract_version_copy(MODULE, METHOD, entry, root)
    marker = copy / f"{METHOD}.py"
    before = marker.stat().st_mtime_ns

    assert extract_version_copy(MODULE, METHOD, entry, root) == copy
    assert marker.stat().st_mtime_ns == before, "a verified copy is not re-extracted"

    marker.write_text("print('edited')\n", encoding="utf-8")
    assert extract_version_copy(MODULE, METHOD, entry, root) == copy
    assert marker.read_text(encoding="utf-8") == "print('ok')\n"
    assert snapshot_code_fingerprint(copy) == entry.code_fingerprint


def test_a_tree_git_no_longer_holds_is_refused_naming_the_version(registered):
    root, entry = registered
    gone = replace(entry, tree_hash="0" * 40)

    with pytest.raises(MalformedMethodRecordError, match=r"Version v1 .*git no longer holds"):
        extract_version_copy(MODULE, METHOD, gone, root)
    assert not layout.code_copy_dir(root, gone.tree_hash).exists()
    assert not [p for p in layout.code_copies_dir(root).iterdir() if p.name.startswith(".")]


def test_two_concurrent_extractions_leave_one_verified_copy(registered):
    root, entry = registered
    results, errors = [], []
    start = threading.Barrier(2)

    def extract():
        start.wait()
        try:
            results.append(extract_version_copy(MODULE, METHOD, entry, root))
        except Exception as exc:  # pragma: no cover - the assertion reports it
            errors.append(exc)

    threads = [threading.Thread(target=extract) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    copy = layout.code_copy_dir(root, entry.tree_hash)
    assert results == [copy, copy]
    assert snapshot_code_fingerprint(copy) == entry.code_fingerprint
    leftovers = [p.name for p in layout.code_copies_dir(root).iterdir()
                 if p.name.startswith(".")]
    assert leftovers == []
