"""Entry point: python -m wfc <command>"""

import sys
from .cli import cli_main


def main():
    sys.exit(cli_main())


if __name__ == "__main__":
    main()
