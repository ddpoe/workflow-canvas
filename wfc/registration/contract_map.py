"""The contract map: every registered method's contract, read for the enrichment pass."""

from __future__ import annotations

from sqlmodel import select

from ..persistence import Module


def load_contract_map(session) -> dict[str, dict]:
    """Read every registered method's contract into the enrichment pass's map.

    The one database read behind pipeline enrichment: the Contracts unit's
    ``enrich_pipeline`` takes this map as an argument and derives each node's
    script path, slot filenames and slot types from it without a session.

    Args:
        session: An open database session.

    Returns:
        ``{"<module>.<method>": {"input_slots": ..., "output_slots": ...,
        "script_path": ..., "env": ...}}`` for every registered method;
        ``input_slots`` and ``output_slots`` are ``{}`` for a method
        registered without a contract. Enrichment reads the output half;
        the Graph unit's structural core and column cross-check read the
        input half.
    """
    contract_map: dict[str, dict] = {}
    modules_db = {m.name: m for m in session.exec(select(Module)).all()}
    for mod in modules_db.values():
        for meth in mod.methods:
            mc = meth.contract
            contract_map[f"{mod.name}.{meth.name}"] = {
                "input_slots": mc.input_slots if mc else {},
                "output_slots": mc.output_slots if mc else {},
                "script_path": meth.script_path,
                "env": meth.env,
            }
    return contract_map
