"""Production-path builders: the one route to each state a test starts from.

Every function here produces state by running production code -- ``wfc
init``'s ``init_project``, ``register_module`` / ``register_method``,
``register_sample``, the env-manifest serialization, the ``wfc`` CLI entry
point, and (for the run builders) the scenario harness's real claim ->
materialize -> dispatch -> collect -> record phases. Nothing here writes a
``Run``, ``RunOutput``, ``Method`` or ``Module`` row by hand.

Each builder's docstring carries two lines a witness built on it inherits:

- *Pins:* what the route fixes or substitutes that a user's own path would
  not (an environment pin, a stubbed probe, a placeholder digest).
- *Does not prove:* what a test on this route therefore cannot claim.

The package is split by concern; every builder is re-exported here so
``from tests.fixtures import routes; routes.completed_run`` and
``from tests.fixtures.routes import completed_run`` both resolve:

  - ``roots``: init_test_project (a project ``wfc init`` would have
    produced), project_archive_dir / sample_source_dir (the two out-of-tree
    locations the routes stage into -- inputs, not state).
  - ``registration``: register_test_method, register_sample_row /
    create_sample_csv, write_env_record.
  - ``runs``: completed_run / claimed_run (a run produced by production's
    own phases on the harness's stub rung) and the DrivenRun handle.
  - ``clients``: run_cli (one in-process ``wfc`` invocation) and
    canvas_client (a test client over the canvas app).
  - ``snapshots``: build_project_snapshot / restore_project_snapshot (the
    module-scoped build-once, restore-per-test shape).

Fixtures over these routes live in ``tests/conftest.py`` (``tmp_project``,
``cli``, ``canvas_db``, ``canvas_client``) and ``tests/fixtures/conftest.py``
(``register_fixture_methods``, ``pipeline_factory``). The fakes a route
installs (the readiness probes ``init_test_project`` scopes to its own call,
the stub rung the run builders drive on) are entries of
``tests.fixtures.fakes``.
"""

from __future__ import annotations

from .clients import CliResult, canvas_client, run_cli
from .registration import (
    create_sample_csv,
    register_sample_row,
    register_test_method,
    write_env_record,
)
from .roots import init_test_project, project_archive_dir, sample_source_dir
from .runs import (
    ROUTE_METHOD,
    ROUTE_PIPELINE_ID,
    ROUTE_PIPELINE_NAME,
    DrivenRun,
    _drive_declared_run,
    _resolve_declaration,
    _route_scenario,
    claimed_run,
    completed_run,
)
from .snapshots import (
    ProjectSnapshot,
    build_project_snapshot,
    restore_project_snapshot,
)

__all__ = [
    "CliResult",
    "DrivenRun",
    "ProjectSnapshot",
    "ROUTE_METHOD",
    "ROUTE_PIPELINE_ID",
    "ROUTE_PIPELINE_NAME",
    "build_project_snapshot",
    "canvas_client",
    "claimed_run",
    "completed_run",
    "create_sample_csv",
    "init_test_project",
    "project_archive_dir",
    "register_sample_row",
    "register_test_method",
    "restore_project_snapshot",
    "run_cli",
    "sample_source_dir",
    "write_env_record",
]
