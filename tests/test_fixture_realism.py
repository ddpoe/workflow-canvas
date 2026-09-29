"""The sample fixtures build a project state a user could actually produce.

No marker: this runs in the **default suite**. The failure this guards
against was a *green default suite* over a broken product — every
pipeline-running module lives behind ``-m integration``, so nothing the
default run executed had an opinion about the sample fixtures, and three
incarnations passed while every real pipeline was broken. A guard that
only runs under ``-m integration`` reproduces that blindness one level up.

Three claims, asserted about every construction path that reaches a
``samples`` row:

(a) **The name has a row.** A fixture that stages a CSV and never
    registers it models data ``wfc`` never saw. A step's cache key carries
    the content of the sample it reads, so the claim refuses an
    unregistered name — and the refusal fires in a job log, deep inside a
    pipeline nothing in the default suite runs.
(b) **Its ``content_hash`` addresses a real cache entry.** A hand-written
    content-addressed row is well-formed and its bytes are nowhere: the
    composer sees a hash, emits a ``restore_sample`` rule, and the restore
    fails on a cache entry nothing ever wrote.
(c) **``registered_path != source_path``.** Registration materializes
    nothing under ``data/samples/``; ``registered_path`` records where
    ``restore_sample`` *will* put the bytes. A source staged at its own
    registered path makes every restore ``restore_from_cache``'s
    dest-already-valid skip, so the real-copy path is never exercised.

Two paths reach a row and each gets its own witness:
``tests/fixtures/conftest.py::create_sample_csv`` (what every pipeline test
calls) and ``tests/harness/project.py::build_project`` (the scenario
harness, which routes through production registration).
They are separate code with separate staging rules and can drift apart
independently, so they are not merged into one parametrized case — a
failure has to name which path broke.
"""

from pathlib import Path

from sqlmodel import select

from axiom_annotations import workflow

from tests.fixtures.conftest import create_sample_csv
from tests.harness.project import build_project
from tests.harness.scenario import Scenario


def assert_row_is_one_a_user_could_have(
    project_dir: Path, sample_name: str, *, built_by: str
) -> None:
    """Assert the three claims about one fixture-built sample.

    Args:
        project_dir: The project root the sample was registered into.
        sample_name: The sample's name.
        built_by: The construction path under test, named in every failure
            message so a red run says which fixture broke rather than only
            which claim.

    Raises:
        AssertionError: If the sample has no row, its ``content_hash``
            addresses no cache entry, or it was registered from its own
            ``registered_path``.
    """
    from wfc.persistence import Sample, get_session

    with get_session() as session:
        row = session.exec(
            select(Sample).where(Sample.name == sample_name)
        ).first()

    # (a) the row exists at all.
    assert row is not None, (
        f"{built_by} left no samples row for '{sample_name}'. A fixture "
        f"that stages a file without registering it models data wfc never "
        f"saw: the claim refuses the name, inside a job log, in a pipeline "
        f"the default suite never runs."
    )

    # (b) the hash addresses real bytes in this project's cache.
    assert row.content_hash, (
        f"{built_by} wrote a row for '{sample_name}' with no content_hash. "
        f"The cache is content-addressed — with no hash there is nothing to "
        f"look the bytes up by."
    )
    entry = (
        project_dir / ".dvc" / "cache" / "files" / "md5"
        / row.content_hash[:2] / row.content_hash[2:]
    )
    assert entry.exists(), (
        f"{built_by} registered '{sample_name}' with content_hash "
        f"{row.content_hash}, which addresses nothing at {entry}. A row "
        f"whose bytes are not in the cache is the hand-written shape: the "
        f"composer emits a restore_sample rule for it and the restore fails "
        f"on an entry nothing ever wrote."
    )

    # (c) the restore is a real copy, not the dest-already-valid skip.
    assert Path(row.registered_path) != Path(row.source_path), (
        f"{built_by} registered '{sample_name}' from its own "
        f"registered_path ({row.registered_path}). Registration copies "
        f"nothing into data/samples/, so a source staged there makes every "
        f"restore restore_from_cache's dest-already-valid skip and the "
        f"real-copy path is never exercised. Stage the source outside the "
        f"project's data/ tree."
    )


@workflow(
    purpose=(
        "the sample fixture every pipeline test calls leaves a registered "
        "row whose content hash addresses real cached bytes, registered "
        "from outside the project"
    ),
    inputs="create_sample_csv called the way a pipeline test calls it",
    outputs="a samples row a user's own wfc register-sample could have written",
)
def test_create_sample_csv_builds_a_row_a_user_could_have(tmp_project):
    """``tests/fixtures/conftest.py::create_sample_csv`` goes through production."""
    source = create_sample_csv(tmp_project, "realism_sample")

    assert_row_is_one_a_user_could_have(
        tmp_project,
        "realism_sample",
        built_by="tests/fixtures/conftest.py::create_sample_csv",
    )

    # The helper hands back the registration *source*. It is the user's own
    # file and it stays where the user put it — outside the project tree.
    assert source.exists()
    assert (tmp_project / "data") not in source.parents


@workflow(
    purpose=(
        "the scenario harness's project builder leaves a registered row "
        "whose content hash addresses real cached bytes, registered from "
        "outside the project"
    ),
    inputs="build_project over a one-sample scenario",
    outputs="a samples row a user's own wfc register-sample could have written",
)
def test_the_scenario_harness_builds_rows_a_user_could_have(
    git_project, monkeypatch
):
    """``tests/harness/project.py::build_project`` goes through production.

    A second witness rather than a parametrization of the first: the
    harness stages its sources through its own helper and registers on its
    own schedule, so it can drift away from ``create_sample_csv`` without
    that fixture changing at all.
    """
    build_project(
        Scenario(samples=["harness_realism_sample"]),
        root=git_project,
        monkeypatch=monkeypatch,
    )

    assert_row_is_one_a_user_could_have(
        git_project,
        "harness_realism_sample",
        built_by="tests/harness/project.py::build_project",
    )
