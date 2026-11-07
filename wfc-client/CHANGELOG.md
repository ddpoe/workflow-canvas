# Changelog

Notable changes to wfc-client. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Releases before 0.3.1 are described in the Workflow Canvas changelog.

## [0.3.1b1]

### Added

- `wfc_client.__version__`: the package's version, as a string (`"0.3.1b1"`).
- `wfc_client.InputPath`: the class of what `ctx.input("<input>")` returns for an input that takes one file. It is a `pathlib.Path` that also answers `[0]` and iterates once, so you can name it in type hints and `isinstance` checks. The old private name `wfc_client.context._SinglePath` still works and refers to the same class.
