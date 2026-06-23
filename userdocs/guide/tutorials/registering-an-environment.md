<!-- generated from pm_mvp::docs.consumer.tutorials.registering-an-environment @ 6d2b0c7454b5; do not edit -->

# Registering an Environment

## Outline — scaffold stub

_Phase-4 scaffold stub (2026-06-17 tutorial-restructure audit). Headings below are the planned structure; prose authored in a follow-up cycle. Focus topic 2: registering environments._

## Overview

_Stub._ Why environments exist (reproducibility + isolation) and the end-to-end flow: declare an env, register it, reference it from a method.

## Choosing a backend (pixi / conda / inherit / byo)

_Stub._ `wfc register-env <name> --backend pixi|conda|inherit|byo`: what each backend does, when to use it, `--force` to overwrite. **To absorb:** ADR-019; discovery adrs-8; writing-methods/environment-isolation; cli-reference/container-env-commands.

## The ephemeral-container model

_Stub._ Each step runs in a FRESH `docker run --rm` container; in-session `pip install`s do NOT persist; wfc + scripts are bind-mounted (not baked), so editing a script is free and you only rebuild when deps change. **To absorb:** ADR-019.

## Requesting GPUs

_Stub._ `gpus: true` in method.yaml → `--gpus all`. **To absorb:** ADR-019; writing-methods.

## Working inside an environment (jupyter / shell / exec)

_Stub._ `wfc jupyter` / `wfc shell` / `wfc exec` launch ephemeral containers for interactive dev. **To absorb:** cli-reference/dev-loop-commands.
