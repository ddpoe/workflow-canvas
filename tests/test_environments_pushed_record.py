"""The push record of an env image, and Docker's registry answers classified.

``pushed.json`` sits in the image's captures folder beside ``image.json``.
It is added once, after a push succeeded, as LF bytes, and never changes the
folder's other files; a second write keeps the first record. The registry
read wfc makes through the Docker CLI is classified from Docker's own words
into found, missing, unauthorized and unreachable.

Images are registered through production ``register`` with the Docker
boundary faked (``tests.fixtures.fakes``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from axiom_annotations import workflow

from tests.fixtures.fakes import (
    FAKE_PUSHED_DIGEST,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_docker_repo_digest,
)


def _register_byo(project: Path):
    from wfc import environments as envs_mod

    return envs_mod.register(name="tools", backend="byo",
                             source={"image": "docker://ghcr.io/lab/tools:1.0"},
                             project_dir=project)


def _folder_bytes(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file()}


@workflow(purpose="A push is recorded once beside the image's captures: "
                  "pushed.json holds the digest, the tag and an ISO UTC push "
                  "time as LF bytes, the folder's other files are untouched, "
                  "and a second record for the same image keeps the first")
def test_push_record_is_written_once_beside_the_captures(tmp_project, monkeypatch):
    from wfc.environments.captures import pushed_record, write_pushed

    stub_docker_pull(monkeypatch)
    stub_docker_image_inspect(monkeypatch, "sha256:" + "c" * 64)
    stub_docker_repo_digest(monkeypatch, lambda ref, repository: "sha256:" + "a" * 64)
    stub_docker_image_content(monkeypatch)
    record = _register_byo(tmp_project)
    fingerprint = record.env_fingerprint
    folder = tmp_project / ".wfc" / "captures" / "tools" / fingerprint
    before = _folder_bytes(folder)
    assert pushed_record("tools", fingerprint, tmp_project) is None

    written, created = write_pushed(tmp_project, "tools", fingerprint,
                                    digest=FAKE_PUSHED_DIGEST, tag="fp-0123456789ab")

    assert created
    raw = (folder / "pushed.json").read_bytes()
    assert b"\r\n" not in raw and raw.endswith(b"\n")
    on_disk = json.loads(raw)
    assert on_disk == {"digest": FAKE_PUSHED_DIGEST, "tag": "fp-0123456789ab",
                       "pushed_at": written.pushed_at}
    assert written.pushed_at.endswith("Z") and "T" in written.pushed_at
    after = _folder_bytes(folder)
    assert {k: v for k, v in after.items() if k != "pushed.json"} == before
    assert pushed_record("tools", fingerprint, tmp_project) == written

    again, created_again = write_pushed(tmp_project, "tools", fingerprint,
                                        digest="sha256:" + "e" * 64, tag="fp-other")
    assert not created_again
    assert again == written
    assert (folder / "pushed.json").read_bytes() == raw
    assert not [p.name for p in folder.iterdir() if p.name.startswith(".pushed.")]


def test_push_record_needs_the_images_folder_and_a_digest(tmp_project):
    from wfc.environments.captures import CaptureMissingError, write_pushed

    with pytest.raises(CaptureMissingError):
        write_pushed(tmp_project, "tools", "f" * 32,
                     digest=FAKE_PUSHED_DIGEST, tag="fp-0123456789ab")
    with pytest.raises(ValueError):
        write_pushed(tmp_project, "tools", "f" * 32, digest="latest", tag="fp-x")
    assert not (tmp_project / ".wfc" / "captures" / "tools").exists()


def test_an_unreadable_push_record_is_named(tmp_project):
    from wfc.environments.captures import CaptureMismatchError, pushed_record

    folder = tmp_project / ".wfc" / "captures" / "tools" / ("f" * 32)
    folder.mkdir(parents=True)
    (folder / "pushed.json").write_text('{"digest": "latest"}', encoding="utf-8")

    with pytest.raises(CaptureMismatchError, match="pushed.json"):
        pushed_record("tools", "f" * 32, tmp_project)


@pytest.mark.parametrize(("returncode", "stderr", "state"), [
    (0, "", "found"),
    (1, "no such manifest: ghcr.io/lab/repo/tools:fp-0123456789ab", "missing"),
    (1, "manifest unknown: manifest unknown", "missing"),
    (1, "errors:\nunauthorized: authentication required", "unauthorized"),
    (1, "denied: requested access to the resource is denied", "unauthorized"),
    (1, 'Get "https://127.0.0.1:1/v2/": dial tcp 127.0.0.1:1: connect: '
        "connection refused", "unreachable"),
    (1, "lookup nowhere.invalid: no such host", "unreachable"),
    (1, "something docker never says", "error"),
])
def test_registry_answers_are_classified_from_dockers_words(returncode, stderr, state):
    """Found or no such manifest: reachable; unauthorized/denied: not logged
    in; connection errors: unreachable; anything else: an error kept verbatim."""
    from wfc.environments.docker import RegistryRead, classify_manifest_answer

    assert classify_manifest_answer(returncode, stderr) == state
    assert RegistryRead("r", state, stderr).reachable == (state in ("found", "missing"))
