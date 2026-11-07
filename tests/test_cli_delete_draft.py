"""``wfc delete-draft``: the delete plan printed, asked, and applied with its token.

Catalog case 5, CLI half: the verb prints what goes (Execution's plan
message), refuses without a terminal and without ``--yes`` naming
``--yes``, keeps the draft with ``--outputs-only``, and removes it with
``--yes``.
"""

from __future__ import annotations

from pathlib import Path

from axiom_annotations import Step, workflow

from tests.fixtures.draft_project import built_draft_project
from wfc.cli import cli_main


@workflow(purpose="Case 5, CLI half: wfc delete-draft prints the delete plan; "
                  "with no terminal and no --yes it refuses naming --yes and "
                  "deletes nothing; --outputs-only --yes deletes the draft run "
                  "and keeps the draft; --yes removes the draft; a method with "
                  "no draft is one ERROR line")
def test_delete_draft_prints_its_plan_asks_and_applies_it(
        tmp_project, monkeypatch, capsys):
    from wfc.execution.draft import read_draft_run
    from wfc.registration import read_draft

    _scenario, module, _up_run, _mid_ref = built_draft_project(tmp_project, monkeypatch)
    qualified = f"{module}.mid"
    assert read_draft_run(module, "mid", tmp_project) is not None
    capsys.readouterr()

    口 = Step(step_num=1, name="No terminal, no --yes",
             purpose="The plan prints; the refusal names --yes; nothing is deleted")
    assert cli_main(["delete-draft", qualified]) == 1
    out = capsys.readouterr()
    from wfc.execution.draft_clearing import plan_draft_delete
    removing = plan_draft_delete(module, "mid", root=tmp_project)
    assert removing.deletes and removing.keeps and removing.hint
    listed = "\n".join(["Deletes:", *(f"  - {d}" for d in removing.deletes),
                        "Keeps:", *(f"  - {k}" for k in removing.keeps),
                        removing.hint])
    assert f"Remove the draft of {qualified}\n{listed}" in out.out, out.out
    assert "--yes" in out.err and "Nothing was deleted" in out.err, out.err
    assert read_draft(module, "mid", tmp_project) is not None
    assert read_draft_run(module, "mid", tmp_project) is not None

    口 = Step(step_num=2, name="--outputs-only --yes",
             purpose="The draft run goes; the draft stays")
    deleting = plan_draft_delete(module, "mid", outputs_only=True, root=tmp_project)
    assert cli_main(["delete-draft", qualified, "--outputs-only", "--yes"]) == 0
    out = capsys.readouterr()
    assert f"Delete the outputs of {qualified}\nDeletes:" in out.out, out.out
    for line in (*deleting.deletes, *deleting.keeps):
        assert f"  - {line}\n" in out.out, out.out
    assert deleting.hint in out.out, out.out
    # The words after the delete are Execution's, as the canvas route shows them.
    assert out.out.endswith(f"Deleted the draft run of {qualified}; the draft is kept.\n"), out.out
    assert read_draft_run(module, "mid", tmp_project) is None
    assert read_draft(module, "mid", tmp_project) is not None

    口 = Step(step_num=3, name="--yes",
             purpose="The draft is removed; the method folder is untouched")
    source = read_draft(module, "mid", tmp_project).folder
    assert cli_main(["delete-draft", qualified, "--yes"]) == 0
    out = capsys.readouterr().out
    assert "your method folder and every file the draft used" in out
    assert out.endswith(f"Removed the draft of {qualified}. Your method folder "
                        f"{source} is untouched.\n"), out
    assert read_draft(module, "mid", tmp_project) is None
    assert Path(source).is_dir()

    口 = Step(step_num=4, name="No draft",
             purpose="One ERROR line, exit 1")
    assert cli_main(["delete-draft", qualified, "--yes"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR:") and len(err.strip().splitlines()) == 1, err
