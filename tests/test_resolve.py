"""ADR-018 Task 4: resolve_input / resolve_sample three-state resolution.

Tier 2: Subsystem tests covering the CACHE / REMOTE-PULL / FAIL contract.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from wfc.database import get_session
from wfc.models import Method, Module, Run, RunOutput, Sample
from wfc.provenance import _cache_path


def _ensure_method(session, module_name="mod", method_name="meth"):
    mod = Module(name=module_name)
    session.add(mod)
    session.commit()
    session.refresh(mod)
    m = Method(
        module_id=mod.id,
        name=method_name,
        env="container:demo",
    )
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


# =============================================================================
# resolve_sample
# =============================================================================

class TestResolveSample:
    """resolve_sample returns CACHE / REMOTE-PULL / FAIL outcomes (ADR-018)."""

    def test_cache_hit_returns_cache_path(self, tmp_project):
        from wfc.cli import resolve_sample

        content = b"alpha sample"
        md5 = hashlib.md5(content).hexdigest()

        # Pre-populate cache so the CACHE branch fires.
        cache_path = _cache_path(tmp_project, md5)
        cache_path.parent.mkdir(parents=True)
        cache_path.write_bytes(content)

        with get_session() as session:
            session.add(Sample(
                name="alpha",
                source_path="/tmp/alpha.txt",
                registered_path=str(tmp_project / "data" / "samples" / "alpha" / "alpha.txt"),
                file_type="txt",
                registration_mode="copy",
                content_hash=md5,
            ))
            session.commit()

        resolved = resolve_sample("alpha")
        assert resolved == str(cache_path)
        assert Path(resolved).read_bytes() == content

    def test_remote_pull_populates_then_returns_cache_path(self, tmp_project, monkeypatch):
        from wfc.cli import resolve_sample
        from wfc import provenance as _prov

        content = b"beta sample"
        md5 = hashlib.md5(content).hexdigest()
        cache_path = _cache_path(tmp_project, md5)

        with get_session() as session:
            session.add(Sample(
                name="beta",
                source_path="/tmp/beta.txt",
                registered_path=str(tmp_project / "data" / "samples" / "beta" / "beta.txt"),
                file_type="txt",
                registration_mode="copy",
                content_hash=md5,
            ))
            session.commit()

        def fake_pull(hashes, project_dir):
            # Simulate remote-pull populating the local cache.
            for h in hashes:
                p = _cache_path(Path(project_dir), h)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(content)
            return True

        monkeypatch.setattr(_prov, "pull_cache", fake_pull)

        # Cache is empty before resolution -- forces REMOTE-PULL path.
        assert not cache_path.exists()
        resolved = resolve_sample("beta")
        assert resolved == str(cache_path)
        assert Path(resolved).read_bytes() == content

    def test_fail_when_no_cache_and_no_remote(self, tmp_project, monkeypatch):
        from wfc.cli import resolve_sample
        from wfc import provenance as _prov

        md5 = hashlib.md5(b"missing").hexdigest()
        with get_session() as session:
            session.add(Sample(
                name="gamma",
                source_path="/tmp/gamma.txt",
                registered_path=str(tmp_project / "data" / "samples" / "gamma" / "gamma.txt"),
                file_type="txt",
                registration_mode="copy",
                content_hash=md5,
            ))
            session.commit()

        # pull_cache no-ops: remote configured but doesn't have the hash.
        monkeypatch.setattr(_prov, "pull_cache", lambda hashes, project_dir: False)

        assert resolve_sample("gamma") is None

    def test_unknown_sample_returns_none(self, tmp_project):
        from wfc.cli import resolve_sample
        assert resolve_sample("does-not-exist") is None


# =============================================================================
# resolve_input
# =============================================================================

class TestResolveInputThreeState:
    """resolve_input collapses to CACHE / REMOTE-PULL / FAIL (ADR-018)."""

    def _seed_run(self, project_dir, content_hash):
        with get_session() as session:
            method = _ensure_method(session)
            run = Run(
                method_id=method.id,
                params={},
                sample="s1",
                status="completed",
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            ro = RunOutput(
                run_id=run.id,
                output_name="out.txt",
                artifact_path=str(project_dir / ".runs" / str(run.id) / "out.txt"),
                artifact_type="method_file",
                content_hash=content_hash,
            )
            session.add(ro)
            session.commit()
            return run.id

    def test_cache_hit_returns_cache_path(self, tmp_project):
        from wfc.cli import resolve_input

        content = b"run output"
        md5 = hashlib.md5(content).hexdigest()
        cache_path = _cache_path(tmp_project, md5)
        cache_path.parent.mkdir(parents=True)
        cache_path.write_bytes(content)

        run_id = self._seed_run(tmp_project, md5)
        resolved = resolve_input(run_id)
        assert resolved == str(cache_path)

    def test_remote_pull_succeeds(self, tmp_project, monkeypatch):
        from wfc.cli import resolve_input
        from wfc import provenance as _prov

        content = b"pulled bytes"
        md5 = hashlib.md5(content).hexdigest()
        cache_path = _cache_path(tmp_project, md5)
        run_id = self._seed_run(tmp_project, md5)

        def fake_pull(hashes, project_dir):
            for h in hashes:
                p = _cache_path(Path(project_dir), h)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(content)

        monkeypatch.setattr(_prov, "pull_cache", fake_pull)
        assert not cache_path.exists()
        assert resolve_input(run_id) == str(cache_path)
        assert cache_path.read_bytes() == content

    def test_fail_returns_none(self, tmp_project, monkeypatch):
        from wfc.cli import resolve_input
        from wfc import provenance as _prov

        md5 = hashlib.md5(b"never-cached").hexdigest()
        run_id = self._seed_run(tmp_project, md5)

        monkeypatch.setattr(_prov, "pull_cache", lambda hashes, project_dir: False)
        assert resolve_input(run_id) is None

    def test_audit_row_resolves_via_source_run(self, tmp_project):
        """A cache-hit audit row (no RunOutput rows of its own) resolves to
        the source run's cached output — the wiring a re-executing step
        downstream of a cache hit depends on."""
        from wfc.cli import resolve_input

        content = b"cached source output"
        md5 = hashlib.md5(content).hexdigest()
        cache_path = _cache_path(tmp_project, md5)
        cache_path.parent.mkdir(parents=True)
        cache_path.write_bytes(content)

        source_run_id = self._seed_run(tmp_project, md5)

        # Audit row exactly as pre_run's cache-hit branch inserts it:
        # completed, cache_source_run_id set, no RunOutput rows.
        with get_session() as session:
            source = session.get(Run, source_run_id)
            audit = Run(
                method_id=source.method_id,
                params={},
                sample="s1",
                status="completed",
                cache_source_run_id=source_run_id,
            )
            session.add(audit)
            session.commit()
            session.refresh(audit)
            audit_id = audit.id

        assert resolve_input(audit_id) == str(cache_path)


# =============================================================================
# resolve_output — audit-row hop
# =============================================================================

class TestResolveOutputAuditHop:
    """resolve_output dereferences cache-hit audit rows to their source run."""

    def _seed_source_and_audit(self, project_dir):
        """Source run with one archived output (cache populated) plus an
        audit row exactly as pre_run's cache-hit branch inserts it:
        completed, cache_source_run_id set, no RunOutput rows."""
        content = b"cached source output"
        md5 = hashlib.md5(content).hexdigest()
        cache_path = _cache_path(project_dir, md5)
        cache_path.parent.mkdir(parents=True)
        cache_path.write_bytes(content)

        with get_session() as session:
            method = _ensure_method(session)
            source = Run(
                method_id=method.id,
                params={},
                sample="s1",
                status="completed",
            )
            session.add(source)
            session.commit()
            session.refresh(source)
            session.add(RunOutput(
                run_id=source.id,
                output_name="out.txt",
                artifact_path=str(
                    project_dir / ".runs" / str(source.id) / "out.txt"
                ),
                artifact_type="method_file",
                content_hash=md5,
            ))
            audit = Run(
                method_id=method.id,
                params={},
                sample="s1",
                status="completed",
                cache_source_run_id=source.id,
            )
            session.add(audit)
            session.commit()
            session.refresh(audit)
            return source.id, audit.id, cache_path

    def test_audit_row_resolves_source_output(self, tmp_project):
        from wfc.cli import resolve_output

        source_id, audit_id, cache_path = self._seed_source_and_audit(tmp_project)

        path, ro = resolve_output(audit_id, "out.txt")
        assert path == cache_path
        assert ro.run_id == source_id

    def test_audit_row_discovery_lists_source_names(self, tmp_project):
        from wfc.cli import UnknownOutputError, resolve_output

        _, audit_id, _ = self._seed_source_and_audit(tmp_project)

        with pytest.raises(UnknownOutputError) as excinfo:
            resolve_output(audit_id, None)
        assert excinfo.value.available == ["out.txt"]
