"""The one-method run: one registered method on one sample, recorded and cached.

``wfc run-method <module>.<method> --sample <sample>`` (and any other door)
builds Contracts' one-step document for the method and the sample, with the
params and the name given, checks it with the validator's verdict, saves it
in the project's pipelines folder (Layout's ``one_method_pipeline_path``,
or ``named_pipeline_path`` for a name) or at a given path, and runs that
file through :func:`~wfc.execution.pipeline.run_pipeline`, so the run is
recorded and cached as any pipeline's. Without a name, the file carries the
params' fingerprint (:func:`params_fingerprint`), so other params get
another file. A file already at the path is reused when it holds the same
document and refused otherwise; it is never overwritten. Every refusal
comes before anything is saved. There is no draft form: ``wfc try`` runs a
draft on a sample.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiom_annotations import AutoStep, Step, task, workflow

from .. import layout
from ..contracts import SampleInputError, one_step_document
from ..identity import canonical_json_hash
from ..persistence import require_project_root
from .composer import PipelineDocumentError
from .pipeline import run_pipeline
from .prepare import read_contract_map
from .validation import refuse_invalid


class OneMethodRunRefusedError(PipelineDocumentError):
    """The one-method run is refused before anything is saved or launched."""


@dataclass(frozen=True)
class SavedOneMethodPipeline:
    """The document a one-method run runs, and where it is saved.

    Attributes:
        path: The saved file.
        document: The one-step document.
        reused: True when the file already held this document.
    """

    path: Path
    document: dict[str, Any]
    reused: bool


def params_fingerprint(params: Mapping[str, Any] | None) -> str | None:
    """The short fingerprint a one-method run's file name carries for its params.

    The first 8 hex characters of Identity's ``canonical_json_hash`` of the
    params as given, so key order and spacing do not change it and other
    values do. A value equal to its declared default still counts: it gives
    another file, though the same cache hit.

    Args:
        params: The params given, or ``None``.

    Returns:
        The fingerprint, or ``None`` for no params (``None`` or ``{}``).
    """
    if not params:
        return None
    return canonical_json_hash(dict(params))[:8]


@task(purpose="Build the one-step document for a registered method on a sample "
              "with the params and the name given, check it with the "
              "validator's verdict, and save it in the project's pipelines "
              "folder (named, or carrying the params' fingerprint) or at a "
              "given path; a file already there is reused when it holds the "
              "same document and refused otherwise",
      inputs="'<module>.<method>', the sample, optional params, name and save "
             "path, the project root",
      outputs="SavedOneMethodPipeline: the path, the document, whether reused",
      critical="never overwrites a file; every refusal (an unusable name, an "
               "unregistered sample or method, several inputs the sample could "
               "feed, a param the verdict refuses, a different file at the "
               "path) comes before anything is written")
def save_one_method_pipeline(method: str, sample: str, *,
                             params: Mapping[str, Any] | None = None,
                             name: str | None = None,
                             save: Path | str | None = None,
                             root: Path | None = None) -> SavedOneMethodPipeline:
    """Build, check and save the document that runs *method* on *sample*.

    Args:
        method: ``<module>.<method>``.
        sample: A registered sample.
        params: The method's parameter values, written on the method node.
        name: The pipeline's name: the document's ``name``, and, without
            *save*, its file ``pipelines/<name>.json``. Without it the name
            is the default file's stem.
        save: Where to save it. Omitted: ``pipelines/<name>.json`` for a
            name, else ``pipelines/<module>.<method>-<sample>.json``, with
            ``-<fingerprint>`` before ``.json`` when params are given
            (:func:`params_fingerprint`). A relative path is the project
            root's.
        root: The project root; the resolved one when omitted, else that
            same root.

    Returns:
        The saved document and its path.

    Raises:
        OneMethodRunRefusedError: The method is not module-qualified or not
            registered, the name breaks Layout's pipeline-name rule, the
            sample is not registered, the sample could feed several inputs
            (named, saying to write a pipeline and check it with ``wfc
            check-pipeline``), or a different file is at the path.
        PipelineRefusedError: The validator's verdict refuses the document
            (a param undeclared, of the wrong type, outside its constraints,
            or required and missing), every refusal named.
        ProjectMismatchError: ``root`` is not the process's project root
            (named, with the process's); nothing is written.
    """
    from ..registration import list_samples

    root = require_project_root(root)
    module, dot, method_name = method.partition(".")
    if not (dot and module and method_name):
        raise OneMethodRunRefusedError(
            f"name the method with its module (module.method), not {method!r}")
    if name is not None and (refusal := layout.pipeline_name_refusal(name)):
        raise OneMethodRunRefusedError(f"{refusal}. Pick another --name.")

    口 = Step(step_num=1, name="Refuse an unregistered sample",
             purpose="Before anything is written; the registered samples are "
                     "Registration's listing")
    if sample not in {listed.name for listed in list_samples(root)}:
        raise OneMethodRunRefusedError(
            f"sample '{sample}' is not registered; `wfc list-samples` lists the "
            f"registered samples.")

    口 = Step(step_num=2, name="Build the one-step document",
             purpose="Contracts' one-step document over the contract map: the "
                     "method node with its params, the sample's selector, the "
                     "link naming the one input it feeds, and the name")
    fingerprint = params_fingerprint(params)
    default_path = layout.one_method_pipeline_path(root, module, method_name, sample,
                                                   fingerprint)
    try:
        document = one_step_document(method, sample, read_contract_map(),
                                     params=params, name=name or default_path.stem)
    except SampleInputError as exc:
        raise OneMethodRunRefusedError(
            f"{exc} Write a pipeline that connects them, check it with `wfc "
            f"check-pipeline`, and run it with `wfc run-pipeline --pipeline`.") from exc
    except ValueError as exc:
        raise OneMethodRunRefusedError(str(exc)) from exc

    口 = AutoStep(step_num=3, name="Refuse what the verdict refuses")
    refuse_invalid(document)

    口 = Step(step_num=4, name="Save it, or reuse the same file",
             purpose="A file at the path holding the same document is reused; "
                     "any other file there is refused naming the path, never "
                     "overwritten")
    if save is not None:
        path = Path(save) if Path(save).is_absolute() else root / save
    elif name is not None:
        path = layout.named_pipeline_path(root, name)
    else:
        path = default_path
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if existing != document:
            elsewhere = ("save this run elsewhere with --save" if save is not None
                         else "give this run another --name")
            raise OneMethodRunRefusedError(
                f"{path} already exists and holds a different document; wfc "
                f"never overwrites it. Run it with `wfc run-pipeline --pipeline "
                f"{path}`, or {elsewhere}.")
        return SavedOneMethodPipeline(path=path, document=document, reused=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return SavedOneMethodPipeline(path=path, document=document, reused=False)


@workflow(purpose="Run one registered method on one sample: save its one-step "
                  "document (or reuse the same file), then run that file as any "
                  "pipeline, recorded and cached",
          inputs="'<module>.<method>', the sample, optional params, name and "
                 "save path, the project root and the launch switches",
          outputs="The saved document; the run's rows, written by run_pipeline",
          critical="no draft form; a refusal saves and launches nothing")
def run_one_method(method: str, sample: str, *,
                   params: Mapping[str, Any] | None = None,
                   name: str | None = None,
                   save: Path | str | None = None,
                   root: Path | None = None, cores: int = 4,
                   snakefile_path: str | None = None, archive: bool = True,
                   keep_going: bool = False,
                   on_saved: Callable[[SavedOneMethodPipeline], None] | None = None,
                   ) -> SavedOneMethodPipeline:
    """Save the one-step document for *method* on *sample*, then run it.

    Args:
        method: ``<module>.<method>``.
        sample: A registered sample.
        params: The method's parameter values (see
            :func:`save_one_method_pipeline`).
        name: The pipeline's name (see :func:`save_one_method_pipeline`).
        save: Where to save the document (see
            :func:`save_one_method_pipeline`).
        root: The project root; the resolved one when omitted.
        cores: The engine's cores.
        snakefile_path: Where to write the generated Snakefile.
        archive: Archive the outputs after the run (run_pipeline's switch).
        keep_going: Pass ``--keep-going`` to the engine.
        on_saved: Called with the saved document before the run starts.

    Returns:
        The saved document and its path.

    Raises:
        OneMethodRunRefusedError: The run is refused before anything is saved.
        PipelineRefusedError: The validator's verdict refuses the document
            (a param), before anything is saved.
        ProjectMismatchError: ``root`` is not the process's project root
            (named, with the process's); nothing is saved or run.
        PipelineDocumentError: The preparation or the load refuses the saved
            document (run_pipeline's own refusal).
        RuntimeError: The engine exited non-zero.
    """
    root = require_project_root(root)

    口 = AutoStep(step_num=1, name="Save the document")
    saved = save_one_method_pipeline(method, sample, params=params, name=name,
                                     save=save, root=root)
    if on_saved is not None:
        on_saved(saved)

    口 = AutoStep(step_num=2, name="Run it as any pipeline")
    run_pipeline(str(saved.path), project_root=str(root), cores=cores,
                 snakefile_path=snakefile_path, archive=archive,
                 keep_going=keep_going)
    return saved
