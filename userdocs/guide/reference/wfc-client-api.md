<!-- generated from pm_mvp::docs/consumer/reference/wfc-client-api @ 64b8324da71a; do not edit -->

# wfc-client Python API

## Overview

`wfc-client` is the package a Python method script uses to read its inputs and parameters and to record its outputs and metrics. Import it as `wfc`:

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
