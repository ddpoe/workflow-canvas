<!-- generated from pm_mvp::docs/consumer/reference/cli-registration @ b7efd089a0aa; do not edit -->

# Registration Commands

## Registration Commands

The examples use the suggested layout: your code under `src/<module>/<method>/`, with each module's `module.yaml` in `src/<module>/`, and commands run from the project root.

### wfc register-module

Create a module, or update an existing one, with its output contracts. Contracts come from `--contracts` when given; otherwise from `module.yaml` in `--module-dir`. Without `--module-dir`, wfc looks only in `modules/<name>/`, so pass `--module-dir` for a module under `src/`. A `module.yaml` inside the repository is committed to git.

```bash
wfc register-module --name my_analysis --module-dir src/my_analysis
```

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: register-module
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc register-method

`wfc register-method <method-dir> --module <module>`, where `<method-dir>` is the method's directory and `<module>` is the registered module it joins.

```bash
wfc register-method src/my_analysis/filter_data --module my_analysis
```

Register a method from its directory: find the script, scan a Python script for its parameters, read `method.yaml`, check it against the module's contracts, save a registered copy of the method, and commit that copy to git. Your source directory is not committed. Other files in the source directory are not copied: the command prints a "Not registered" list naming them, with the advice to register data files as samples and wire them as inputs. Before it writes anything, the command prints whether the method is new (registers it as v1), changed since its newest version (adds the next version, naming what changed) or the same as it (no version is added). When a version would be added it asks `Proceed? [y/N]`; any answer but `y` prints `Nothing was registered.` and exits with an error. `--yes` skips the question, and with no terminal and no `--yes` a new version is refused. Earlier versions stay registered and runnable. After you edit a method's script or `method.yaml`, run `wfc register-method` again. A method name can be used in more than one module.

The script is the one named by `--script`, else by the `script:` key in `method.yaml`, else the single `<name>.py`, `.R`, `.r` or `.sh` file in the directory. If the script cannot be found or the check fails, it prints an `ERROR:` line and registers nothing.

The check prints `Env: <name> (validated)` when:

- `method.yaml` names the env by its name, not by an image reference;
- that env is registered (in `.wfc/envs.json`);
- its image is pinned to one exact build (by digest).

The image is not pulled at this point. A name that is not registered is refused, with the registered names listed.

If `method.yaml` declares a parameter that the script never reads by name (no `ctx.params["name"]` or `ctx.params.get("name")`), registration prints a `WARNING:` line saying a value set for it changes nothing. It is a warning only; the method still registers. A script that reads `ctx.params` dynamically is not checked.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: register-method
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc register-sample

Register a data file or directory as a sample. wfc copies the content into the project's cache (the source stays where it is) and backs it up to the archive when one is reachable. A sample name is registered once: registering the same name again with different content is refused. To use a corrected file, register it under a new name and point the Input Selector at the new name.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: register-sample
   :nodescription:
   :noepilog:
   :nodefault:
```

[Register a Module](../how-to/register-a-module.md), [Write a Method](../how-to/write-a-method.md) and [Register Samples](../how-to/register-samples.md) walk through them.

### wfc list-methods, wfc list-modules and wfc list-samples

List what is registered, the same lists the Registry tab shows. `wfc list-methods` prints each method with its newest version, version count, env and source. `wfc list-modules` prints each module with its method count, contracts, colour and description. `wfc list-samples` prints each sample with its type, size, registration time and source. `wfc list-envs` does the same for environments. Each takes `--json`.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: list-methods
   :nodescription:
   :noepilog:
   :nodefault:
```
