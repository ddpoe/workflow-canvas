"""
Auto-generated Snakefile (wildcard-based, unified mode)
Steps: 2 methods
Samples: ['s1', 's2', 's3']
Variants: ['default']
Estimated total runs: 6
"""

import sys, os, logging

SAMPLES = ['s1', 's2', 's3']
VARIANT_NAMES = ['default']
PIPELINE_ID = "pin-collapsed-fan-in"
PIPELINE_JSON = r"<PIPELINE_JSON>"

PROJECT_ROOT = r"<PROJECT_ROOT>"
os.environ["WFC_PROJECT_ROOT"] = PROJECT_ROOT
workdir: PROJECT_ROOT

PARAMS = {
    'merge': {
        'default': {},
    },
    'filter': {
        'default': {},
    },
}

# Pipeline logger — writes to pipeline.log + stderr
PIPELINE_LOG_DIR = os.environ.get(
    "WFC_PIPELINE_LOG_DIR",
    os.path.join(".runs", "pipelines", PIPELINE_ID),
)
os.makedirs(PIPELINE_LOG_DIR, exist_ok=True)
os.makedirs(os.path.join(PIPELINE_LOG_DIR, "runs"), exist_ok=True)

_pipeline_logger = logging.getLogger(f"wfc.pipeline.{PIPELINE_ID}")
_pipeline_logger.setLevel(logging.DEBUG)
_pipeline_log_path = os.path.join(PIPELINE_LOG_DIR, "pipeline.log")
_file_handler = logging.FileHandler(_pipeline_log_path, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_stream_handler = logging.StreamHandler(sys.stderr)
_stream_handler.setLevel(logging.INFO)
_log_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_file_handler.setFormatter(_log_fmt)
_stream_handler.setFormatter(_log_fmt)
_pipeline_logger.addHandler(_file_handler)
_pipeline_logger.addHandler(_stream_handler)

# Run outcomes are tracked via sidecar JSONs (written by run-step)

# Set env vars for run-step (no brace patterns in shell strings)
os.environ["WFC_PIPELINE_JSON"] = os.path.abspath(PIPELINE_JSON) if PIPELINE_JSON else ""
os.environ["WFC_PIPELINE_ID"] = PIPELINE_ID
# PYTHONPATH is deliberately NOT set here.  The shell rule invokes
# the run-step verb (``{sys.executable} -m wfc``) against the host
# venv Python, which already has wfc in its own site-packages.
# run-step dispatches the method into its env's built container
# image, where the method script runs under the env's own
# interpreter with no wfc inside; only WFC_* variables are
# forwarded into the container, never PYTHONPATH.

rule all:
    input:
        expand(".runs/sentinels/pin-collapsed-fan-in/filter/__all__/{variant}/.complete", variant=VARIANT_NAMES)

SAMPLE_HASHES = {'s1': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 's2': 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'}

rule restore_sample:
    output: "data/samples/{sample}/.sample_ready"
    params:
        hash_arg=lambda wildcards: ("--hash " + SAMPLE_HASHES[wildcards.sample]) if SAMPLE_HASHES.get(wildcards.sample) else ""
    shell:
        "{sys.executable} -m wfc restore-sample --name {wildcards.sample} {params.hash_arg}"

rule merge:
    input:
        sources_0="data/samples/s1/.sample_ready",
        sources_1="data/samples/s2/.sample_ready",
        sources_2="data/samples/s3/.sample_ready"
    output: ".runs/sentinels/pin-collapsed-fan-in/merge/__all__/{variant}/.complete"
    params:
        variant="{variant}",
        node_id="merge"
    shell:
        "{sys.executable} -m wfc run-step --node-id {params.node_id} --sample __all__ --variant {params.variant} --collapsed-sample s1 --collapsed-sample s2 --collapsed-sample s3"

rule filter:
    input: ".runs/sentinels/pin-collapsed-fan-in/merge/__all__/{variant}/.complete"
    output: ".runs/sentinels/pin-collapsed-fan-in/filter/__all__/{variant}/.complete"
    params:
        variant="{variant}",
        node_id="filter"
    shell:
        "{sys.executable} -m wfc run-step --node-id {params.node_id} --sample __all__ --variant {params.variant}"

onsuccess:
    _pipeline_logger.info("Pipeline %s completed successfully", PIPELINE_ID)

onerror:
    try:
        shell(f"{sys.executable} -m wfc fail_pipeline --pipeline-id {PIPELINE_ID}")
    except Exception:
        _pipeline_logger.exception("fail_pipeline command failed")
    _pipeline_logger.error("Pipeline %s FAILED", PIPELINE_ID)
