"""The push record of an env image, and Docker's registry answers classified.

``pushed.json`` sits in the image's captures folder beside ``image.json``.
It is added once, after a push succeeded, as LF bytes naming the base pushed
to, and never changes the folder's other files; a second write at the same
base keeps the first record, and a push to another base replaces it. A
record naming no base is at the project's base, and a base switch names it.
The registry read wfc makes through the Docker CLI is classified from Docker's own words
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

OLD_BASE = "registry.lab.example/lab"
NEW_BASE = "ghcr.io/lab/project"


def _register_byo(project: Path):
    from wfc import environments as envs_mod

    return envs_mod.register(name="tools", backend="byo",
                             source={"image": "docker://ghcr.io/lab/tools:1.0"},
                             project_dir=project)


def _folder_bytes(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file()}


@workflow(purpose="A push is recorded once beside the image's captures: "
                  "pushed.json holds the digest, the tag, an ISO UTC push "
                  "time and the base pushed to as LF bytes, the folder's "
                  "other files are untouched, a second record for the same "
                  "image at the same base keeps the first, and a push to "
                  "another base replaces it")
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
                                    digest=FAKE_PUSHED_DIGEST, tag="fp-0123456789ab",
                                    base=OLD_BASE)

    assert created
    raw = (folder / "pushed.json").read_bytes()
    assert b"\r\n" not in raw and raw.endswith(b"\n")
    on_disk = json.loads(raw)
    assert on_disk == {"digest": FAKE_PUSHED_DIGEST, "tag": "fp-0123456789ab",
                       "pushed_at": written.pushed_at, "base": OLD_BASE}
    assert written.pushed_at.endswith("Z") and "T" in written.pushed_at
    after = _folder_bytes(folder)
    assert {k: v for k, v in after.items() if k != "pushed.json"} == before
    assert pushed_record("tools", fingerprint, tmp_project) == written

    again, created_again = write_pushed(tmp_project, "tools", fingerprint,
                                        digest="sha256:" + "e" * 64, tag="fp-other",
                                        base=OLD_BASE)
    assert not created_again
    assert again == written
    assert (folder / "pushed.json").read_bytes() == raw
    assert not [p.name for p in folder.iterdir() if p.name.startswith(".pushed.")]

    moved, created_moved = write_pushed(tmp_project, "tools", fingerprint,
                                        digest="sha256:" + "e" * 64,
                                        tag="fp-0123456789ab", base=NEW_BASE)
    assert created_moved and moved.base == NEW_BASE
    assert json.loads((folder / "pushed.json").read_bytes()) == {
        "digest": "sha256:" + "e" * 64, "tag": "fp-0123456789ab",
        "pushed_at": moved.pushed_at, "base": NEW_BASE}
    after_moved = _folder_bytes(folder)
    assert {k: v for k, v in after_moved.items() if k != "pushed.json"} == before


def test_a_switch_names_the_base_on_a_record_that_names_none(tmp_project, monkeypatch):
    """A pushed.json naming no base is at the project's base; a switch names it."""
    from wfc.environments.captures import (
        PushedRecord,
        name_pushed_bases,
        pushed_record,
    )

    stub_docker_pull(monkeypatch)
    stub_docker_image_inspect(monkeypatch, "sha256:" + "c" * 64)
    stub_docker_repo_digest(monkeypatch, lambda ref, repository: "sha256:" + "a" * 64)
    stub_docker_image_content(monkeypatch)
    fingerprint = _register_byo(tmp_project).env_fingerprint
    path = tmp_project / ".wfc" / "captures" / "tools" / fingerprint / "pushed.json"
    # A record in the format wfc wrote before push records named their base:
    # no production writer makes one any more, and projects still hold them.
    unnamed = PushedRecord(digest=FAKE_PUSHED_DIGEST, tag="fp-0123456789ab",
                           pushed_at="2026-10-20T10:00:00Z")
    path.write_bytes(unnamed.to_json().encode("utf-8"))
    assert unnamed.pushed_to(OLD_BASE) == OLD_BASE

    assert name_pushed_bases(tmp_project, OLD_BASE) == [path]
    assert pushed_record("tools", fingerprint, tmp_project) == PushedRecord(
        digest=FAKE_PUSHED_DIGEST, tag="fp-0123456789ab",
        pushed_at="2026-10-20T10:00:00Z", base=OLD_BASE)
    assert name_pushed_bases(tmp_project, OLD_BASE) == [path]  # named already
    assert name_pushed_bases(tmp_project, NEW_BASE) == []


def test_push_record_needs_the_images_folder_and_a_digest(tmp_project):
    from wfc.environments.captures import CaptureMissingError, write_pushed

    with pytest.raises(CaptureMissingError):
        write_pushed(tmp_project, "tools", "f" * 32,
                     digest=FAKE_PUSHED_DIGEST, tag="fp-0123456789ab", base=OLD_BASE)
    with pytest.raises(ValueError):
        write_pushed(tmp_project, "tools", "f" * 32, digest="latest", tag="fp-x",
                     base=OLD_BASE)
    with pytest.raises(ValueError):
        write_pushed(tmp_project, "tools", "f" * 32, digest=FAKE_PUSHED_DIGEST,
                     tag="fp-x", base="")
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
