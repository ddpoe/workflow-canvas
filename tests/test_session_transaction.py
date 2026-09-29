"""The session's transaction rule: what a ``with get_session()`` block keeps.

Every production block commits explicitly. A write the block has not
committed when it leaves — because it raised, or simply ended — is discarded
when the session closes. This pins the rule the Persistence move must not
change: the move changes where a session comes from, never what a block does
with it.

Requirement: ``docs/system/persistence.json``, catalog case
``transaction-discard``.
"""
from __future__ import annotations

import pytest
from axiom_annotations import workflow
from sqlmodel import select

from wfc.persistence import get_session, reset_engine, Module


@workflow(purpose="A session block that commits one row, writes a second and "
                  "then raises keeps exactly the committed row",
          inputs="A create_all database bound by the override",
          outputs="The rows a fresh session reads")
def test_uncommitted_write_does_not_survive_its_block(tmp_path, monkeypatch):
    """Persistence catalog ``transaction-discard``."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'wfc.db'}")
    reset_engine()
    try:
        with pytest.raises(RuntimeError, match="block failed"):
            with get_session() as session:
                session.add(Module(name="committed"))
                session.commit()
                session.add(Module(name="uncommitted"))
                session.flush()  # sent to the database, inside the open transaction
                raise RuntimeError("block failed")

        with get_session() as fresh:
            names = fresh.exec(select(Module.name)).all()
    finally:
        reset_engine()

    assert names == ["committed"]
