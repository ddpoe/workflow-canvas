"""Drafts in Registration: pointers, the Registration check and registering a draft.

A draft points a module's method name at a method folder on disk. Its
pointer, ``draft.json``, lives in the draft's own folder under
``.wfc/runs/drafts/<module>/<method>/`` (ignored by git), holds the folder's
stat snapshot and the contract its ``method.yaml`` declared when it was last
read. The Registration check collects every refusal registration would make
into a ✓/✗ list, restoring nothing and writing nothing. Registering a draft
goes through the ordinary plan/apply flow and then removes the draft folder.

Every project is built by production ``wfc init`` and registration
(``tmp_project`` + ``register_test_method``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.routes import register_test_method
from wfc import layout

MODULE, METHOD = "mod", "transform"

GOOD_YAML = """\
inputs:
  data:
    type: .csv
    required: true
outputs:
  output:
    type: .csv
    required: true
params:
  suffix:
    type: str
    required: false
    default: "_t"
executor: python
env: fixture-env
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def _tree_bytes(folder: Path) -> dict[str, bytes]:
    """Every file under *folder*, by relative path, with its bytes."""
    if not folder.exists():
        return {}
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def _method_rows() -> list[tuple[str, str]]:
    from sqlmodel import select

    from wfc.persistence import Method, Module, get_session
    with get_session() as session:
        return sorted((mod.name, meth.name)
                      for mod in session.exec(select(Module)).all()
                      for meth in mod.methods)


@pytest.fixture
def project(tmp_project):
    """A project with module ``mod`` and its method ``transform`` at v1."""
    register_test_method(tmp_project, module_name=MODULE,
                         method_dir=tmp_project / "methods" / METHOD)
    return tmp_project


@pytest.fixture
def elsewhere(tmp_path_factory) -> Path:
    """A folder outside the project where the author keeps method folders."""
    return tmp_path_factory.mktemp("author")


def _draft_folder(elsewhere: Path, name: str = METHOD, yaml: str = GOOD_YAML,
                  script: str | None = None) -> Path:
    folder = elsewhere / name
    shutil.copytree(Path(__file__).parent / "fixtures" / "methods" / "transform",
                    folder)
    (folder / "transform.py").rename(folder / f"{name}.py")
    if script is not None:
        (folder / f"{name}.py").write_text(script, encoding="utf-8")
    (folder / "method.yaml").write_text(yaml, encoding="utf-8")
    return folder


def _touch(path: Path, text: str) -> None:
    """Rewrite *path* so its size or mtime differs from before."""
    before = path.stat().st_mtime_ns
    path.write_text(text, encoding="utf-8")
    after = path.stat()
    if after.st_mtime_ns == before:
        os.utime(path, ns=(after.st_atime_ns, before + 1_000_000_000))


# --- adding a draft ---------------------------------------------------------


@workflow(purpose="Adding a folder as a draft names its kind in the dialog's "
                  "sentence: a DRAFT version of a method the module has, or a "
                  "DRAFT method; the pointer lands in the draft's own folder")
def test_adding_a_draft_states_its_kind(project, elsewhere):
    from wfc.registration import add_draft, plan_draft, read_draft

    口 = Step(step_num=1, name="Draft a version of a registered method",
             purpose="The folder is named like the module's method transform (v1)")
    version_folder = _draft_folder(elsewhere)
    plan = plan_draft(MODULE, version_folder)
    assert plan.kind == "version"
    assert plan.message == (
        "Module mod already has a method called transform (newest version v1). "
        "This folder becomes its DRAFT version; registering it adds v2."
    )
    assert (plan.newest_label, plan.registers_as) == (1, 2)
    assert not layout.draft_dir(project, MODULE, METHOD).exists()

    口 = Step(step_num=2, name="Add it",
             purpose="The pointer is written under .wfc/runs/drafts/mod/transform/")
    pointer = add_draft(MODULE, version_folder)
    assert layout.draft_pointer_path(project, MODULE, METHOD).is_file()
    assert read_draft(MODULE, METHOD) == pointer
    assert pointer.folder == version_folder.resolve()
    assert pointer.contract["inputs"] == {"data": {"type": ".csv", "required": True}}

    口 = Step(step_num=3, name="Draft a new method",
             purpose="A folder named like no method of the module is a DRAFT method")
    method_plan = plan_draft(MODULE, _draft_folder(elsewhere, "fresh"))
    assert method_plan.kind == "method"
    assert method_plan.message == (
        "Module mod has no method called fresh. This folder becomes a DRAFT "
        "method; registering it adds fresh as v1."
    )
    assert (method_plan.newest_label, method_plan.registers_as) == (None, 1)


def test_one_draft_per_method_and_module_first(project, elsewhere):
    from wfc.registration import DraftRefusedError, add_draft

    first = _draft_folder(elsewhere)
    add_draft(MODULE, first)
    assert add_draft(MODULE, first).folder == first.resolve()  # same folder: kept

    other = _draft_folder(elsewhere / "copy")
    with pytest.raises(DraftRefusedError) as exc:
        add_draft(MODULE, other)
    assert str(first.resolve()) in str(exc.value)

    with pytest.raises(DraftRefusedError) as exc:
        add_draft("nomod", _draft_folder(elsewhere, "other"))
    assert "nomod" in str(exc.value) and "register-module" in str(exc.value)


# --- the re-read and Update (d-18) --------------------------------------------


@workflow(purpose="The draft re-read reads method.yaml again through Contracts' "
                  "parser and runs the Registration check: the read-only form "
                  "writes nothing, the writing form (Update's) stores the "
                  "contract and method.yaml's stat on the pointer; a script edit "
                  "only marks files updated, and nothing is registered")
def test_the_reread_reads_method_yaml_again_writing_or_read_only(project, elsewhere):
    from wfc.registration import add_draft, draft_status, read_draft, reread_draft, update_draft

    folder = _draft_folder(elsewhere)
    pointer = add_draft(MODULE, folder)
    pointer_file = layout.draft_pointer_path(project, MODULE, METHOD)
    status = draft_status(pointer)
    assert (status.stale, status.files_updated, status.missing) == (False, False, False)

    口 = Step(step_num=1, name="Edit only the script",
             purpose="A script-only change only marks the folder's files updated")
    _touch(folder / "transform.py", (folder / "transform.py").read_text() + "\n# edit\n")
    status = draft_status(pointer)
    assert (status.stale, status.files_updated) == (False, True)
    assert status.changed_files == ["transform.py"]

    口 = Step(step_num=2, name="Edit method.yaml; re-read it read-only",
             purpose="The contract read now and the check come back; the pointer "
                     "is not written")
    _touch(folder / "method.yaml", GOOD_YAML.replace("_t", "_new"))
    stored = pointer_file.read_bytes()
    seen = reread_draft(MODULE, METHOD, write=False)
    assert seen.changed
    assert seen.contract["params"]["suffix"]["default"] == "_new"
    assert seen.pointer.contract == seen.contract
    assert seen.check.ok, seen.check.failures
    assert pointer_file.read_bytes() == stored
    assert read_draft(MODULE, METHOD).contract["params"]["suffix"]["default"] == "_t"

    口 = Step(step_num=3, name="Re-read it writing (Update)",
             purpose="The contract and method.yaml's stat are stored; nothing is "
                     "registered; Update names no param rule of its own")
    rows_before = _method_rows()
    update = update_draft(MODULE, METHOD)
    assert update.changed and update.check.ok
    assert update.contract["params"]["suffix"]["default"] == "_new"
    assert read_draft(MODULE, METHOD) == update.pointer
    assert update.message == "Read method.yaml again: the contract changed."
    status = draft_status(update.pointer)
    assert status.stale is False
    assert status.files_updated  # the script edit still counts toward "Files updated"
    assert _method_rows() == rows_before

    assert reread_draft(MODULE, METHOD).changed is False


def test_a_changed_or_unreadable_method_yaml_never_refuses_by_itself(project, elsewhere):
    """No "press Update" refusal is left: the re-read's check carries the ✗."""
    from wfc.registration import (
        DraftRefusedError,
        add_draft,
        draft_run_refusal,
        reread_draft,
    )

    folder = _draft_folder(elsewhere)
    add_draft(MODULE, folder)
    _touch(folder / "method.yaml", GOOD_YAML.replace("_t", "_new"))
    assert draft_run_refusal(MODULE, METHOD) is None

    _touch(folder / "method.yaml", "inputs: [unclosed\n")
    assert draft_run_refusal(MODULE, METHOD) is None
    for write in (False, True):
        reread = reread_draft(MODULE, METHOD, write=write)
        assert reread.contract is None and not reread.check.ok
        assert any(not line.ok and "method.yaml" in line.text
                   for line in reread.check.lines), reread.check.lines
        assert "Update" not in " ".join(reread.check.failures)

    shutil.rmtree(folder)
    assert "gone" in draft_run_refusal(MODULE, METHOD)
    with pytest.raises(DraftRefusedError):
        reread_draft(MODULE, METHOD, write=False)


# --- a declared param the script never reads (case 18) -------------------------

UNREAD_PARAM_YAML = GOOD_YAML.replace("executor:", """\
  scale:
    type: int
    required: false
    default: 1
executor:""")

UNREAD_PARAM_SCRIPT = textwrap.dedent("""\
    import wfc_client as wfc

    @wfc.method
    def unread(ctx):
        suffix = ctx.params["suffix"]
        ctx.save_artifact("output", ctx.workdir / f"out{suffix}.csv")
""")


def test_registration_and_the_draft_check_warn_about_an_unread_param(project, elsewhere,
                                                                     capsys):
    """Catalog case 18: a WARNING line, never a refusal; dynamic access skips it."""
    from wfc.registration import add_draft, check_draft, read_version_list, reread_draft

    folder = _draft_folder(elsewhere, "unread", yaml=UNREAD_PARAM_YAML,
                           script=UNREAD_PARAM_SCRIPT)
    add_draft(MODULE, folder)
    capsys.readouterr()
    check = check_draft(MODULE, "unread")
    assert check.ok, check.failures
    (warning,) = check.warnings
    assert "'scale'" in warning and "'suffix'" not in warning
    assert f"WARNING: {warning}" in capsys.readouterr().out

    # The read-only re-read (Validate's, the snapshot's) keeps the warning as
    # data and prints nothing; the writing form (a run's, Update's) prints it.
    assert reread_draft(MODULE, "unread", write=False).check.warnings == (warning,)
    assert capsys.readouterr().out == ""
    assert reread_draft(MODULE, "unread", write=True).check.warnings == (warning,)
    assert f"WARNING: {warning}" in capsys.readouterr().out

    register_test_method(project, module_name=MODULE, method_dir=folder)
    assert "'scale'" in capsys.readouterr().out
    assert [e.label for e in read_version_list(project, MODULE, "unread")] == [1]

    (folder / "unread.py").write_text(
        UNREAD_PARAM_SCRIPT.replace('ctx.params["suffix"]', 'dict(ctx.params)["suffix"]'),
        encoding="utf-8")
    assert check_draft(MODULE, "unread").warnings == ()


# --- Remove and Delete outputs ----------------------------------------------


def test_delete_outputs_keeps_the_pointer_and_remove_deletes_the_folder(project, elsewhere):
    from wfc.registration import (
        DraftNotFoundError,
        add_draft,
        delete_draft_outputs,
        read_draft,
        remove_draft,
    )

    folder = _draft_folder(elsewhere)
    pointer = add_draft(MODULE, folder)
    result = layout.draft_result_dir(project, MODULE, METHOD, "n1", "s1")
    result.mkdir(parents=True)
    (result / "output.csv").write_text("a\n")
    layout.draft_run_path(project, MODULE, METHOD).write_text("{}")

    delete_draft_outputs(MODULE, METHOD)
    assert sorted(p.name for p in layout.draft_dir(project, MODULE, METHOD).iterdir()) == [
        layout.DRAFT_POINTER_FILENAME]
    assert read_draft(MODULE, METHOD) == pointer

    remove_draft(MODULE, METHOD)
    assert not layout.draft_dir(project, MODULE, METHOD).exists()
    assert folder.is_dir() and (folder / "method.yaml").is_file()  # the author's folder stays
    with pytest.raises(DraftNotFoundError):
        remove_draft(MODULE, METHOD)


# --- the Registration check ---------------------------------------------------


@workflow(purpose="An invalid draft's Registration check shows each reason ✗ "
                  "(env not registered, script missing) and writes nothing "
                  "(case 5's check list)")
def test_invalid_draft_check_lists_each_reason_and_writes_nothing(project, elsewhere):
    from wfc.registration import add_draft, check_draft

    folder = elsewhere / "broken"
    folder.mkdir()
    (folder / "method.yaml").write_text(GOOD_YAML.replace("fixture-env", "no-such-env"),
                                        encoding="utf-8")
    add_draft(MODULE, folder)
    draft_files = _tree_bytes(layout.draft_dir(project, MODULE, "broken"))
    registry = _tree_bytes(layout.registry_dir(project))
    status, head, rows = _git(project, "status", "--porcelain"), _git(project, "rev-parse", "HEAD"), _method_rows()

    check = check_draft(MODULE, "broken")

    assert check.ok is False
    failures = check.failures
    assert any("no-such-env" in text for text in failures), failures
    assert any("No method script found" in text for text in failures), failures
    assert any(line.ok and "Module mod is registered" in line.text for line in check.lines)
    assert _tree_bytes(layout.draft_dir(project, MODULE, "broken")) == draft_files
    assert _tree_bytes(layout.registry_dir(project)) == registry
    assert _git(project, "status", "--porcelain") == status
    assert _git(project, "rev-parse", "HEAD") == head
    assert _method_rows() == rows


def test_valid_draft_check_passes_every_line(project, elsewhere):
    from wfc.registration import add_draft, check_draft

    add_draft(MODULE, _draft_folder(elsewhere))
    check = check_draft(MODULE, METHOD)
    assert check.ok, check.failures
    assert len(check.lines) >= 6


def test_check_runs_the_undeclared_name_scan(project, elsewhere):
    from wfc.registration import add_draft, check_draft

    script = textwrap.dedent("""\
        import wfc_client as wfc

        @wfc.method
        def typo(ctx):
            src = ctx.input("dta")
            ctx.save_artifact("output", ctx.workdir / "out.csv")
    """)
    add_draft(MODULE, _draft_folder(elsewhere, "typo", script=script))
    check = check_draft(MODULE, "typo")
    assert not check.ok
    assert any("dta" in text and "Declared inputs: ['data']" in text
               for text in check.failures), check.failures


def test_check_never_restores_a_changed_snapshot(project, elsewhere):
    """Registration restores a registered copy changed outside wfc; the check does not."""
    from wfc.registration import add_draft, check_draft

    copy = layout.method_dir(project, MODULE, METHOD) / "transform.py"
    copy.write_text(copy.read_text() + "\n# edited in the registry\n", encoding="utf-8")
    edited = copy.read_bytes()
    add_draft(MODULE, _draft_folder(elsewhere))
    check_draft(MODULE, METHOD)
    assert copy.read_bytes() == edited


# --- the contract map ---------------------------------------------------------


def test_contract_map_holds_each_drafts_contract(project, elsewhere):
    from wfc.persistence import get_session
    from wfc.registration import add_draft, load_contract_map

    add_draft(MODULE, _draft_folder(elsewhere))
    add_draft(MODULE, _draft_folder(elsewhere, "fresh"))
    with get_session() as session:
        contract_map = load_contract_map(session)

    entry = contract_map[f"{MODULE}.{METHOD}"]
    draft = entry["versions"]["draft"]
    assert draft["params"]["suffix"]["default"] == "_t"
    assert draft["folder"] == str((elsewhere / METHOD).resolve())
    assert entry["label"] == 1 and entry["version"] != "draft"  # newest stays the registered one

    fresh = contract_map[f"{MODULE}.fresh"]
    assert fresh["draft_only"] is True and fresh["version"] is None
    assert fresh["versions"]["draft"]["input_slots"] == {"data": {"type": ".csv", "required": True}}


# --- registering a draft (case 6, registration half) --------------------------


@workflow(purpose="Registering a draft adds the next version of its method, or "
                  "v1 of a new one, through the ordinary plan and apply, and "
                  "removes the draft folder (case 6's registration half)")
def test_registering_a_draft(project, elsewhere):
    from wfc.registration import (
        add_draft,
        plan_draft_registration,
        read_draft,
        read_version_list,
        register_draft,
    )

    口 = Step(step_num=1, name="Register a DRAFT version",
             purpose="transform has v1; its draft registers as v2")
    add_draft(MODULE, _draft_folder(elsewhere))
    plan = plan_draft_registration(MODULE, METHOD)
    assert plan.label == 2
    registration = register_draft(MODULE, METHOD, planned=plan.token, assume_yes=True)
    assert registration.label == 2
    assert [e.label for e in read_version_list(project, MODULE, METHOD)] == [1, 2]
    assert read_draft(MODULE, METHOD) is None
    assert not layout.draft_dir(project, MODULE, METHOD).exists()

    口 = Step(step_num=2, name="Register a DRAFT method",
             purpose="fresh is new to the module; its draft registers as v1")
    add_draft(MODULE, _draft_folder(elsewhere, "fresh"))
    registration = register_draft(MODULE, "fresh", assume_yes=True)
    assert registration.label == 1
    assert (MODULE, "fresh") in _method_rows()
    assert not layout.draft_dir(project, MODULE, "fresh").exists()


def test_a_refused_registration_keeps_the_draft(project, elsewhere):
    from wfc.registration import RegistrationConfirmationRequired, add_draft, read_draft, register_draft

    add_draft(MODULE, _draft_folder(elsewhere))
    with pytest.raises(RegistrationConfirmationRequired):
        register_draft(MODULE, METHOD)
    assert read_draft(MODULE, METHOD) is not None
