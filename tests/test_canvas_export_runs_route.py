"""The Canvas API's run bundle route: a dry run states the plan, the download applies it.

``POST /api/wfc/export-runs?dryRun=true`` returns Run bundle's plan (steps,
every refusal, the local-only image warnings) and its opaque token. The
download sends the token back and receives the zip the facade writes. Runs
come from ``tests.fixtures.routes.completed_run``: production's phases on the
harness's stub rung, whose env image was never pushed, so every step's image
is one only this machine holds. The route is reached through the canvas test
client (``tests.fixtures.routes.canvas_client``).
"""

from __future__ import annotations

import dataclasses
import io
import json
import zipfile

from axiom_annotations import Step, workflow

from tests.fixtures import routes
from tests.fixtures.routes import completed_run
from tests.harness.scenario import DEFAULT_MODULE, DEFAULT_SAMPLE

ROUTE = "/api/wfc/export-runs"


def _old_run(method: str) -> int:
    """A completed run made through the route that records no invocation."""
    from wfc.execution.claim import register_run
    from wfc.execution.record import complete_run

    run_id = register_run(method, DEFAULT_MODULE, DEFAULT_SAMPLE)
    complete_run(run_id)
    return run_id


@workflow(purpose="The bundle route's contract: the dry run returns the plan (its steps "
                  "and the never-pushed image warning) and a token; the download with "
                  "that token streams the bundle zip; a download without a token is a "
                  "400 and one whose token no longer matches the plan is a 409, both "
                  "writing nothing",
          inputs="One completed run on the stub env, whose image has no push record",
          outputs="A plan payload, one zip, a 400 and a 409")
def test_the_dry_run_states_the_plan_and_only_its_token_downloads_the_bundle(
    tmp_project, monkeypatch,
):
    """Dry run, download with the token, without one, and with a stale one."""
    run_id = completed_run(tmp_project, monkeypatch=monkeypatch, method="local").run_id
    client = routes.canvas_client(tmp_project, monkeypatch)

    口 = Step(step_num=1, name="Dry run",
             purpose="The plan: one step, no refusal, the local-only warning, a token")
    dry = client.post(ROUTE, params={"dryRun": "true"},
                      json={"run_ids": [str(run_id)]})
    assert dry.status_code == 200, dry.text
    plan = dry.json()["plan"]
    assert plan["ok"] is True and plan["refusals"] == []
    assert [s["run_id"] for s in plan["steps"]] == [run_id]
    assert [w["run_id"] for w in plan["warnings"]] == [run_id]
    token = plan["token"]
    assert isinstance(token, str) and token

    口 = Step(step_num=2, name="Download without a token",
             purpose="A 400 naming the dry run; no zip")
    bare = client.post(ROUTE, json={"run_ids": [str(run_id)]})
    assert bare.status_code == 400, bare.text
    assert "dry run" in bare.json()["detail"]

    口 = Step(step_num=3, name="Download with a token from another plan",
             purpose="The dry run stated a bundle without outputs; a download asking "
                     "for one with outputs is not that plan: a 409")
    stale = client.post(ROUTE, json={"run_ids": [str(run_id)], "with_outputs": True,
                                     "plan_token": token})
    assert stale.status_code == 409, stale.text
    assert "changed since the dry run" in stale.json()["detail"]["message"]

    口 = Step(step_num=4, name="Download with the token",
             purpose="The zip the facade writes, named as an attachment")
    download = client.post(ROUTE, json={"run_ids": [str(run_id)], "plan_token": token})
    assert download.status_code == 200, download.text
    assert download.headers["content-type"] == "application/zip"
    assert "attachment" in download.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(download.content)) as bundle:
        names = set(bundle.namelist())
        manifest = json.loads(bundle.read("wfc-run.json"))
    assert {"README.md", "rerun.py", "wfc-run.json", "ro-crate-metadata.json"} <= names
    assert [s["run_id"] for s in manifest["steps"]] == [run_id]


@workflow(purpose="A selection the bundle cannot hold completely: the dry run lists "
                  "every reason with ok false, and the download is a 409 naming every "
                  "reason, a run not found and a run with no invocation record alike")
def test_a_refused_selection_is_a_409_naming_every_reason(tmp_project, monkeypatch):
    """Every refusal, in the dry run and in the download's 409."""
    completed_run(tmp_project, monkeypatch=monkeypatch, method="legacy")
    old = _old_run("legacy")
    missing = old + 100
    client = routes.canvas_client(tmp_project, monkeypatch)
    body = {"run_ids": [str(old), str(missing)]}

    dry = client.post(ROUTE, params={"dryRun": "true"}, json=body)
    assert dry.status_code == 200, dry.text
    plan = dry.json()["plan"]
    assert plan["ok"] is False
    reasons = [r["reason"] for r in plan["refusals"]]
    assert any(f"Run {missing} not found" in r for r in reasons)
    assert any(f"Run {old} " in r and "no invocation record" in r for r in reasons)

    refused = client.post(ROUTE, json={**body, "plan_token": plan["token"]})
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["reasons"] == reasons
    assert "Nothing was written" in detail["message"]


def _members(content: bytes) -> list[tuple[str, str]]:
    """Each zip member's name and the MD5 of its bytes, in zip order.

    The export time is the one value two exports of the same runs may not
    share: its ISO string (``wfc-run.json``'s ``exported_at``, the crate's
    ``datePublished``) and the README's ``Exported <date>`` are replaced by a
    placeholder before hashing.
    """
    import hashlib
    import re

    with zipfile.ZipFile(io.BytesIO(content)) as bundle:
        moment = json.loads(bundle.read("wfc-run.json"))["exported_at"]
        members = []
        for name in bundle.namelist():
            data = bundle.read(name).replace(moment.encode("utf-8"), b"<exported_at>")
            if name == "README.md":
                data = re.sub(rb"Exported \d{4}-\d{2}-\d{2}", b"Exported <date>", data)
            members.append((name, hashlib.md5(data).hexdigest()))
    return members


@workflow(purpose="History's export and wfc export-run are one builder. The "
                  "route's dry run and download, confirmed past the never-pushed image "
                  "warning, give the same zip members, in the same order with the "
                  "same bytes, as wfc export-run --yes over the same runs and options, "
                  "with outputs and without; a dry run with Include images names the "
                  "image to save and warns of nothing",
          inputs="Two completed runs on the stub env (a file output and a directory "
                 "output), whose image has no push record; the daemon's image inspect "
                 "answered by the registered fake",
          outputs="Two pairs of zips compared member by member; one with_image plan")
def test_the_route_downloads_what_export_run_writes_for_the_same_runs(
    cli, tmp_project, monkeypatch, tmp_path,
):
    """The route's zip and the CLI's zip hold the same members and bytes."""
    from tests.fixtures.fakes import stub_docker_image_inspect

    first = completed_run(tmp_project, monkeypatch=monkeypatch, method="local").run_id
    second = completed_run(tmp_project, monkeypatch=monkeypatch, method="dir_out").run_id
    client = routes.canvas_client(tmp_project, monkeypatch)
    runs = {"run_ids": [str(first), str(second)]}

    for with_outputs in (False, True):
        body = {**runs, "with_outputs": with_outputs}
        口 = Step(step_num=1.1, name="Canvas: dry run, then download with its token",
                 purpose="What Export runs does once the warning's confirm is pressed, "
                         "with Include outputs off and on")
        plan = client.post(ROUTE, params={"dryRun": "true"}, json=body).json()["plan"]
        assert plan["ok"] is True and plan["warnings"], plan
        assert plan["with_outputs"] is with_outputs
        download = client.post(ROUTE, json={**body, "plan_token": plan["token"]})
        assert download.status_code == 200, download.text

        口 = Step(step_num=1.2, name="CLI: wfc export-run --yes over the same runs",
                 purpose="--yes answers the same never-pushed image question; "
                         "--with-outputs when Include outputs is on")
        out = tmp_path / f"cli-{with_outputs}.zip"
        argv = ["export-run", "--run", str(first), "--run", str(second),
                "--out", str(out), "--yes"]
        if with_outputs:
            argv.append("--with-outputs")
        written = cli(*argv)
        assert written.returncode == 0, written

        口 = Step(step_num=1.3, name="Compare member by member",
                 purpose="The same member names in the same order, each with the same "
                         "bytes, the export time aside")
        route_members = _members(download.content)
        cli_members = _members(out.read_bytes())
        assert [n for n, _ in route_members] == [n for n, _ in cli_members]
        assert route_members == cli_members
        assert len({n.split("/")[1] for n, _ in route_members
                    if n.startswith("steps/")}) == 2
        assert any(n.startswith("outputs/") for n, _ in route_members) is with_outputs

    口 = Step(step_num=2, name="Canvas: dry run with Include images",
             purpose="The daemon holds the image: the plan carries with_image, the "
                     "image to save, and no never-pushed warning")
    stub_docker_image_inspect(monkeypatch, "sha256:" + "c" * 64)
    held = client.post(ROUTE, params={"dryRun": "true"},
                       json={**runs, "with_image": True}).json()["plan"]
    assert held["ok"] is True, held
    assert held["with_image"] is True
    assert held["images"], "the plan names the image to save"
    assert held["warning_lines"] == []


def test_run_ids_that_are_not_run_numbers_are_a_400(canvas_client):
    """No run, or a run id that is not a number, is refused before any plan."""
    client = canvas_client
    assert client.post(ROUTE, params={"dryRun": "true"},
                       json={"run_ids": []}).status_code == 400
    assert client.post(ROUTE, params={"dryRun": "true"},
                       json={"run_ids": ["seven"]}).status_code == 400


def test_the_plan_token_names_what_the_bundle_would_hold(tmp_project, monkeypatch):
    """The same plan replanned has the same token; any change to its content does not.

    The archive count is no part of it: the dry run's archive pass hashes the
    outputs that the download's pass then finds already hashed.
    """
    from wfc.run_bundle import plan_export

    run_id = completed_run(tmp_project, monkeypatch=monkeypatch, method="local").run_id
    first = plan_export([run_id], project_dir=tmp_project)
    again = plan_export([run_id], project_dir=tmp_project)
    assert first.token == again.token
    assert dataclasses.replace(first, archived=first.archived + 3).token == first.token

    (step,) = first.steps
    (output,) = step.outputs[:1]
    changed_output = dataclasses.replace(output, content_hash="0" * 32)
    changed = dataclasses.replace(
        first, steps=(dataclasses.replace(step, outputs=(changed_output,)
                                          + step.outputs[1:]),))
    assert changed.token != first.token
    assert dataclasses.replace(first, with_image=True).token != first.token
    assert dataclasses.replace(first, local_only=()).token != first.token
