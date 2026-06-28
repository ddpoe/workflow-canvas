"""Smoke test for the ``wfc seed`` command (``wfc.seed.seed``).

Regression guard: making ``Method.env`` a required env field broke the live
``seed`` path, which constructed ``Method(...)`` rows without an ``env`` kwarg
and committed them. Nothing in the suite exercised ``seed()``, so CI missed it.
The guard is the non-empty-env assertion below: ``seed()`` must give every
method a real env. (At the DB layer ``env`` carries a ``server_default=''`` for
legacy backfill, so an env-less insert no longer raises ``IntegrityError`` — it
silently stores ``''``; the non-empty assertion is what catches a regression.)
"""

from sqlmodel import Session, create_engine, select

from wfc.models import Module, Method
from wfc.seed import seed


def test_seed_inserts_demo_module_and_methods_with_env(tmp_path, monkeypatch):
    """``seed()`` succeeds and every seeded Method has a non-empty env."""
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    from wfc.database import reset_engine
    reset_engine()

    # Inserted bad (empty-env) rows before the seed.py fix; the non-empty
    # assertion below is the durable guard.
    seed()

    engine = create_engine(url)
    with Session(engine) as session:
        mod = session.exec(
            select(Module).where(Module.name == "demo_pipeline")
        ).first()
        assert mod is not None

        methods = session.exec(
            select(Method).where(Method.module_id == mod.id)
        ).all()
        names = {m.name for m in methods}
        assert names == {"preprocess", "filter_cells", "label", "aggregate"}
        for m in methods:
            assert m.env, f"method {m.name!r} has empty env"
    engine.dispose()
    reset_engine()
