"""Execution's run tallies: how many runs each method, version, sample and env has.

One read answers what the Registry and Envs tabs show beside each row: the
run count per method, per method version, per sample and per env spec (a
method's ``env``), and the newest run's start time. Every ``Run`` row counts,
a cache-hit row included, as the tabs always counted them. A key with no run
reads as zero runs and no start time.

Runs come from ``tests.fixtures.routes.completed_run``: production's five
phases on the harness's stub rung, so each row's method, version, sample and
start time are the ones the claim wrote.
"""

from __future__ import annotations

from axiom_annotations import workflow
from sqlmodel import select

from tests.fixtures.routes import completed_run
from wfc.execution import RunTally, run_tallies
from wfc.persistence import Method, get_session


@workflow(purpose="The run tallies count every run row per method, per method "
                  "version, per sample and per env spec, with the newest start "
                  "time; a method, version, sample or env with no run reads as "
                  "zero runs and no start time",
          inputs="Two methods sharing one env, run on two samples, one run "
                 "repeated as a cache hit, all driven through production's phases",
          outputs="Counts and newest start times equal to what the rows record, "
                  "and the zero tally for keys no run names")
def test_run_tallies_count_each_methods_versions_samples_and_envs_runs(
    tmp_project, monkeypatch
):
    samples = ["s1", "s2"]
    drives = [("alpha", "s1"), ("alpha", "s2"), ("beta", "s1"), ("alpha", "s1")]
    driven = [
        completed_run(tmp_project, monkeypatch=monkeypatch, method=method,
                      sample=sample, samples=samples,
                      pipeline_id=f"tally-{i}", name=f"tally_{i}")
        for i, (method, sample) in enumerate(drives)
    ]
    rows = driven[-1].observation.run_rows
    assert len(rows) == 4, "four drives, four run rows (the repeat is a row too)"
    alpha, _, beta, repeat = (d.run_row for d in driven)
    assert repeat["cache_source_run_id"] == alpha["id"], \
        "the repeated alpha/s1 drive is a cache hit on the first one"
    with get_session() as session:
        env = session.exec(select(Method.env)
                           .where(Method.id == alpha["method_id"])).one()

    tallies = run_tallies()

    def newest(keep) -> object:
        return max(r["started_at"] for r in rows if keep(r))

    assert tallies.for_method(alpha["method_id"]) == RunTally(
        3, newest(lambda r: r["method_id"] == alpha["method_id"]))
    assert tallies.for_method(beta["method_id"]) == RunTally(1, beta["started_at"])
    assert tallies.for_version(alpha["version_id"]) == RunTally(
        3, newest(lambda r: r["version_id"] == alpha["version_id"]))
    assert tallies.for_version(beta["version_id"]).count == 1
    assert tallies.for_sample("s1") == RunTally(
        3, newest(lambda r: r["sample"] == "s1"))
    assert tallies.for_sample("s2").count == 1
    assert tallies.for_env(env) == RunTally(4, newest(lambda r: True))

    nothing = RunTally(0, None)
    assert tallies.for_method(-1) == nothing
    assert tallies.for_version(None) == nothing
    assert tallies.for_sample("never-run") == nothing
    assert tallies.for_env("container:never-used") == nothing
