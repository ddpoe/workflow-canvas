"""What a terminal sees of ``wfc``: its help, and the bytes it writes.

Covers:
  - Tier 1: ``wfc --help`` lists only the user's commands, under the name
    ``wfc``; the hidden verbs a generated Snakefile calls are in neither the
    list nor the usage line (case 14).
  - Tier 1: each command's ``--help`` opens with what the list says about
    it, and every help text is ASCII (case 14).
  - Tier 1: ``register-sample --help`` says a sample name is registered once
    and that a corrected file goes under a new name (case 14).
  - Tier 1: run as a real process whose output encoding is cp1252, ``wfc``
    writes UTF-8 (no undecodable byte) and a line on stdout arrives before
    the error on stderr it belongs to (case 15).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

import pytest

from tests.fixtures.routes import run_cli

#: The verbs ``wfc --help`` leaves out: a generated Snakefile calls the
#: first four, and ``seed`` is retired (it points at ``wfc demo``).
HIDDEN = {"run-step", "restore-sample", "pipeline-summary", "fail_pipeline", "seed"}


def _verbs(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    (sub,) = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    return sub


def _listed() -> list[tuple[str, str]]:
    """Every listed command path with its list line (cache's sub-verbs too)."""
    from wfc.cli import build_parser

    rows = []
    sub = _verbs(build_parser())
    for choice in sub._choices_actions:
        if choice.help is argparse.SUPPRESS:
            continue
        rows.append((choice.dest, choice.help))
        if choice.dest == "cache":
            for cache_choice in _verbs(sub.choices["cache"])._choices_actions:
                rows.append((f"cache {cache_choice.dest}", cache_choice.help))
    return rows


def _words(text: str) -> str:
    return " ".join(text.split())


def test_wfc_help_lists_only_the_users_commands_under_wfc():
    from wfc.cli import build_parser

    result = run_cli("--help")

    assert result.returncode == 0
    out = result.stdout
    assert out.startswith("usage: wfc ")
    assert "==SUPPRESS==" not in out
    usage, _, body = out.partition("\n\n")
    every = set(_verbs(build_parser()).choices)
    listed = {line.split()[0] for line in body.splitlines()
              if line.startswith("    ") and not line.startswith("     ")}
    assert listed == every - HIDDEN
    for hidden in HIDDEN:
        assert hidden not in _words(usage).replace("{", ",").replace("}", ",").split(","), hidden


@pytest.mark.parametrize("command,line", _listed(), ids=[c for c, _ in _listed()])
def test_each_commands_help_opens_with_its_list_line_in_ascii(command, line):
    result = run_cli(*command.split(), "--help")

    assert result.returncode == 0
    assert result.stdout.startswith(f"usage: wfc {command}")
    assert _words(line) in _words(result.stdout)
    assert result.stdout.isascii(), [c for c in result.stdout if not c.isascii()]


def test_register_sample_help_says_a_name_is_registered_once_and_how_to_correct_a_file():
    """A user with a wrong file reads, in ``register-sample --help``, that the
    name cannot be re-registered and what to do instead."""
    result = run_cli("register-sample", "--help")

    assert result.returncode == 0
    text = _words(result.stdout)
    assert "a name is registered once" in text
    assert ("A sample name is registered once. To use a corrected file, register "
            "it under a new name and point the Input Selector at that name.") in text


def test_top_level_and_hidden_help_are_ascii():
    for argv in (["--help"], *[[verb, "--help"] for verb in sorted(HIDDEN)]):
        result = run_cli(*argv)
        assert result.returncode == 0, argv
        assert result.stdout.isascii(), argv


def test_a_cp1252_pipe_gets_utf8_and_the_context_line_before_its_error(tmp_path):
    """``wfc init`` in a repository with no wfc and no terminal prints the
    changes it would make (naming an archive path with non-ASCII letters) on
    stdout, then refuses on stderr; both streams share one pipe."""
    repo = tmp_path / "theirs"
    repo.mkdir()
    (repo / "analysis.py").write_text("print('hi')\n")
    for git in (["init", "-q"], ["add", "."],
                ["-c", "user.email=u@u", "-c", "user.name=u", "commit", "-q", "-m", "mine"]):
        subprocess.run(["git", *git], cwd=repo, check=True, capture_output=True)
    archive = tmp_path / "archivé—x"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8",)}
    env["PYTHONIOENCODING"] = "cp1252"

    proc = subprocess.run(
        [sys.executable, "-m", "wfc", "init", "--dir", str(repo),
         "--archive", str(archive)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=repo, env=env, timeout=120,
    )

    assert proc.returncode != 0
    text = proc.stdout.decode("utf-8")  # strict: no undecodable byte
    assert "archivé—x" in text
    assert text.index("wfc init would make these changes:") < text.index("--yes")
    assert not (repo / ".wfc").exists()
