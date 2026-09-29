"""The artifact member route serves a directory output's members and nothing
outside them.

Tier 2, entered at the provider (the route's one callee) and at the route for
the status a browser sees. The run is driven through the harness, archived
through production, and read through the directory's checkout.

Declared fixture deviation: ``test_a_symlink_out_of_a_checkout_is_refused``
plants a symlink in a checkout, which neither collection (it refuses
symlinks) nor checkout writes; it is the state a hand edit leaves.
"""
from __future__ import annotations

import os

import pytest
from axiom_annotations import workflow

from tests.fixtures.fakes.server_state import bind_provider
from tests.fixtures.routes import canvas_client, completed_run
from tests.harness.scenario import Behavior

TRAVERSALS = [
    ("tiles/../report.csv", "'..'"),
    ("tiles/sub/../../report.csv", "'..'"),
    ("/tiles/t0.png", "absolute"),
    ("C:/Windows/win.ini", "drive"),
    ("tiles\\t0.png", "backslash"),
    ("tiles/..\\..\\report.csv", "backslash"),
    ("tiles/%2e%2e/report.csv", "percent-escape"),
    ("tiles%2ft0.png", "percent-escape"),
]


def _archived_run(root, monkeypatch):
    """A completed run with a file and a directory output, archived."""
    from wfc.canvas.wfc_provider import WfcProvider
    from wfc.storage import archive_outputs

    run = completed_run(
        root, monkeypatch=monkeypatch, method="m", module="mod", sample="s1",
        outputs={"report": ".csv", "tiles": "directory"},
        output_files={"report": "report.csv", "tiles": "tiles"},
        behavior=Behavior(outputs={
            "report": "x,y\n1,2\n",
            "tiles": {"t0.png": "PNG-a", "t1.png": "PNG-b"},
        }),
    )
    archive_outputs(root, run_id=run.run_id)
    return str(run.run_id), WfcProvider(str(root))


@workflow(purpose="The member route serves a directory output's member and "
                  "refuses '..', absolute, drive, backslash and "
                  "percent-encoded traversal by name, never rewriting them")
def test_member_route_serves_members_and_refuses_traversal(tmp_project,
                                                           monkeypatch):
    from wfc.canvas.wfc_provider import ArtifactPathRefusedError

    rid, provider = _archived_run(tmp_project, monkeypatch)

    assert provider.get_artifact_path(rid, "tiles/t0.png").read_bytes() == b"PNG-a"
    assert provider.get_artifact_path(rid, "tiles/t1.png").read_bytes() == b"PNG-b"
    for name, reason in TRAVERSALS:
        with pytest.raises(ArtifactPathRefusedError, match=reason):
            provider.get_artifact_path(rid, name)

    client = canvas_client(tmp_project, monkeypatch)
    bind_provider(monkeypatch, provider)
    ok = client.get(f"/api/wfc/run/{rid}/artifact/tiles/t0.png")
    assert ok.status_code == 200 and ok.content == b"PNG-a"
    refused = client.get(f"/api/wfc/run/{rid}/artifact/tiles%5Ct0.png")
    assert refused.status_code == 400
    assert "backslash" in refused.json()["detail"]


@workflow(purpose="A member that resolves outside its output through a "
                  "symlink is refused, not served")
def test_a_symlink_out_of_a_checkout_is_refused(tmp_project, monkeypatch):
    from wfc.canvas.wfc_provider import ArtifactPathRefusedError

    rid, provider = _archived_run(tmp_project, monkeypatch)
    checkout = provider.get_artifact_path(rid, "tiles")
    outside = tmp_project / "secret.txt"
    outside.write_text("not an output\n")
    link = checkout / "leak.txt"
    os.chmod(checkout, 0o755)
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"this host cannot create a symlink ({exc})")

    with pytest.raises(ArtifactPathRefusedError, match="outside the output"):
        provider.get_artifact_path(rid, "tiles/leak.txt")
