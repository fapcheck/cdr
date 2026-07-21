#!/usr/bin/env python3
"""Backward-compatible entry point for report.py."""

import sys

sys.dont_write_bytecode = True

from report import main


if __name__ == "__main__":
    raise SystemExit(main())
