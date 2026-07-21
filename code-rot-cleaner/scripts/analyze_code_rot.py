#!/usr/bin/env python3
"""Backward-compatible entry point for audit.py."""

import sys

sys.dont_write_bytecode = True

from audit import main


if __name__ == "__main__":
    raise SystemExit(main())
