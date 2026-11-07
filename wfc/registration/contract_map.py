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
    with one identity share a key, holding the newer label.

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
        reads the input half; the params normalization reads ``params``.

        A method with a draft also holds ``versions["draft"]``: the contract
        its draft's ``method.yaml`` holds now (read as the draft re-read
        reads it, so an edit needs no Update first), with ``label: None`` and
        the draft's ``folder``. A DRAFT method (no registered version) gets
        an entry of its own, ``draft_only: True``, whose top level is the
        draft's contract and whose ``version`` and ``label`` are ``None``. A
        draft whose ``method.yaml`` could not be read, or whose module is not
        registered, adds nothing.
    """
    contract_map: dict[str, dict] = {}
    modules = session.exec(select(Module)).all()
    names = [(mod.name, meth.name, meth.env) for mod in modules for meth in mod.methods]
    if not modules:
        # Nothing registered: no version list or draft to read, so no project
        # root is needed (a bare database answers an empty map).
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
    _add_drafts(contract_map, {mod.name for mod in modules}, root)
    return contract_map


def _add_drafts(contract_map: dict[str, dict], modules: set[str], root) -> None:
    """Add each draft's contract, its ``method.yaml`` read now, as ``versions["draft"]``."""
    from .draft import draft_contract_now, list_drafts

    for pointer in list_drafts(root):
        contract, _error = draft_contract_now(pointer)
        if contract is None or pointer.module not in modules:
            continue
        draft = {
            "label": None,
            "input_slots": contract["inputs"],
            "output_slots": contract["outputs"],
            "params": contract["params"],
            "env": contract["env"],
            "folder": str(pointer.folder),
        }
        key = f"{pointer.module}.{pointer.method}"
        if key in contract_map:
            contract_map[key]["versions"]["draft"] = draft
        else:
            contract_map[key] = {**draft, "version": None, "draft_only": True,
                                 "versions": {"draft": draft}}
