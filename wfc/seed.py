"""Retired ``wfc seed`` — ``wfc demo`` is the way to populate a project.

``wfc demo`` populates an initialised project through the genuine
registration path. This module keeps only a pointer so a caller of the
``seed`` entry point gets a clear redirect.
"""

import sys

SEED_RETIRED_MESSAGE = (
    "wfc seed has been replaced by wfc demo. Run: wfc demo\n"
    "(seed inserted demo rows that bypassed env registration and contracts, "
    "so the seeded project could never run.)"
)


def seed() -> int:
    """Print the retirement pointer and return a non-zero exit code.

    Returns:
        1, always — the command inserts nothing.
    """
    print(SEED_RETIRED_MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(seed())
