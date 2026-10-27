"""The contract map: every registered method version's contract, for the enrichment pass."""

from __future__ import annotations

from sqlmodel import select

from ..persistence import Module
from ..persistence import project_root as get_project_root
from .registered_code import MalformedMethodRecordError, version_contracts


def _slots(contract, env_name: str, label: int) -> dict:
    return {
        "label": label,
        "input_slots": contract.input_slots if contract else {},
        "output_slots": contract.output_slots if contract else {},
        "params": contract.params_schema if contract else None,
        "env": env_name,
    }


def load_contract_map(session) -> dict[str, dict]:
    """Read every registered method version's contract into one map.

    The one database read behind pipeline enrichment: the Contracts unit's
    ``enrich_pipeline`` takes this map as an argument and derives each node's
    slot filenames, slot types and env from it without a session.

    Each method's entry carries one contract per version, keyed by the
    version's content identity (the pin a node carries), and names its
    newest version; the entry's top-level slots are the newest version's and
    its top-level env is the Method row's stored value (the spelling the
    registration recorded), which is what an unpinned node reads. Two labels
    with one
    identity share a key, holding the newer label.

    Args:
        session: An open database session.

    Returns:
        ``{"<module>.<method>": {"input_slots": ..., "output_slots": ...,
        "params": ..., "env": ..., "label": <newest label>, "version":
        <newest identity>, "versions": {<identity>: {"label",
        "input_slots", "output_slots", "params", "env"}}}}`` for every
        registered method (``params`` is the version's declared params, or
        ``None`` for a version with no contract); a method with no version
        list is malformed and left out, so a node naming it is refused.
        Enrichment reads the output half; the Graph unit's structural core
        and column cross-check read the input half; the params
        normalization reads ``params``.
    """
    contract_map: dict[str, dict] = {}
    names = [(mod.name, meth.name, meth.env)
             for mod in session.exec(select(Module)).all() for meth in mod.methods]
    if not names:
        # Nothing registered: no version list to read, so no project root
        # is needed (a bare database answers an empty map).
        return contract_map
    root = get_project_root()
    for module, method, row_env in names:
        try:
            pairs = version_contracts(module, method, root)
        except MalformedMethodRecordError:
            continue
        if not pairs:
            continue
        versions = {version.entry.identity: _slots(contract, version.entry.env_name,
                                                   version.entry.label)
                    for version, contract in pairs}
        newest, newest_contract = pairs[-1]
        contract_map[f"{module}.{method}"] = {
            **_slots(newest_contract, row_env, newest.entry.label),
            "version": newest.entry.identity,
            "versions": versions,
        }
    return contract_map
