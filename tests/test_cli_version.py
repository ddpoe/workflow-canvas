"""``wfc --version`` names the installed wfc.

Covers:
  - Tier 2: ``--version`` with no verb prints ``wfc <version>``, the
    version in the installed ``workflow-canvas`` distribution's metadata,
    and exits 0.
  - Tier 2: with the metadata unreadable, ``--version`` prints
    ``wfc unknown`` and exits 0.
  - Tier 2: the version is looked up only for ``--version``: another
    command run without the metadata logs no version warning.
"""

from __future__ import annotations

import importlib.metadata
import logging

import pytest
from axiom_annotations import workflow

from tests.fixtures.fakes import stub_distribution_metadata_missing


@workflow(
    purpose="`wfc --version`, given with no verb, prints `wfc <version>` "
            "from the installed distribution's metadata and exits 0.",
)
def test_version_flag_prints_the_installed_version_and_exits_zero(capsys):
    from wfc.cli import cli_main

    with pytest.raises(SystemExit) as exited:
        cli_main(["--version"])

    assert exited.value.code == 0
    installed = importlib.metadata.version("workflow-canvas")
    assert capsys.readouterr().out == f"wfc {installed}\n"


@workflow(
    purpose="With the distribution metadata missing, `wfc --version` prints "
            "`wfc unknown` and exits 0.",
)
def test_version_flag_without_metadata_prints_unknown(monkeypatch, capsys):
    from wfc.cli import cli_main

    stub_distribution_metadata_missing(monkeypatch)

    with pytest.raises(SystemExit) as exited:
        cli_main(["--version"])

    assert exited.value.code == 0
    assert capsys.readouterr().out == "wfc unknown\n"


@workflow(
    purpose="A command other than `--version`, run with the distribution "
            "metadata missing, logs no version warning: the version is "
            "looked up only when `--version` is given.",
)
def test_other_commands_without_metadata_log_no_version_warning(monkeypatch,
                                                                caplog):
    from wfc.cli import cli_main

    stub_distribution_metadata_missing(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="wfc.version"):
        with pytest.raises(SystemExit) as exited:
            cli_main(["--help"])

    assert exited.value.code == 0
    assert [r for r in caplog.records if r.name == "wfc.version"] == []
