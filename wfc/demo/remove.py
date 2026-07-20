"""Teardown logic for ``wfc demo --remove`` (US-3).

Deletes ONLY by the ``__demo__`` tag / module cascade — never by matching
method names against whatever is present. Because the reserved-prefix guard
means no user-driven path can create a ``__demo__`` name, the tag is proof
of demo ownership. Deletion runs in dependency-safe order (SQLite FK
enforcement is OFF here — nothing at the storage layer catches mistakes):

    run children (RunInput / RunOutput / RunAnnotation)
    -> runs -> null surviving runs' FKs into the deleted set
    -> MethodContract / TrackedFunction (+ParamDef) / MethodVersion
    -> methods -> ModuleContract -> module -> samples -> env -> files.

The DVC cache and the output archive are NEVER touched — cached bytes are
governed by ``wfc cache prune`` (ADR-018).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .scaffold import (
    DEMO_ENV,
    DEMO_IMAGE_TAG,
    DEMO_METHODS,
    DEMO_MODULE,
    DemoError,
    _project_env,
)


def remove_demo(
    target_dir: str | Path | None = None,
    purge_image: bool = False,
    assume_yes: bool = False,
) -> int:
    """Remove every demo-owned entity, run, and file from the project.

    Idempotent and tolerant of partial scaffolds: whatever is present is
    removed, whatever is absent is skipped without error.

    Args:
        target_dir: Project directory (default: cwd).
        purge_image: Also delete the ``local/wfc-demo-env`` Docker image.
        assume_yes: Skip the confirmation prompt.

    Returns:
        0 on success or nothing-to-do; 1 when the user declines the
        confirmation.

    Raises:
        DemoError: If *target_dir* is not an initialised project.
    """
    from sqlmodel import select

    target = Path(target_dir or Path.cwd()).resolve()
    marker = target / ".wfc" / "wf-canvas.toml"
    db_path = target / ".wfc" / "wfc.db"
    if not marker.exists() or not db_path.exists():
        raise DemoError(
            f"{target} is not a Workflow Canvas project — nothing to remove"
        )

    with _project_env(target):
        from ..database import get_session
        from ..envs import load_manifest
        from ..models import (
            Method,
            MethodContract,
            MethodVersion,
            Module,
            ModuleContract,
            ParamDef,
            Run,
            RunAnnotation,
            RunInput,
            RunOutput,
            Sample,
            TrackedFunction,
        )

        # ---- Compute the demo-owned set from the DB (tag / cascade only) ----
        with get_session() as session:
            module = session.exec(
                select(Module).where(Module.name == DEMO_MODULE)
            ).first()
            module_id = module.id if module is not None else None
            methods = (
                session.exec(
                    select(Method).where(Method.module_id == module_id)
                ).all()
                if module_id is not None
                else []
            )
            method_ids = [m.id for m in methods]
            method_names = [m.name for m in methods]
            runs = (
                session.exec(
                    select(Run).where(Run.method_id.in_(method_ids))  # type: ignore[attr-defined]
                ).all()
                if method_ids
                else []
            )
            run_ids = [r.id for r in runs]
            # autoescape: `_` is a single-char LIKE wildcard, so an
            # unescaped LIKE '__demo__%' would also select (and delete!)
            # user samples like 'mydemo__x'.
            samples = session.exec(
                select(Sample).where(
                    Sample.name.startswith(DEMO_MODULE, autoescape=True)  # type: ignore[attr-defined]
                )
            ).all()
            sample_names = [s.name for s in samples]

        try:
            manifest = load_manifest(target)
        except Exception as exc:
            print(
                f"WARNING: could not read the env manifest ({exc}) — env "
                f"teardown will be skipped.",
                file=sys.stderr,
            )
            manifest = {}
        env_present = DEMO_ENV in manifest.get("envs", {})

        # Files: method dirs come from the DB rows when available, else the
        # shipped set (partial-scaffold residue) — but a dir is only deleted
        # when NO surviving non-demo method claims it (checked below).
        candidate_method_dirs = sorted(set(method_names) | set(DEMO_METHODS))
        sample_dirs = sorted(
            p for p in (target / "data" / "samples").glob(f"{DEMO_MODULE}*")
            if p.is_dir()
        ) if (target / "data" / "samples").exists() else []
        pipeline_file = target / "demo-pipeline.json"
        build_dir = target / ".wfc" / "build" / DEMO_ENV

        file_targets: list[Path] = []
        for m in candidate_method_dirs:
            d = target / "methods" / m
            if d.exists():
                file_targets.append(d)
        file_targets.extend(sample_dirs)
        if pipeline_file.exists():
            file_targets.append(pipeline_file)
        if build_dir.exists():
            file_targets.append(build_dir)

        nothing_in_db = module_id is None and not samples and not env_present
        if nothing_in_db and not file_targets:
            print("No demo state found in this project — nothing to remove.")
            return 0

        # ---- Print the removal list ----
        print("wfc demo --remove will delete:")
        if module_id is not None:
            print(f"  module {DEMO_MODULE} with {len(methods)} method(s): "
                  + ", ".join(method_names))
        print(f"  {len(run_ids)} demo run(s) and their input/output/annotation rows")
        for s in sample_names:
            print(f"  sample {s}")
        if env_present:
            print(f"  env {DEMO_ENV}")
        for f in file_targets:
            print(f"  {f}")
        if purge_image:
            print(f"  Docker image {DEMO_IMAGE_TAG}")

        # ---- Shared-env warning (D-5): non-demo methods on __demo__env ----
        if env_present:
            from ..cli import _methods_referencing_env

            refs = [
                r for r in _methods_referencing_env(DEMO_ENV)
                if not r.startswith(f"{DEMO_MODULE}/")
            ]
            if refs:
                print(
                    f"WARNING: non-demo method(s) reference env {DEMO_ENV} and "
                    f"will no longer resolve it: " + ", ".join(refs),
                    file=sys.stderr,
                )

        if not assume_yes:
            answer = input("Proceed? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted — nothing removed.")
                return 1

        # ---- Delete DB rows in dependency-safe order ----
        with get_session() as session:
            if run_ids:
                for model in (RunInput, RunOutput):
                    for row in session.exec(
                        select(model).where(model.run_id.in_(run_ids))  # type: ignore[attr-defined]
                    ).all():
                        session.delete(row)
                for row in session.exec(
                    select(RunAnnotation).where(
                        RunAnnotation.run_id.in_(run_ids)  # type: ignore[attr-defined]
                    )
                ).all():
                    session.delete(row)
                for r in session.exec(
                    select(Run).where(Run.id.in_(run_ids))  # type: ignore[attr-defined]
                ).all():
                    session.delete(r)
                session.commit()

                # Null surviving runs' FKs into the deleted set. Cache keys
                # are method-scoped so this SHOULD be a closed set, but a
                # user who byte-copies a demo method can cache-hit a demo
                # run — nulling is cheap and makes teardown robust.
                for survivor in session.exec(
                    select(Run).where(
                        Run.cache_source_run_id.in_(run_ids)  # type: ignore[attr-defined]
                    )
                ).all():
                    survivor.cache_source_run_id = None
                    session.add(survivor)
                for survivor in session.exec(
                    select(Run).where(
                        Run.cancelled_due_to_run_id.in_(run_ids)  # type: ignore[attr-defined]
                    )
                ).all():
                    survivor.cancelled_due_to_run_id = None
                    session.add(survivor)
                # Same for surviving runs' input rows that recorded a demo
                # run as their source (e.g. a user pipeline that consumed a
                # demo method's output).
                for ri in session.exec(
                    select(RunInput).where(
                        RunInput.source_run_id.in_(run_ids)  # type: ignore[attr-defined]
                    )
                ).all():
                    ri.source_run_id = None
                    session.add(ri)
                session.commit()

            if method_ids:
                for model in (MethodContract, MethodVersion):
                    for row in session.exec(
                        select(model).where(
                            model.method_id.in_(method_ids)  # type: ignore[attr-defined]
                        )
                    ).all():
                        session.delete(row)
                tfs = session.exec(
                    select(TrackedFunction).where(
                        TrackedFunction.method_id.in_(method_ids)  # type: ignore[attr-defined]
                    )
                ).all()
                tf_ids = [tf.id for tf in tfs]
                if tf_ids:
                    for pd in session.exec(
                        select(ParamDef).where(
                            ParamDef.tracked_function_id.in_(tf_ids)  # type: ignore[attr-defined]
                        )
                    ).all():
                        session.delete(pd)
                for tf in tfs:
                    session.delete(tf)
                for m in session.exec(
                    select(Method).where(Method.id.in_(method_ids))  # type: ignore[attr-defined]
                ).all():
                    session.delete(m)
                session.commit()

            if module_id is not None:
                for mc in session.exec(
                    select(ModuleContract).where(
                        ModuleContract.module_id == module_id
                    )
                ).all():
                    session.delete(mc)
                mod = session.get(Module, module_id)
                if mod is not None:
                    session.delete(mod)
                session.commit()

            for s in session.exec(
                select(Sample).where(
                    Sample.name.startswith(DEMO_MODULE, autoescape=True)  # type: ignore[attr-defined]
                )
            ).all():
                session.delete(s)
            session.commit()

            # Surviving-method claims on the shared methods/<name>/ dirs:
            # a user method with the same name (e.g. my-analysis/preprocess)
            # keeps its snapshot directory.
            surviving_names = {
                m.name
                for m in session.exec(
                    select(Method).where(
                        Method.name.in_(candidate_method_dirs)  # type: ignore[attr-defined]
                    )
                ).all()
            }

        # ---- Env ----
        if env_present:
            from ..envs import delete as delete_env

            try:
                delete_env(DEMO_ENV, target)
            except KeyError:
                pass

        # ---- Files ----
        removed_files: list[Path] = []
        for f in file_targets:
            name = f.name
            if f.parent == target / "methods" and name in surviving_names:
                print(
                    f"  keeping {f} — a surviving method named '{name}' "
                    f"still uses it"
                )
                continue
            if f.is_dir():
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink(missing_ok=True)
            removed_files.append(f)

        # ---- Optional image purge ----
        if purge_image:
            try:
                res = subprocess.run(
                    ["docker", "rmi", DEMO_IMAGE_TAG],
                    capture_output=True, text=True, timeout=120,
                )
            except subprocess.TimeoutExpired:
                print(
                    f"WARNING: `docker rmi {DEMO_IMAGE_TAG}` did not finish "
                    f"within 120s — remove the image manually.",
                    file=sys.stderr,
                )
            else:
                if res.returncode != 0:
                    print(
                        f"WARNING: could not remove image {DEMO_IMAGE_TAG}: "
                        f"{res.stderr.strip()}",
                        file=sys.stderr,
                    )

    print(
        f"\nDemo removed: {len(run_ids)} run(s), "
        f"{len(method_names)} method(s), {len(sample_names)} sample(s), "
        f"{len(removed_files)} file path(s)."
        "\nCached output bytes remain in the DVC cache — reclaim them with "
        "`wfc cache prune`."
    )
    return 0
