"""Contracts unit: the env-spec grammar at its two refusing entry points.

An env spec is the bare name of a registered env. Method registration
(``wfc.environments.check_method_env``) applies the env-name rule alone, so the legacy
``container:<name>`` spelling and both direct-ref forms are refused
there like any other non-name. ``wfc register-env`` (``wfc.environments.register``)
applies the same rule before any Docker call or manifest write. The read side
(dispatch, claim, canvas, delete-env query) strips a legacy ``container:``
prefix once and then applies the same rule; the delete-env reference query is
the one read-side site with a cell here, because an equality match against a
composed prefixed form would miss bare rows there.

The accepting case — a registered bare name resolves and is stored verbatim —
is ``tests/test_registration.py::test_register_method_resolves_container_env``;
the unknown-name and floating-tag-entry refusals are
``::test_register_method_container_env_missing_from_manifest`` and
``::test_register_method_container_env_floating_tag_rejected``.
"""
from __future__ import annotations

import pytest
from axiom_annotations import workflow
from sqlmodel import select

from tests.fixtures.conftest import FIXTURE_ENV_NAME
from tests.fixtures.fakes import refuse_docker
from wfc.contracts import ENV_NAME_RULE, is_env_name
from wfc.persistence import get_session, Method, Module
from wfc.registration import register_method, register_module

DIGEST = "b" * 64
DIRECT_REF = f"docker://ghcr.io/dante/image-io@sha256:{DIGEST}"


def _write_method(root, name: str, env_value: str):
    """A registrable method directory whose method.yaml declares ``env: <env_value>``."""
    method_dir = root / "methods" / name
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / "method.yaml").write_text(
        f"env: {env_value}\n"
        "inputs:\n  data:\n    type: .csv\n"
        "outputs:\n  result:\n    type: .csv\n"
    )
    (method_dir / f"{name}.py").write_text(
        "import wfc_client as wfc\n\n"
        "@wfc.method\n"
        f"def {name}(ctx):\n"
        "    pass\n\n"
        "if __name__ == '__main__':\n"
        "    wfc.run()\n"
    )
    return method_dir


NON_NAMES = [
    pytest.param(f"container:{FIXTURE_ENV_NAME}", id="legacy-prefixed-name"),
    pytest.param(f"container:{DIRECT_REF}", id="prefixed-direct-ref"),
    pytest.param(DIRECT_REF, id="unprefixed-direct-ref"),
    pytest.param("my:env", id="colon-name"),
    pytest.param("local/image-io", id="slash-name"),
]


@pytest.mark.parametrize("env_value", NON_NAMES)
@workflow(purpose="Method registration refuses every env spec that is not a "
                  "bare env name — the legacy container: spelling, both "
                  "direct-ref forms, a colon-bearing and a slash-bearing "
                  "name — with one plain message: the value given, the form, "
                  "the rule Contracts owns, and the registered names; no "
                  "method row is stored",
          inputs="A method.yaml declaring the non-name env spec, in a project "
                 "whose manifest holds the fixture env",
          outputs="ValueError carrying value, form, rule and options; no row")
def test_registration_refuses_every_non_name_env_spec_with_one_message(
    tmp_project, env_value
):
    from wfc.init import init_project

    init_project(tmp_project)
    register_module(name="grammar_mod", contracts=[], description="env-spec grammar")
    method_dir = _write_method(tmp_project, "grammar_method", env_value)

    with pytest.raises(ValueError) as excinfo:
        register_method(method_dir=method_dir, module_name="grammar_mod")
    message = str(excinfo.value)

    assert env_value in message, message
    assert "`env: <name>`" in message, message
    assert ENV_NAME_RULE in message, message
    assert f"Registered envs: {FIXTURE_ENV_NAME}" in message, message
    # Plain: no history, no apology, no hint at a direct-ref form.
    rest = message.replace(env_value, "")
    assert "ADR" not in rest and "docker://" not in rest and "direct" not in rest, message

    with get_session() as session:
        stored = session.exec(select(Method).where(Method.name == "grammar_method")).first()
    assert stored is None


@workflow(purpose="A manifest that is present but cannot be read is reported "
                  "as unreadable, never as 'no envs registered': the refusal "
                  "carries the value given, that .wfc/envs.json could not be "
                  "read, and the load error's own text",
          inputs="A method.yaml declaring the legacy prefixed spelling, in a "
                 "project whose .wfc/envs.json is not valid JSON",
          outputs="ValueError naming the value and the unreadable manifest; "
                  "no register-env instruction")
def test_registration_refusal_reports_an_unreadable_manifest(tmp_project):
    from wfc.init import init_project

    init_project(tmp_project)
    (tmp_project / ".wfc" / "envs.json").write_text("{not json", encoding="utf-8")
    register_module(name="grammar_mod", contracts=[], description="env-spec grammar")
    env_value = f"container:{FIXTURE_ENV_NAME}"
    method_dir = _write_method(tmp_project, "grammar_method", env_value)

    with pytest.raises(ValueError) as excinfo:
        register_method(method_dir=method_dir, module_name="grammar_mod")
    message = str(excinfo.value)

    assert env_value in message, message
    assert ".wfc/envs.json could not be read" in message, message
    assert "not valid JSON" in message, message
    assert "register-env" not in message, message


@workflow(purpose="The delete-env reference query lists a method stored under "
                  "a bare env name and one stored under the legacy prefixed "
                  "spelling alike, and nothing bound to another env — the "
                  "query reads stored rows through the read-side grammar "
                  "instead of composing the prefixed form",
          inputs="Three stored method rows: bare name, legacy prefixed name, "
                 "another env",
          outputs="module/method references for the first two only")
def test_delete_env_reference_query_counts_bare_and_legacy_rows(tmp_project):
    from wfc.registration import methods_referencing_env

    with get_session() as session:
        mod = Module(name="query_mod", description="delete-env reference query")
        session.add(mod)
        session.commit()
        session.refresh(mod)
        for name, env in (
            ("bare_row", "image-io"),
            ("legacy_row", "container:image-io"),
            ("other_row", "other-env"),
        ):
            session.add(Method(name=name, module_id=mod.id, env=env,
                               script_path=f"methods/{name}/{name}.py"))
        session.commit()

    assert sorted(methods_referencing_env("image-io")) == [
        "query_mod/bare_row", "query_mod/legacy_row",
    ]


@pytest.mark.parametrize("bad_name", ["my:env", "local/image-io"], ids=["colon", "slash"])
@workflow(purpose="wfc register-env refuses a name outside the env-name rule "
                  "before any Docker call and before any manifest write, "
                  "stating the rule; a reserved-namespace name that fits the "
                  "rule passes it (the reserved guard is a separate check)",
          inputs="A byo registration under a colon- or slash-bearing name, "
                 "with every docker_runner entry point armed to fail",
          outputs="ValueError naming the rule; no docker call; no envs.json")
def test_register_env_refuses_an_invalid_name_before_docker_or_manifest(
    tmp_path, monkeypatch, bad_name
):

    from wfc.environments import register

    (tmp_path / ".wfc").mkdir()

    refuse_docker(monkeypatch, "docker was called for an invalid env name")

    with pytest.raises(ValueError) as excinfo:
        register(name=bad_name, backend="byo",
                 source={"image": "docker://ghcr.io/dante/x:latest"},
                 project_dir=tmp_path)
    assert bad_name in str(excinfo.value)
    assert ENV_NAME_RULE in str(excinfo.value)
    assert not (tmp_path / ".wfc" / "envs.json").exists()

    assert is_env_name("__demo__env")


def test_registration_refuses_an_unregistered_name_pointing_at_register_env(
    tmp_project,
):
    """A bare name with no manifest entry is refused, telling the user to build it.

    The project has registered no env, so the refusal's remedy is the
    ``wfc register-env`` command rather than a list of names.
    """
    from wfc import layout
    from wfc.init import init_project

    init_project(tmp_project)
    (tmp_project / layout.STATE_DIR_NAME / layout.ENV_MANIFEST_FILENAME).unlink(
        missing_ok=True)
    register_module(name="grammar_mod", contracts=[], description="env-spec grammar")
    method_dir = _write_method(tmp_project, "grammar_method", "unbuilt-env")

    with pytest.raises(ValueError) as excinfo:
        register_method(method_dir=method_dir, module_name="grammar_mod")
    message = str(excinfo.value)

    assert "'unbuilt-env' is not found in .wfc/envs.json" in message, message
    assert "`wfc register-env <name>`" in message, message
    with get_session() as session:
        stored = session.exec(select(Method).where(Method.name == "grammar_method")).first()
    assert stored is None
