<!-- generated from pm_mvp::docs/consumer/reference/wfc-client-api @ c05b3ced80bd; do not edit -->

# wfc-client Python API

## Overview

`wfc-client` is a separate, optional helper library for Python method scripts: it reads a script's inputs and parameters and records its outputs and metrics. It is its own package, installed in the method's environment, and not part of wfc. A script does not need it: it can read the environment variables and write the files itself, as the [Method Script Contract](method-script-contract.md) describes. Import it as `wfc`:

```python
import wfc_client as wfc
```

For a walkthrough of a complete method script, see [Write a Method in Python](../tutorials/authoring-a-method-script.md).

## Entry Point

```{eval-rst}
.. autofunction:: wfc_client.method(func)

.. autofunction:: wfc_client.run
```

## Run Context

```{eval-rst}
.. autoclass:: wfc_client.RunContext
   :members: workdir, input, save_artifact, log_metric
```
