"""Entry point for ``python -m wfc <command>``."""

import sys

from .cli import cli_main


def main():
    """Run the wfc command line and exit with its return code."""
    sys.exit(cli_main())


if __name__ == "__main__":
    main()
