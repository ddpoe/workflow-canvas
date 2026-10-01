# Contributing to Workflow Canvas

Thanks for your interest in Workflow Canvas. Bug reports, questions, documentation
fixes and code changes are all welcome.

By taking part in this project you agree to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting bugs and asking questions

Open an issue at <https://github.com/ddpoe/workflow-canvas/issues>. For a bug,
include:

- the `wfc` version (`pip show workflow-canvas`) and your operating system;
- the command you ran and its full output;
- the output of `wfc doctor`, run from the project directory.

## Repository layout

| Path | Contents |
|---|---|
| `wfc/` | The `workflow-canvas` package: the `wfc` CLI, execution engine and the Canvas server. |
| `wfc/canvas/static/` | The Canvas web front end (Svelte + TypeScript). |
| `wfc-client/` | The separate `wfc-client` package that method scripts import (`import wfc_client as wfc`). |
| `tests/` | The Python test suite. |
| `userdocs/` | The documentation site (Sphinx). |

## Setting up a development environment

You need Python 3.12, [Poetry](https://python-poetry.org/), git and Docker.
Node.js (20 or later) is needed only if you work on the Canvas front end.

```bash
git clone https://github.com/ddpoe/workflow-canvas.git
cd workflow-canvas
poetry install --with dev,docs
poetry run wfc --help
```

`poetry install` installs `workflow-canvas` in editable mode together with the
test, lint and docs tools. To work on `wfc-client` against your local checkout,
install it into the same environment:

```bash
poetry run pip install -e ./wfc-client
```

### The Canvas front end

The Canvas's built front end is not stored in git. Build it before running
`wfc canvas` from a source checkout:

```bash
cd wfc/canvas/static
npm ci
npm run build
```

## Running the tests

```bash
poetry run pytest
```

The default run skips tests marked `slow` (real subprocesses) and `integration`
(these need a running Docker daemon). To run them:

```bash
poetry run pytest -m slow
poetry run pytest -m integration
```

The `wfc-client` package has its own tests:

```bash
poetry run pytest wfc-client/tests
```

Front-end unit tests use Vitest, and the end-to-end tests use Playwright:

```bash
cd wfc/canvas/static
npm test
npx playwright install   # first time only
npm run test:e2e
```

## Linting and type checking

```bash
poetry run ruff check        # add --fix to apply the automatic fixes
poetry run mypy
```

Docstrings follow the [Google style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)
(`Args:`, `Returns:`, `Raises:`), and ruff checks them. Public functions and
classes need a docstring.

## Building the documentation

```bash
poetry run sphinx-build -c userdocs/_sphinx -b html userdocs/guide userdocs/_build/html
```

Open `userdocs/_build/html/index.html` in a browser. The pages under
`userdocs/guide/` are generated from the maintainer's documentation sources, so a
direct edit to one of them is overwritten on the next release. To propose a
documentation change, open an issue or a pull request that edits the page; the
maintainer carries the change over to the source.

## Submitting a change

1. For anything beyond a small fix, open an issue first so we can agree on the
   approach.
2. Create a branch, make the change, and add or update tests for the behavior you
   changed.
3. Make sure `poetry run pytest`, `poetry run ruff check` and `poetry run mypy`
   pass.
4. Open a pull request that describes what changed and why, and links the issue.

## License

By contributing, you agree that your contributions are licensed under the
project's [BSD 3-Clause license](LICENSE).
