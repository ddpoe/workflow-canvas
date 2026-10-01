<!-- generated from pm_mvp::docs.consumer.reference.wfc-client-api @ b863cb41817d; do not edit -->

# Python API: wfc-client

## Overview

`wfc-client` is the package a Python method script uses to read its inputs and parameters and to record its outputs and metrics. Import it as `wfc`:

```python
import wfc_client as wfc
```

For a walkthrough of a complete method script, see [Authoring a Method Script](../tutorials/authoring-a-method-script.md).

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
