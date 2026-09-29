"""
Auto-generated Snakefile (wildcard-based, unified mode)
Steps: 2 methods
Samples: ['s1', 's2']
Variants: ['default']
Estimated total runs: 4
"""

import sys, os, logging

SAMPLES = ['s1', 's2']
VARIANT_NAMES = ['default']
PIPELINE_ID = "pin-selector-beside-method-and-reference"
PIPELINE_JSON = r"<PIPELINE_JSON>"

PROJECT_ROOT = r"<PROJECT_ROOT>"
os.environ["WFC_PROJECT_ROOT"] = PROJECT_ROOT
workdir: PROJECT_ROOT

PARAMS = {
    'segment': {
        'default': {},
    },
    'quantify': {
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
        expand(".runs/sentinels/pin-selector-beside-method-and-reference/quantify/{sample}/{variant}/.complete", sample=SAMPLES, variant=VARIANT_NAMES)

SAMPLE_HASHES = {'s1': 'cccccccccccccccccccccccccccccccc', 's2': 'dddddddddddddddddddddddddddddddd'}

rule restore_sample:
    output: "data/samples/{sample}/.sample_ready"
    params:
        hash_arg=lambda wildcards: ("--hash " + SAMPLE_HASHES[wildcards.sample]) if SAMPLE_HASHES.get(wildcards.sample) else ""
    shell:
        "{sys.executable} -m wfc restore-sample --name {wildcards.sample} {params.hash_arg}"

rule segment:
    input: "data/samples/{sample}/.sample_ready"
    output: ".runs/sentinels/pin-selector-beside-method-and-reference/segment/{sample}/{variant}/.complete"
    params:
        variant="{variant}",
        node_id="segment"
    shell:
        "{sys.executable} -m wfc run-step --node-id {params.node_id} --sample {wildcards.sample} --variant {params.variant}"

rule quantify:
    input:
        data_0=".runs/sentinels/pin-selector-beside-method-and-reference/segment/{sample}/{variant}/.complete",
        sample_ready="data/samples/{sample}/.sample_ready",
        ref0_model=".runs/run-m/model/model.pkl"
    output: ".runs/sentinels/pin-selector-beside-method-and-reference/quantify/{sample}/{variant}/.complete"
    params:
        variant="{variant}",
        node_id="quantify"
    shell:
        "{sys.executable} -m wfc run-step --node-id {params.node_id} --sample {wildcards.sample} --variant {params.variant} --ref-input model=.runs/run-m/model/model.pkl"

onsuccess:
    _pipeline_logger.info("Pipeline %s completed successfully", PIPELINE_ID)

onerror:
    try:
        shell(f"{sys.executable} -m wfc fail_pipeline --pipeline-id {PIPELINE_ID}")
    except Exception:
        _pipeline_logger.exception("fail_pipeline command failed")
    _pipeline_logger.error("Pipeline %s FAILED", PIPELINE_ID)
