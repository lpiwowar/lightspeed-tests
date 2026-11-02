"""RHOS Lightspeed test suite and shared utilities."""

import sys
from pathlib import Path

import pytest


def main() -> int:
    """Run selected tests or the bundled suite, returning pytest's exit status."""
    return pytest.main(
        [
            "-c",
            str(Path(__file__).with_name("pytest.ini")),
            # Apply testpaths from any working directory and keep the cache there.
            "--rootdir",
            str(Path.cwd()),
            "--pyargs",
            *sys.argv[1:],
        ]
    )
