"""The composer: a pipeline document to a ``PipelineDef``, with its reads done.

The one place on the execution path that fetches what the Graph unit's pure
load takes as values: the referenced runs' outputs and sample (Storage's
``resolve_run_reference_outputs``), the contract map (Registration's
``load_contract_map``) and, once the load has derived the sample list, the
samples' content hashes (Registration's ``load_sample_hashes``) for the
emitter's restore rule. The reads share one session, opened after a
connection probe: an unreachable database is one typed failure before the
launch (``DatabaseUnreachableError``), and a failing query reports itself.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from sqlalchemy.engine import make_url
from sqlalchemy.exc import InterfaceError, OperationalError

from ..graph import PipelineDef, load_pipeline, reference_nodes
from ..persistence import database_url, get_engine, get_session
from ..registration import load_contract_map, load_sample_hashes
from ..storage import resolve_run_reference_outputs


class DatabaseUnreachableError(RuntimeError):
    """The project database could not be reached; the pipeline was not launched.

    Raised by the composer's connection probe before any of its reads, and
    so before ``prepare_launch``: nothing is frozen and no worker is started.
    The message names the database with its password masked (a
    ``DATABASE_URL`` can carry one). The cause is chained, never quoted,
    since a driver's message can carry the full DSN.
    """

    def __init__(self, url: str):
        """Build the message for ``url`` with its password masked.

        Args:
            url: The database URL the engine was, or would have been,
                built on.
        """
        self.masked_url = make_url(url).render_as_string(hide_password=True)
        super().__init__(
            f"The project database is unreachable ({self.masked_url}); "
            "the pipeline was not launched."
        )


class LoadedPipeline(NamedTuple):
    """What the composer returns: the pipeline and its samples' content hashes.

    Attributes:
        pipeline: The ``PipelineDef`` the Graph load derives.
        sample_hashes: Sample name to content hash, for the pipeline's
            samples that are registered with a hash; the rest are absent.
    """

    pipeline: PipelineDef
    sample_hashes: dict[str, str]


@contextmanager
def _composer_session():
    """One session for the composer's reads, opened after a connection probe.

    A session is lazy, so the probe connects once first: a database that
    cannot be reached raises ``DatabaseUnreachableError`` here, before any
    read. Inside the session a query that fails propagates as itself.

    Yields:
        An open session on the process engine.

    Raises:
        DatabaseUnreachableError: When the engine cannot be built or the
            database cannot be connected to.
    """
    try:
        engine = get_engine()
        with engine.connect():
            pass
    except (InterfaceError, OperationalError) as exc:
        raise DatabaseUnreachableError(database_url()) from exc
    with get_session() as session:
        yield session


def load_pipeline_from_document(document: dict) -> LoadedPipeline:
    """Load a pipeline document through the Graph package, fetching for it.

    The one place on the execution path that fetches what the pure load
    takes as values: the referenced runs' outputs and sample
    (``wfc.storage.resolve_run_reference_outputs``), the contract map
    (``wfc.registration.load_contract_map``) and, once the load has derived
    the sample list, the samples' content hashes
    (``wfc.registration.load_sample_hashes``) for the emitter's restore
    rule. The reads share one session, opened after a connection probe, so
    an unreachable database is one failure before the launch and a failing
    query reports itself.

    Args:
        document: The pipeline document, already substituted.

    Returns:
        The ``PipelineDef`` the Graph load derives, with its sample hashes.

    Raises:
        DatabaseUnreachableError: When the database cannot be reached; no
            read has run.
    """
    with _composer_session() as session:
        refs = resolve_run_reference_outputs(reference_nodes(document), session)
        contract_map = load_contract_map(session)
        pipeline = load_pipeline(document, contract_map=contract_map,
                                 reference_outputs=refs)
        sample_hashes = load_sample_hashes(pipeline.samples, session)
    return LoadedPipeline(pipeline, sample_hashes)


def load_pipeline_from_path(path: Path) -> PipelineDef:
    """Read a pipeline document from ``path`` and load it.

    For the callers that need the pipeline only (the cancelled-row walk, the
    harness's expansion rung); the sample hashes are read and dropped.

    Args:
        path: Path to the JSON config file.

    Returns:
        The ``PipelineDef`` the Graph load derives.

    Raises:
        FileNotFoundError: If the config file does not exist.
        DatabaseUnreachableError: When the database cannot be reached.
    """
    return load_pipeline_from_document(json.loads(Path(path).read_text())).pipeline
