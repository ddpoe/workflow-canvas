"""AST validation of ctx.save_artifact() at register-method (Tier-1).

Tier 2 subsystem tests for wfc.registration.validate_save_artifacts: missing
required save errors, unknown literal name errors, dynamic name warns,
reachable `if` save is accepted, unreachable `if False:` save warns.
Body-only: only the decorated function's body is scanned, so saves made in
helper functions are not seen.
"""

import textwrap

import pytest

from axiom_annotations import workflow, Step

from wfc.registration import validate_save_artifacts


def _write(tmp_path, body: str):
    script = tmp_path / "m.py"
    script.write_text(textwrap.dedent(body))
    return script


@workflow(purpose="register-method AST scan validates ctx.save_artifact names against method.yaml")
def test_missing_required_save_errors(tmp_path):
    script = _write(tmp_path, """\
        import wfc_client as wfc

        @wfc.method
        def qc(ctx):
            out = ctx.workdir / "clean.csv"
            ctx.save_artifact("clean", out)
            # 'dropped' required output is never saved
    """)
    with pytest.raises(ValueError) as exc:
        validate_save_artifacts(script, declared_outputs=["clean", "dropped"],
                                required_outputs=["clean", "dropped"])
    assert "dropped" in str(exc.value)


def test_unknown_save_name_errors(tmp_path):
    script = _write(tmp_path, """\
        import wfc_client as wfc

        @wfc.method
        def qc(ctx):
            out = ctx.workdir / "x.csv"
            ctx.save_artifact("ghost", out)
    """)
    with pytest.raises(ValueError) as exc:
        validate_save_artifacts(script, declared_outputs=["clean"],
                                required_outputs=[])
    assert "ghost" in str(exc.value)


def test_dynamic_name_warns_not_errors(tmp_path):
    script = _write(tmp_path, """\
        import wfc_client as wfc

        @wfc.method
        def qc(ctx):
            name = "clean"
            ctx.save_artifact(name, ctx.workdir / "clean.csv")
    """)
    # 'clean' required, but saved dynamically -> warn, and since the literal
    # save is absent the required check would fire. Make it non-required so the
    # dynamic-name warning is the observable behavior.
    warnings = validate_save_artifacts(script, declared_outputs=["clean"],
                                       required_outputs=[])
    assert any("dynamic" in w for w in warnings)


def test_reachable_if_branch_save_accepted(tmp_path):
    script = _write(tmp_path, """\
        import wfc_client as wfc

        @wfc.method
        def qc(ctx):
            if ctx.params.get("emit"):
                ctx.save_artifact("clean", ctx.workdir / "clean.csv")
    """)
    # A save inside a runtime-conditional `if` satisfies the required output.
    warnings = validate_save_artifacts(script, declared_outputs=["clean"],
                                       required_outputs=["clean"])
    assert warnings == []


def test_unreachable_if_false_save_warns(tmp_path):
    script = _write(tmp_path, """\
        import wfc_client as wfc

        @wfc.method
        def qc(ctx):
            if False:
                ctx.save_artifact("clean", ctx.workdir / "clean.csv")
    """)
    warnings = validate_save_artifacts(script, declared_outputs=["clean"],
                                       required_outputs=[])
    assert any("unreachable" in w for w in warnings)


def test_all_required_saved_no_error(tmp_path):
    script = _write(tmp_path, """\
        import wfc_client as wfc

        @wfc.method
        def qc(ctx):
            ctx.save_artifact("clean", ctx.workdir / "clean.csv")
            ctx.save_artifact("dropped", ctx.workdir / "dropped.csv")
    """)
    warnings = validate_save_artifacts(script, declared_outputs=["clean", "dropped"],
                                       required_outputs=["clean", "dropped"])
    assert warnings == []


# --- declared params the script never reads (case 18) -----------------------------


def test_a_declared_param_the_script_never_reads_warns(tmp_path):
    """Catalog case 18: literal reads count, wherever in the file; the rest warn."""
    from wfc.registration import unread_param_warnings

    script = _write(tmp_path, """\
        import wfc_client as wfc

        def _scale(ctx):
            return ctx.params.get("scale", 1)

        @wfc.method
        def qc(ctx):
            suffix = ctx.params["suffix"]
            if "verbose" in ctx.params:
                print(_scale(ctx))
            ctx.save_artifact("clean", ctx.workdir / f"clean{suffix}.csv")
    """)
    warnings = unread_param_warnings(script, ["suffix", "scale", "verbose", "threshold"])
    assert len(warnings) == 1, warnings
    assert "'threshold'" in warnings[0] and "never reads" in warnings[0]


@pytest.mark.parametrize("access", [
    pytest.param('key = "threshold"\n    value = ctx.params[key]', id="variable key"),
    pytest.param("for name in ctx.params:\n        print(name)", id="iteration"),
    pytest.param("print(dict(ctx.params))", id="passed on"),
    pytest.param("print(ctx.params.items())", id="items"),
    pytest.param("p = ctx.params\n    print(p)", id="aliased"),
    pytest.param('import os\n    print(os.environ["WFC_PARAMS"])', id="raw WFC_PARAMS"),
])
def test_dynamic_params_access_skips_the_unread_param_warning(tmp_path, access):
    """Catalog case 18: any dynamic access to ctx.params skips the warning (d-9)."""
    from wfc.registration import unread_param_warnings

    script = tmp_path / "m.py"
    script.write_text("import wfc_client as wfc\n\n@wfc.method\ndef qc(ctx):\n"
                      f"    {access}\n", encoding="utf-8")
    assert unread_param_warnings(script, ["threshold", "suffix"]) == []
