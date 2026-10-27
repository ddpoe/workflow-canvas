"""Tier 3 integration test: an env image pushed at registration is pulled back to run.

A throwaway ``registry:2`` runs on a free port (a loopback registry needs no
TLS). A git project sets ``[registry] url`` to ``127.0.0.1:<port>/<project>``
and registers a byo ``local/`` image with the CLI's ``register-env``, which
tags and pushes it before it commits and records the registry digest in
``pushed.json``. Every local copy of the image is then removed. ``wfc
run-pipeline`` pulls the image by ``<base>/<env>@<digest>``, checks that its
content renders the recorded env fingerprint, and the step runs in the
pulled image.

This is the backing witness of ``stub_docker_tag``, ``stub_docker_push`` and
``stub_docker_read_manifest``: the same test reads the live registry's answer
for the pushed ref and for a tag it does not hold.

``integration``-marked (deselected by default); skips cleanly without a
reachable Docker daemon. The registry container is always removed.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.conftest import requires_docker
from tests.fixtures.conftest import (
    init_test_project,
    register_sample_row,
    register_test_method,
    sample_source_dir,
)
from tests.integration.registry_fixture import image_absent as _absent
from tests.integration.registry_fixture import local_registry
from tests.integration.test_pinned_method_version_runs import (
    _METHOD_SOURCE,
    _METHOD_YAML,
    ENV,
    METHOD,
    MODULE,
    _build_image,
    _commit,
    _git,
    _run,
    _run_pipeline,
    _stamp_runs,
    _stamp_text,
    _wfc,
    _write_pipeline,
)

pytestmark = [pytest.mark.integration, requires_docker]

@workflow(purpose="With [registry] url set to a live registry:2, register-env "
                  "pushes a byo local/ image and records its registry digest; "
                  "with every local copy removed, run-pipeline pulls the image "
                  "by <base>/<env>@<digest>, its content renders the recorded "
                  "env fingerprint, and the step runs in it; the registry "
                  "answers found for the pushed ref and missing for a tag it "
                  "does not hold")
def test_a_pushed_env_image_is_pulled_by_digest_and_runs(
        tmp_path: Path, minimal_image: str, monkeypatch) -> None:
    """Register pushes, the local copies go, the run pulls by digest."""
    from wfc.environments import docker as docker_runner
    from wfc.environments.captures import pushed_record
    from wfc.environments.fingerprint import content_fingerprint
    from wfc.environments.manifest import env_record_for_spec
    from wfc.environments.registry import probe_registry, push_target, registry_ref
    from wfc.persistence.config import set_registry_url

    tag = f"local/wfc-test-registry-{uuid.uuid4().hex[:8]}:one"
    refs: list[str] = [tag]
    proj = tmp_path / "proj"
    proj.mkdir()
    with local_registry() as registry:
        try:
            口 = Step(step_num=1, name="Make a project whose registry is the live one",
                     purpose="git init, wfc init, then [registry] url = "
                             "127.0.0.1:<port>/<project>, committed")
            _git(proj, "init")
            _git(proj, "config", "user.email", "wfc@wfc")
            _git(proj, "config", "user.name", "wfc")
            _git(proj, "config", "core.autocrlf", "false")
            _git(proj, "config", "core.fileMode", "false")
            (proj / ".gitattributes").write_text("* -text\n", encoding="utf-8")
            monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))
            monkeypatch.setenv("DATABASE_URL", f"sqlite:///{proj / '.wfc' / 'wfc.db'}")
            init_test_project(proj)
            base = f"{registry}/wfcproj"
            set_registry_url(proj, base)
            _commit(proj, "registry: the live registry:2")
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)

            口 = Step(step_num=2, name="Register the env, which pushes it",
                     purpose="`wfc register-env --backend byo` of a local/ image "
                             "tags and pushes it to <base>/<env>:fp-<12 hex> and "
                             "records the registry's digest in pushed.json")
            image_id = _build_image(tmp_path / "image", tag, "registry")
            refs.append(image_id)
            registered = _wfc(proj, env, "register-env", ENV, "--backend", "byo",
                              "--image", f"docker://{tag}")
            assert registered.returncode == 0, (
                f"{registered.stdout}\n{registered.stderr}")
            record, _ = env_record_for_spec(ENV, proj)
            assert record is not None and record.env_fingerprint
            fingerprint = record.env_fingerprint
            pushed = pushed_record(ENV, fingerprint, proj)
            assert pushed is not None, registered.stdout
            ref = registry_ref(base, ENV, pushed.digest)
            refs += [push_target(base, ENV, fingerprint), ref]

            口 = Step(step_num=3, name="Read the registry's answers",
                     purpose="The pushed ref is found and a tag the registry "
                             "does not hold is missing; both prove the registry "
                             "answers, so the probe passes")
            found = docker_runner.read_manifest(ref)
            assert found.state == docker_runner.MANIFEST_FOUND, found.detail
            missing = docker_runner.read_manifest(f"{base}/{ENV}:fp-000000000000")
            assert missing.state == docker_runner.MANIFEST_MISSING, missing.detail
            assert probe_registry(base) == (True, "")

            口 = Step(step_num=4, name="Register the method and a sample",
                     purpose="One method whose newest version records the "
                             "pushed image; one sample; an unpinned pipeline")
            source = sample_source_dir(proj) / "_methods" / METHOD
            source.mkdir(parents=True)
            (source / "method.yaml").write_text(_METHOD_YAML, encoding="utf-8")
            (source / f"{METHOD}.py").write_text(_METHOD_SOURCE.format(mark="pulled"),
                                                 encoding="utf-8")
            register_test_method(project_dir=proj, module_name=MODULE,
                                 method_dir=source, method_name=METHOD)
            sample = sample_source_dir(proj) / "s1"
            sample.mkdir(parents=True)
            (sample / "trigger.txt").write_text("trigger", encoding="utf-8")
            register_sample_row(proj, "s1", sample / "trigger.txt")
            _write_pipeline(proj, None)

            口 = Step(step_num=5, name="Remove every local copy",
                     purpose="The local/ tag, the pushed tag and the image ID are "
                             "removed; the daemon holds no copy under any name")
            for name in (tag, push_target(base, ENV, fingerprint), image_id):
                _run(["docker", "rmi", "-f", name], tmp_path, timeout=120)
            assert all(_absent(name) for name in (tag, image_id, ref))

            口 = Step(step_num=6, name="Run the step",
                     purpose="Pre-flight pulls <base>/<env>@<digest> and checks "
                             "its content; the step runs in the pulled image")
            ran = _run_pipeline(proj, env)
            assert ran.returncode == 0, f"{ran.stdout}\n{ran.stderr}"
            assert f"pulling {ref}" in ran.stdout + ran.stderr
            (run,) = _stamp_runs()
            assert run.status == "completed", run.error_message
            assert _stamp_text(run.id, proj).startswith("pulled ")
            assert content_fingerprint(docker_runner.image_content(ref)) == fingerprint
        finally:
            for name in refs:
                _run(["docker", "rmi", "-f", name], tmp_path, timeout=120)
