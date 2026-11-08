"""Ctrl-C at the interactive prompt: ``input()`` raising ``KeyboardInterrupt``.

``stub_interactive_prompt`` (``process.py``) answers every prompt with a
string; a user who presses Ctrl-C at ``Proceed? [y/N]`` gives no string at
all. :func:`stub_prompt_interrupt` stands that answer at the same edge, for
the verbs whose question treats Ctrl-C as a no (``wfc prune-envs``).
"""

from __future__ import annotations

from ._entry import fake


@fake(
    boundary="builtins.input -- Ctrl-C pressed at the interactive prompt "
             "(prune-envs' Proceed? question)",
    preserves="everything the verb decides when the question is interrupted: "
              "what it prints, its exit code, that nothing is changed",
    not_proven="that a real terminal's Ctrl-C reaches input() as "
               "KeyboardInterrupt",
    backed_by="undrivable: no terminal in the suite",
)
def stub_prompt_interrupt(monkeypatch, *, prompts: list[str] | None = None) -> None:
    """Make every ``input()`` raise ``KeyboardInterrupt``, as Ctrl-C at the prompt does.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        prompts: When given, receives each prompt's text.
    """
    def prompt(text=""):
        if prompts is not None:
            prompts.append(text)
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", prompt)
