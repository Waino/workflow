"""Command line interface for workflow."""

import sys

import click


@click.command()
def main() -> int:
    """Run the workflow CLI."""
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
