"""``wfc runs``: every run's id, pipeline, node, method, sample and status.

Runs come from ``tests.fixtures.routes.completed_run`` (production's five
phases on the harness's stub rung) under two pipelines; the verb runs
in-process through ``run_cli``.
"""

from __future__ import annotations

from axiom_annotations import workflow

from tests.fixtures.routes import completed_run, run_cli


def _rows(stdout: str) -> list[list[str]]:
    """The table's body rows, split into columns; the header line is checked."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines[0].split() == ["RUN", "PIPELINE", "NODE", "METHOD", "SAMPLE",
                                "STATUS"]
    # The method cell is "<module>.<method> v<N>": two words of the split.
    return [[*row[:3], " ".join(row[3:5]), *row[5:]]
            for row in (line.split() for line in lines[1:])]


@workflow(purpose="wfc runs lists every run with its run id, pipeline, node, "
                  "method and version, sample and status, and --pipeline keeps "
                  "only that pipeline's runs",
          inputs="One completed run in each of two pipelines",
          outputs="Both rows unfiltered, one row under --pipeline, exit 0, "
                  "nothing on stderr")
def test_wfc_runs_lists_each_run_and_filters_by_pipeline(tmp_project, monkeypatch):
    one = completed_run(tmp_project, monkeypatch=monkeypatch, method="m_one",
                        pipeline_id="p-one", name="one")
    two = completed_run(tmp_project, monkeypatch=monkeypatch, method="m_two",
                        pipeline_id="p-two", name="two")

    listed = run_cli("runs")
    assert listed.returncode == 0, listed
    assert listed.stderr == ""
    assert _rows(listed.stdout) == [
        [str(one.run_id), "p-one", "m_one", "harness_mod.m_one v1", one.target[1],
         "completed"],
        [str(two.run_id), "p-two", "m_two", "harness_mod.m_two v1", two.target[1],
         "completed"],
    ]

    filtered = run_cli("runs", "--pipeline", "p-two")
    assert filtered.returncode == 0, filtered
    assert _rows(filtered.stdout) == [
        [str(two.run_id), "p-two", "m_two", "harness_mod.m_two v1", two.target[1],
         "completed"],
    ]


@workflow(purpose="wfc runs --method keeps only the runs of the method named "
                  "module.method; a bare name is refused; with --pipeline both "
                  "filters apply",
          inputs="Runs of one method name in two modules, and of a second method",
          outputs="One run under each qualified name, one under a name and a "
                  "pipeline, exit 0; a bare name exits 1 with an error and no rows")
def test_wfc_runs_method_filter_takes_a_qualified_name(tmp_project, monkeypatch):
    here = completed_run(tmp_project, monkeypatch=monkeypatch, method="m_plot",
                         pipeline_id="p-one", name="one")
    there = completed_run(tmp_project, monkeypatch=monkeypatch, method="m_plot",
                          module="other_mod", pipeline_id="p-two", name="two")
    completed_run(tmp_project, monkeypatch=monkeypatch, method="m_other",
                  pipeline_id="p-one", name="one")

    def method_runs(name: str) -> list[tuple[str, str]]:
        result = run_cli("runs", "--method", name)
        assert result.returncode == 0, result
        return [(row[0], row[3]) for row in _rows(result.stdout)]

    assert method_runs("harness_mod.m_plot") == [(str(here.run_id),
                                                  "harness_mod.m_plot v1")]
    assert method_runs("other_mod.m_plot") == [(str(there.run_id),
                                                "other_mod.m_plot v1")]

    bare = run_cli("runs", "--method", "m_plot")
    assert bare.returncode == 1, bare
    assert "module.method" in bare.stderr
    assert bare.stdout == ""

    both = run_cli("runs", "--method", "other_mod.m_plot", "--pipeline", "p-two")
    assert both.returncode == 0, both
    assert [(row[0], row[3]) for row in _rows(both.stdout)] == [
        (str(there.run_id), "other_mod.m_plot v1")]
