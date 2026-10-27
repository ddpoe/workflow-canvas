"""E2E: after a pipeline, the cache holds the one local copy of every sample and output.

A pipeline over a file sample and a directory sample, producing a file
output and a directory output. Afterwards each restored sample, run-archive
output and checkout is a view of its cache entry (the same inode, or a
reflink) or is absent: none holds bytes of its own beside its cache entry.

Tier 3: @workflow + Step() markers.
"""

import hashlib
import os
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.conftest import requires_docker
from tests.fixtures.conftest import (
    create_sample_csv,
    register_sample_row,
    register_test_method,
    sample_source_dir,
)
from wfc import layout
from wfc.execution import run_pipeline

WFC_ROOT = Path(__file__).resolve().parent.parent.parent

# Real pipelines through the container dispatch path (register_fixture_methods
# builds a real image): deselected from the default suite, skipped without Docker.
pytestmark = [pytest.mark.integration, requires_docker]

#: A method over a file or a directory that writes a file output (every
#: input member, marked, concatenated) and a directory output (each member,
#: marked, at its relative path).
MIRROR_YAML = """\
inputs:
  data:
    type: .txt
    required: true
    description: "A file, or a directory of files"

outputs:
  copy:
    type: .txt
    required: true
    description: "Every input member, marked, in one file"
  bundle:
    type: directory
    required: true
    description: "Each input member, marked"

executor: python
env: fixture-env
"""

MIRROR_PY = """\
import json
import os
from pathlib import Path


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    src = Path(json.loads(os.environ["WFC_INPUT_PATHS"])["data"][0])
    if src.is_file():
        members = {src.name: src}
    else:
        members = {m.relative_to(src).as_posix(): m for m in sorted(src.rglob("*"))
                   if m.is_file() and not m.name.startswith(".")}
    for rel, member in members.items():
        dest = run_dir / "bundle" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"mirrored:" + member.read_bytes())
    (run_dir / "copy.txt").write_bytes(
        b"".join(b"copied:" + m.read_bytes() for m in members.values()))


if __name__ == "__main__":
    main()
"""

BUNDLE = {"a.txt": b"alpha\n", "sub/b.txt": b"beta\n"}


def _cache_objects(project_dir: Path) -> dict[str, Path]:
    """Every local cache object, by the md5 of its bytes."""
    objects = {}
    for path in layout.dvc_cache_files_dir(project_dir).rglob("*"):
        if path.is_file() and not path.name.startswith("."):
            objects[hashlib.md5(path.read_bytes()).hexdigest()] = path
    return objects


def _files(directory: Path) -> list[Path]:
    """The data files under ``directory``: dotfiles (sentinels) are not data."""
    if not directory.exists():
        return []
    return [p for p in sorted(directory.rglob("*"))
            if p.is_file() and not p.name.startswith(".")]


def _assert_view(path: Path, objects: dict[str, Path], tier: str) -> None:
    """``path`` holds no bytes of its own: it is a view of a cache object."""
    from wfc.storage import links

    md5 = hashlib.md5(path.read_bytes()).hexdigest()
    assert md5 in objects, f"{path} holds bytes no cache entry holds"
    assert not os.access(path, os.W_OK), f"the view {path} is read-only"
    if tier == links.TIER_HARDLINK:
        assert os.path.samefile(path, objects[md5]), (
            f"{path} is a second copy of its cache object, not a view of it")


def _register_mirror(project_dir: Path, tmp_path_factory) -> None:
    src = tmp_path_factory.mktemp("mirror-src")
    (src / "method.yaml").write_text(MIRROR_YAML, newline="\n")
    (src / "mirror.py").write_text(MIRROR_PY, newline="\n")
    register_test_method(project_dir=project_dir, module_name="views",
                         method_dir=src, method_name="mirror")


def _register_bundle(project_dir: Path) -> None:
    src = sample_source_dir(project_dir) / "bundle-src" / "bundle"
    for rel, content in BUNDLE.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(content)
    register_sample_row(project_dir, "bundle", src)


@workflow(
    purpose="After a pipeline over a file and a directory sample that produces "
            "a file and a directory output, on a filesystem that links, every "
            "restored sample, run-archive output and checkout is a view of its "
            "cache entry or is absent",
)
def test_after_a_pipeline_every_local_copy_is_a_view_of_the_cache_or_absent(
        pipeline_factory, register_fixture_methods, tmp_path_factory):
    from wfc.persistence import InUseKind, Run, RunOutput, Sample, get_session
    from wfc.storage import end_pipeline_samples, links
    from wfc.storage.in_use import hold_for_pipeline
    from wfc.storage.resolve import resolve_input, run_archive_path

    project_dir = register_fixture_methods

    口 = Step(step_num=1, name="Register a file sample, a directory sample and a method",
             purpose="Registration caches each sample; the mirror method reads "
                     "either and writes a file and a directory")
    create_sample_csv(project_dir, "sample_a", num_rows=3)
    _register_bundle(project_dir)
    _register_mirror(project_dir, tmp_path_factory)
    with get_session() as session:
        samples = {s.name: s for s in session.exec(select(Sample)).all()}
    assert set(samples) == {"sample_a", "bundle"}

    口 = Step(step_num=2, name="Hold both samples from outside the pipeline",
             purpose="Another reader's hold keeps each restored sample after "
                     "the pipeline's end, so the views it made can be looked "
                     "at instead of only their absence")
    for name in samples:
        hold_for_pipeline(InUseKind.sample_view, name, "observer")

    口 = Step(step_num=3, name="Run the pipeline",
             purpose="mirror runs over each sample, writing a file output and "
                     "a directory output; the archive pass caches all four")
    pipeline = pipeline_factory(
        name="one-copy",
        nodes=[
            {"id": "sel", "type": "input_selector", "samples": ["sample_a", "bundle"]},
            {"id": "mirror_1", "method": "mirror", "module": "views",
             "slot_outputs": {"copy": "copy.txt", "bundle": "bundle"}},
        ],
        links=[{"source": "sel", "target": "mirror_1"}],
        samples=[],
    )
    run_pipeline(pipeline_path=str(pipeline), project_root=str(project_dir),
                 wfc_root=str(WFC_ROOT), cores=1)
    with get_session() as session:
        runs = session.exec(select(Run)).all()
        outputs = {(r.id, o.slot): o for r in runs for o in session.exec(
            select(RunOutput).where(RunOutput.run_id == r.id)).all()}
    assert sorted(r.status for r in runs) == ["completed", "completed"]
    assert sorted(slot for _, slot in outputs) == ["bundle", "bundle", "copy", "copy"]
    assert all(o.content_hash for o in outputs.values()), "the archive pass cached every output"

    口 = Step(step_num=4, name="Read each directory output as a reader outside the pipeline does",
             purpose="The resolver reads an archived directory output through "
                     "its checkout")
    checkouts = [Path(resolve_input(r.id, "bundle")) for r in runs]
    for checkout in checkouts:
        assert os.path.samefile(checkout.parent, layout.checkouts_dir(project_dir))
    assert sorted(p.relative_to(c).as_posix() for c in checkouts
                  for p in _files(c)) == sorted([*BUNDLE, "data.csv"])

    objects = _cache_objects(project_dir)
    sample_views = []
    for name, s in samples.items():
        restored = layout.restored_sample_path(project_dir, name, s.registered_path)
        sample_views += _files(restored) if restored.is_dir() else [restored]
    tier = links.probe_pair(next(iter(objects.values())).parent,
                            layout.checkouts_dir(project_dir)).tier
    if tier == links.TIER_COPY:
        pytest.skip("this filesystem refuses links: views are copies here")

    口 = Step(step_num=5, name="Each restored sample is a view of its cache entry",
             purpose="The file sample and each member of the directory sample "
                     "share their cache object's bytes, not a copy of them")
    assert len(sample_views) == 1 + len(BUNDLE)
    for path in sample_views:
        _assert_view(path, objects, tier)

    口 = Step(step_num=6, name="Each checkout member is a view of its cache object",
             purpose="A directory output read back costs no copy")
    every_checkout = [d for d in layout.checkouts_dir(project_dir).iterdir() if d.is_dir()]
    assert len(every_checkout) == len(checkouts)
    for checkout in every_checkout:
        for path in _files(checkout):
            _assert_view(path, objects, tier)

    口 = Step(step_num=7, name="Each run-archive output is gone",
             purpose="Once its hash is committed an output leaves the run "
                     "archive; nothing left in a run archive is a second copy "
                     "of a cache object")
    for (run_id, slot), row in outputs.items():
        path = run_archive_path(row, project_dir)
        assert path is not None and not path.exists(), (
            f"run {run_id}'s {slot} output left the run archive once archived")
    for run in runs:
        for path in _files(layout.run_archive_dir(project_dir, run.id)):
            md5 = hashlib.md5(path.read_bytes()).hexdigest()
            assert md5 not in objects or os.path.samefile(path, objects[md5]), (
                f"{path} in the run archive is a second copy of a cache object")

    口 = Step(step_num=8, name="The last holder ends",
             purpose="The restored samples go with their last holder; the "
                     "cache entries keep the bytes")
    assert sorted(end_pipeline_samples(project_dir, "observer")) == ["bundle", "sample_a"]
    for name, s in samples.items():
        assert not layout.sample_dir(project_dir, name).exists()
        assert layout.dvc_cache_entry(project_dir, s.content_hash).is_file()
