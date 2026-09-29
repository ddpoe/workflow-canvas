"""Orchestration: the pipeline's Snakefile, and the engine that runs it.

The unit's public surface. ``snakemake`` is the emitter: a ``PipelineDef``
and the values Execution's composer read (the samples' content hashes) to
the text of a Snakefile, :func:`generate_snakefile`. The emitter reads
nothing and launches nothing. ``engine`` is the hand-off: emit, write,
launch, register the live process, wait, and report an
:class:`EngineOutcome` (:func:`invoke_engine`). What to do about a failure
is Execution's; the outcome carries the message it raises.

Execution imports from this package; nothing else does. The package
imports Graph, Layout, Contracts and the standard library only: no
database, no session, and never Execution.
"""

from .engine import EngineOutcome, invoke_engine
from .snakemake import generate_snakefile

#: The engine's own working state, written at the project root; never committed.
ENGINE_STATE_DIR_NAME = ".snakemake"

__all__ = [
    "ENGINE_STATE_DIR_NAME", "EngineOutcome", "generate_snakefile", "invoke_engine"]
