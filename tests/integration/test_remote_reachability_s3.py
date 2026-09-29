"""A non-local DVC remote is probed for real, and a failure names it.

Tier 3 (integration). The remote is an S3 bucket on a local moto server (a
true external edge, served over HTTP; nothing in wfc or DVC is patched). The
project is scaffolded by production ``init_project`` with an ``s3://`` archive,
and the endpoint is set with DVC's own ``dvc remote modify``, as a user would.
"""
from __future__ import annotations

import socket
import subprocess
import sys

import pytest
from axiom_annotations import Step, workflow

pytestmark = [pytest.mark.integration]

BUCKET = "wfc-reach"
URL = f"s3://{BUCKET}/archive"
MISSING_URL = "s3://no-such-bucket/archive"


def _modify_url(root, url: str) -> None:
    subprocess.run([sys.executable, "-m", "dvc", "remote", "modify",
                    "default", "url", url],
                   cwd=root, check=True, capture_output=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@workflow(purpose="check_remote_reachable reports an S3 remote reachable while "
                  "its server answers, and names the remote when it is down")
def test_an_s3_remote_is_probed_and_named_when_down(tmp_path, monkeypatch):
    pytest.importorskip("dvc_s3", reason="the s3 extra is not installed")
    moto_server = pytest.importorskip("moto.server",
                                      reason="moto[server] is not installed")
    import boto3

    from wfc.init import init_project
    from wfc.storage import check_remote_reachable
    from tests.fixtures.fakes import stub_readiness_probes

    _ = Step(step_num=1, name="Serve a bucket and point a project at it",
             purpose="A moto S3 server with the bucket; the project's archive "
                     "is s3:// with the endpoint set through dvc remote modify")
    for key, value in {"AWS_ACCESS_KEY_ID": "testing",
                       "AWS_SECRET_ACCESS_KEY": "testing",
                       "AWS_DEFAULT_REGION": "us-east-1",
                       "AWS_MAX_ATTEMPTS": "1"}.items():
        monkeypatch.setenv(key, value)
    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}"
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    try:
        boto3.client("s3", endpoint_url=endpoint).create_bucket(Bucket=BUCKET)
        root = tmp_path / "proj"
        with pytest.MonkeyPatch.context() as mp:
            stub_readiness_probes(mp)
            init_project(root, archive=URL, assume_yes=True)
        subprocess.run([sys.executable, "-m", "dvc", "remote", "modify",
                        "default", "endpointurl", endpoint],
                       cwd=root, check=True, capture_output=True)

        _ = Step(step_num=2, name="The remote answers",
                 purpose="Probed through DVC, not skipped as non-local")
        assert check_remote_reachable(root) == (True, "")

        _ = Step(step_num=3, name="A missing bucket does not answer",
                 purpose="The server answers, but no parent of the root "
                         "exists; the reason names the remote and the URL "
                         "DVC probed, not the one wf-canvas.toml records")
        _modify_url(root, MISSING_URL)
        ok, reason = check_remote_reachable(root)
        assert ok is False and "'default'" in reason
        assert MISSING_URL in reason and URL not in reason

        _ = Step(step_num=4, name="Restore the bucket's URL",
                 purpose="The remote answers again, so the next step "
                         "isolates the server being down")
        _modify_url(root, URL)
        assert check_remote_reachable(root) == (True, "")
    finally:
        server.stop()

    _ = Step(step_num=5, name="The server is down",
             purpose="Only the server changed: unreachable, and the reason "
                     "names the remote and its URL")
    ok, reason = check_remote_reachable(root)
    assert ok is False
    assert "'default'" in reason and URL in reason
