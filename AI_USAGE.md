# Use of generative AI

This package was developed with substantial help from generative AI coding
tools, primarily Claude and Claude Code. AI assistance was used for
implementation, refactoring, documentation, and writing tests, following a
structured plan-then-implement-then-review process rather than unsupervised
generation.

The author reviewed the code, owns the design decisions, and is responsible for
the package's behavior. Correctness is checked by an automated test suite
covering unit, integration, and end-to-end cases, which the author maintains.

To keep a codebase built this way understandable over time, the author also
developed [axiom-graph](https://github.com/ddpoe/axiom-graph), a tool that
indexes the code and flags when documentation drifts out of sync with the
implementation. It is used during development to keep the docs and code aligned
as the package changes.
