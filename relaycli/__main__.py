"""Allow ``python -m relaycli`` to work like ``relaycli``."""

from relaycli.cli import app

if __name__ == "__main__":
    app()
