"""Allow the package to be run with ``python -m scione``."""

from scione.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
