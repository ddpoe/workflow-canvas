"""Retired ``wfc seed`` — replaced by ``wfc demo``.

The old ``seed()`` hand-inserted DB rows that bypassed the real registration
path (no env, no contracts, no code snapshot), producing a project that could
only fail on Run. ``wfc demo`` populates an initialised project through the
genuine registration path instead. This module keeps only a pointer so any
caller of the old entry point gets a clear redirect.
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
        1, always — the command no longer inserts anything.
    """
    print(SEED_RETIRED_MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(seed())
