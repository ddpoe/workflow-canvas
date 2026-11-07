"""Persistence: the ``[previews]`` limits ``read_config`` returns.

Tier 2: ``[previews] memory_mb`` and ``cache_mb`` are read from a real
project's ``wf-canvas.toml`` through ``read_config``, the one reader every
caller (the canvas's preview and image routes among them) goes through.
Absent means the defaults, 1024 and 500; anything but a positive whole number
is refused naming the key, the value and the fix.
"""
from __future__ import annotations

import pytest
from axiom_annotations import workflow

from wfc import layout
from wfc.persistence import read_config


def _append_config(root, text: str) -> None:
    """Append a section to the project's wf-canvas.toml."""
    marker = layout.marker_path(root)
    marker.write_text(marker.read_text(encoding="utf-8") + "\n" + text, encoding="utf-8")


@workflow(purpose="A project with no [previews] section reads memory_mb 1024 and "
                  "cache_mb 500; one that sets both reads back exactly what it set, "
                  "and one that sets only one key keeps the other's default",
          inputs="Projects made by wfc init, with and without a [previews] section",
          outputs="The previews entry of read_config's result")
def test_preview_limits_default_and_obey_the_config(tmp_project):
    """Both defaults when absent; both keys obeyed when set; one key alone."""
    assert read_config(tmp_project)["previews"] == {"memory_mb": 1024, "cache_mb": 500}

    _append_config(tmp_project, "[previews]\nmemory_mb = 64\n")
    assert read_config(tmp_project)["previews"] == {"memory_mb": 64, "cache_mb": 500}

    marker = layout.marker_path(tmp_project)
    marker.write_text(
        marker.read_text(encoding="utf-8").replace(
            "memory_mb = 64\n", "memory_mb = 2048\ncache_mb = 3\n"),
        encoding="utf-8",
    )
    assert read_config(tmp_project)["previews"] == {"memory_mb": 2048, "cache_mb": 3}


@pytest.mark.parametrize("key", ["memory_mb", "cache_mb"])
@pytest.mark.parametrize(
    "literal",
    ["true", "false", "512.0", "0", "-5", '"512"', '"lots"'],
    ids=["true", "false", "float", "zero", "negative", "numeric-string", "word"],
)
@workflow(purpose="A [previews] limit that is not a positive whole number (a "
                  "boolean, a float, zero, a negative or a string) is refused "
                  "with a ValueError naming the key, the value and the fix",
          inputs="A project whose [previews] section sets one key to an invalid value",
          outputs="The ValueError read_config raises")
def test_an_invalid_preview_limit_is_refused_naming_the_fix(tmp_project, key, literal):
    """Every invalid form refused; the message names key, value and fix."""
    _append_config(tmp_project, f"[previews]\n{key} = {literal}\n")
    with pytest.raises(ValueError) as exc:
        read_config(tmp_project)
    message = str(exc.value)
    # The value as the user wrote it in TOML (true, "512"), not as Python shows it.
    assert f"[previews] {key} = {literal}" in message
    assert "positive whole number" in message


def test_a_previews_value_that_is_not_a_table_is_refused_naming_the_fix(tmp_project):
    marker = layout.marker_path(tmp_project)
    marker.write_text("previews = true\n" + marker.read_text(encoding="utf-8"),
                      encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        read_config(tmp_project)

    message = str(exc.value)
    assert "previews = true is not a table" in message
    assert "[previews]" in message and "memory_mb = 1024" in message
    assert "delete the line" in message
